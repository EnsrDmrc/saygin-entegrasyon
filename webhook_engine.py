from fastapi import APIRouter, Depends, Request, Body, HTTPException
from sqlalchemy.orm import Session
import os
import requests
import models, schemas
from database import get_db

# Yönlendirici (Router) Tanımı
router = APIRouter(
    prefix="/webhooks",
    tags=["Sipariş ve Webhook İşlemleri"]
)

# --- 1. ÇİFT YÖNLÜ SENKRONİZASYON MOTORU (CANLI API ENTEGRASYONU) ---
def sync_stock_to_channels(db: Session, variant_id: int, new_stock_quantity: int, origin_channel_id: int = None):
    """
    Yeni stoğu tüm pazaryerlerinin gerçek API'lerine fırlatır.
    Siparişin geldiği orijinal kanalı (origin_channel_id) gereksiz API çağrısı yapmamak için es geçer.
    """
    listings = db.query(models.ChannelListing).filter(models.ChannelListing.variant_id == variant_id).all()
    variant = db.query(models.Variant).filter(models.Variant.id == variant_id).first()
    
    if not variant:
        return
        
    print(f"\n--- 🔄 CANLI STOK DAĞITIMI BAŞLADI (Varyant ID: {variant_id} | Yeni Stok: {new_stock_quantity}) ---")
    
    # Güvenlik Anahtarlarını Hazırla
    SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
    SHOPIFY_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")
    N11_KEY = os.getenv("N11_APP_KEY")
    N11_SECRET = os.getenv("N11_APP_SECRET")
    
    # Optimizasyon: Shopify lokasyon ID'sini her döngüde tekrar çekmemek için hafızada tut
    shopify_loc_id = None
    
    for listing in listings:
        if listing.channel_id == origin_channel_id:
            print(f"> [ATLANDI] Kanal ID: {listing.channel_id} (Sipariş zaten bu kanaldan geldi)")
            continue
            
        # N11 CANLI ENTEGRASYONU
        if listing.channel_id == 3: 
            print("> N11 sunucularına bağlanılıyor...")
            if N11_KEY and N11_SECRET and variant.sku:
                url = "https://api.n11.com/ms/product/tasks/price-stock-update"
                headers = {"appkey": N11_KEY, "appsecret": N11_SECRET, "Content-Type": "application/json"}
                payload = {
                    "payload": {
                        "integrator": "SayginGrupEntegrasyon", 
                        "skus": [{"stockCode": variant.sku.strip().upper(), "quantity": new_stock_quantity}]
                    }
                }
                try:
                    res = requests.post(url, json=payload, headers=headers)
                    if res.status_code == 200:
                        print(f"[BAŞARILI] N11 vitrini güncellendi! Yeni Stok: {new_stock_quantity}")
                    else:
                        print(f"[HATA] N11 Güncellenemedi: {res.text}")
                except Exception as e:
                    print(f"[HATA] N11 Bağlantı sorunu: {str(e)}")
            else:
                print("[UYARI] N11 API anahtarları veya ürün SKU'su eksik!")

        # SHOPIFY CANLI ENTEGRASYONU
        elif listing.channel_id == 4: 
            print("> Shopify sunucularına bağlanılıyor...")
            if SHOPIFY_TOKEN and SHOPIFY_URL:
                try:
                    # Lokasyon ID'yi sadece ilk ihtiyaç duyulduğunda çek
                    if not shopify_loc_id:
                        loc_res = requests.get(f"https://{SHOPIFY_URL}/admin/api/2026-07/locations.json", headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN})
                        if loc_res.status_code == 200:
                            shopify_loc_id = loc_res.json()["locations"][0]["id"]
                    
                    if shopify_loc_id:
                        inv_url = f"https://{SHOPIFY_URL}/admin/api/2026-07/inventory_levels/set.json"
                        inv_res = requests.post(inv_url, headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}, json={
                            "location_id": shopify_loc_id,
                            "inventory_item_id": listing.channel_product_id,
                            "available": new_stock_quantity
                        })
                        if inv_res.status_code == 200:
                            print(f"[BAŞARILI] Shopify vitrini güncellendi! Yeni Stok: {new_stock_quantity}")
                        else:
                            print(f"[HATA] Shopify Güncellenemedi: {inv_res.text}")
                except Exception as e:
                    print(f"[HATA] Shopify Bağlantı sorunu: {str(e)}")
            else:
                print("[UYARI] Shopify API anahtarları eksik!")
                
    print("--- 🔄 TÜM KANALLARLA SENKRONİZASYON TAMAMLANDI ---\n")


# --- 2. MERKEZİ SİPARİŞ İŞLEME MOTORU (GATEWAY) ---
def process_standardized_order(db: Session, merchant_id: int, channel_id: int, order_number: str, total_price: float, items: list):
    print(f"\n--- {merchant_id} ID'Lİ SATICI İÇİN {channel_id} NUMARALI KANALDAN SİPARİŞ İŞLENİYOR ({order_number}) ---")
    
    db_order = models.Order(
        merchant_id=merchant_id,
        channel_id=channel_id, 
        order_number=order_number,
        total_amount=total_price,
        status="approved"
    )
    db.add(db_order)
    db.flush() 
    
    for item in items:
        sku = str(item.get("sku"))
        quantity = int(item.get("quantity", 1)) 
        price = float(item.get("price", 0.0))
        
        listing = db.query(models.ChannelListing).join(models.Channel).filter(
            models.ChannelListing.channel_id == channel_id,
            models.ChannelListing.channel_product_id == sku,
            models.Channel.merchant_id == merchant_id
        ).first()
        
        if listing:
            variant = db.query(models.Variant).filter(models.Variant.id == listing.variant_id).first()
            
            if variant and variant.stock_quantity >= quantity:
                db_order_item = models.OrderItem(
                    order_id=db_order.id, variant_id=variant.id, quantity=quantity, unit_price=price
                )
                db.add(db_order_item)
                
                variant.stock_quantity -= quantity
                db.commit()
                
                print(f"[BAŞARILI] {sku} eşleşti! Stoktan {quantity} adet düşüldü. Kalan Stok: {variant.stock_quantity}")
                sync_stock_to_channels(db, variant.id, variant.stock_quantity, origin_channel_id=channel_id)
            else:
                print(f"[UYARI] {sku} için stok yetersiz veya ürün bulunamadı!")
        else:
            print(f"[ATLANDI] {sku} bu kanalda eşleştirilemedi.")
            
    db.commit()
    return {"status": "success", "message": f"Kanal {channel_id} siparişi merkezi motorda başarıyla işlendi."}

# --- DİNLEYİCİLER (WEBHOOKS) ---

@router.post("/shopify/{merchant_id}")
async def shopify_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    order_number = payload.get("name", "Bilinmiyor")
    total_price = float(payload.get("total_price", 0.0))
    line_items = payload.get("line_items", []) 
    
    print(f"\n--- {merchant_id} ID'Lİ SATICI İÇİN SHOPIFY SİPARİŞİ İŞLENİYOR ({order_number}) ---")
    
    db_order = models.Order(
        merchant_id=merchant_id, channel_id=4, order_number=order_number, total_amount=total_price, status="approved"
    )
    db.add(db_order)
    db.flush() 
    
    for item in line_items:
        # 1. Gelen paketten SKU'yu alıyoruz
        gelen_sku = item.get("sku")
        if not gelen_sku:
            gelen_sku = str(item.get("variant_id"))
        
        quantity = int(item.get("quantity", 1))
        unit_price = float(item.get("price", 0.0))
        
        # 2. DOĞRU ARAMA: Önce yerel veritabanımızdan doğrudan SKU ile ürünü buluyoruz
        variant = db.query(models.Variant).filter(models.Variant.sku == gelen_sku).first()
        
        if variant:
            # 3. Ürünü bulduktan sonra, bu ürünün Shopify (Kanal 4) bağlantısı var mı diye bakıyoruz
            listing = db.query(models.ChannelListing).filter(
                models.ChannelListing.variant_id == variant.id,
                models.ChannelListing.channel_id == 4
            ).first()
            
            if listing and variant.stock_quantity >= quantity:
                db_order_item = models.OrderItem(
                    order_id=db_order.id, variant_id=variant.id, quantity=quantity, unit_price=unit_price
                )
                db.add(db_order_item)
                
                # Stok düşümü ve dağıtım
                variant.stock_quantity -= quantity
                print(f"[BAŞARILI] {gelen_sku} eşleşti! Stoktan {quantity} adet düşüldü.")
                sync_stock_to_channels(db, variant.id, variant.stock_quantity, origin_channel_id=4)
            else:
                print(f"[UYARI] {gelen_sku} için stok yetersiz veya Shopify bağlantısı kurulamamış!")
        else:
            print(f"[ATLANDI] {gelen_sku} kodlu ürün yerel veritabanında bulunamadı.")
            
    db.commit() 
    return {"status": "success", "message": "Shopify siparişi işlendi ve stoklar güncellendi."}

@router.post("/n11/{merchant_id}")
async def n11_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    order_number = payload.get("orderNumber", "Bilinmiyor")
    total_price = float(payload.get("totalAmount", 0.0))
    items = payload.get("items", []) 
    
    print(f"\n--- {merchant_id} ID'Lİ SATICI İÇİN N11 SİPARİŞİ İŞLENİYOR ({order_number}) ---")
    
    db_order = models.Order(
        merchant_id=merchant_id, channel_id=3, order_number=order_number, total_amount=total_price, status="approved"
    )
    db.add(db_order)
    db.flush() 
    
    for item in items:
        channel_product_id = str(item.get("sku"))
        quantity = int(item.get("quantity", 1))
        unit_price = float(item.get("price", 0.0))
        
        listing = db.query(models.ChannelListing).join(models.Channel).filter(
            models.ChannelListing.channel_id == 3,
            models.ChannelListing.channel_product_id == channel_product_id,
            models.Channel.merchant_id == merchant_id
        ).first()
        
        if listing:
            variant = db.query(models.Variant).filter(models.Variant.id == listing.variant_id).first()
            if variant and variant.stock_quantity >= quantity:
                db_order_item = models.OrderItem(
                    order_id=db_order.id, variant_id=variant.id, quantity=quantity, unit_price=unit_price
                )
                db.add(db_order_item)
                
                variant.stock_quantity -= quantity
                print(f"[BAŞARILI] n11 ürünü ({channel_product_id}) eşleşti! Stoktan {quantity} adet düşüldü.")
                sync_stock_to_channels(db, variant.id, variant.stock_quantity, origin_channel_id=3)
            else:
                print(f"[UYARI] {channel_product_id} için stok yetersiz veya ürün bulunamadı!")
        else:
            print(f"[ATLANDI] {channel_product_id} kodlu ürün n11 eşleştirmelerinde bulunamadı.")
            
    db.commit() 
    return {"status": "success", "message": "n11 siparişi başarıyla işlendi ve stoklar güncellendi."}

@router.post("/trendyol/{merchant_id}")
def trendyol_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    order_number = payload.get("orderNumber", "Bilinmiyor")
    total_price = float(payload.get("totalPrice", 0.0))
    lines = payload.get("lines", [])
    
    standard_items = [{"sku": str(line.get("barcode")), "quantity": int(line.get("quantity", 1)), "price": float(line.get("price", 0.0))} for line in lines]
    return process_standardized_order(db, merchant_id, 5, order_number, total_price, standard_items)

@router.post("/hepsiburada/{merchant_id}")
def hepsiburada_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    order_number = payload.get("orderId", "Bilinmiyor")
    total_price = float(payload.get("totalAmount", 0.0))
    order_items = payload.get("orderItems", [])
    
    standard_items = [{"sku": str(item.get("merchantSku")), "quantity": int(item.get("quantity", 1)), "price": float(item.get("price", 0.0))} for item in order_items]
    return process_standardized_order(db, merchant_id, 6, order_number, total_price, standard_items)

@router.post("/pazarama/{merchant_id}")
def pazarama_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    order_number = payload.get("orderCode", "Bilinmiyor")
    total_price = float(payload.get("totalAmount", 0.0))
    products = payload.get("products", [])
    
    standard_items = [{"sku": str(i.get("sellerSku")), "quantity": int(i.get("quantity", 1)), "price": float(i.get("price", 0.0))} for i in products]
    return process_standardized_order(db, merchant_id, 7, order_number, total_price, standard_items)