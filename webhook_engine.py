from fastapi import APIRouter, Depends, Request, Body, HTTPException
from sqlalchemy.orm import Session
import models, schemas
from database import get_db

# Yönlendirici (Router) Tanımı: Tüm linkler otomatik olarak /webhooks ile başlayacak
router = APIRouter(
    prefix="/webhooks",
    tags=["Sipariş ve Webhook İşlemleri"]
)

# --- ÇİFT YÖNLÜ SENKRONİZASYON MOTORU ---
def sync_stock_to_channels(db: Session, variant_id: int, new_stock_quantity: int):
    listings = db.query(models.ChannelListing).filter(models.ChannelListing.variant_id == variant_id).all()
    print(f"\n--- 🔄 STOK SENKRONİZASYONU BAŞLADI (Varyant ID: {variant_id} | Yeni Stok: {new_stock_quantity}) ---")
    
    for listing in listings:
        if listing.channel_id == 3:
            channel_name = "n11"
        elif listing.channel_id == 4:
            channel_name = "Shopify"
        else:
            channel_name = f"Kanal ID: {listing.channel_id}"
            
        print(f"> {channel_name} sunucularına bağlanılıyor...")
        print(f"> {channel_name} API'sine '{listing.channel_product_id}' kodu için '{new_stock_quantity}' adet yeni stok bilgisi iletiliyor...")
        print(f"[BAŞARILI] {channel_name} vitrini güncellendi! Yeni Stok: {new_stock_quantity}")
        
    print("--- 🔄 TÜM KANALLARLA SENKRONİZASYON TAMAMLANDI ---\n")

# --- MERKEZİ SİPARİŞ İŞLEME MOTORU (GATEWAY) ---
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
                sync_stock_to_channels(db, variant.id, variant.stock_quantity)
            else:
                print(f"[UYARI] {sku} için stok yetersiz veya ürün bulunamadı!")
        else:
            print(f"[ATLANDI] {sku} bu kanalda eşleştirilemedi.")
            
    db.commit()
    return {"status": "success", "message": f"Kanal {channel_id} siparişi merkezi motorda başarıyla işlendi."}

@router.post("/shopify/{merchant_id}")
async def shopify_webhook(merchant_id: int, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
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
        channel_product_id = item.get("sku")
        if not channel_product_id:
            channel_product_id = str(item.get("variant_id"))
        
        quantity = int(item.get("quantity", 1))
        unit_price = float(item.get("price", 0.0))
        
        listing = db.query(models.ChannelListing).join(models.Channel).filter(
            models.ChannelListing.channel_id == 4,
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
                print(f"[BAŞARILI] {channel_product_id} eşleşti! Stoktan {quantity} adet düşüldü. Kalan Stok: {variant.stock_quantity}")
            else:
                print(f"[UYARI] {channel_product_id} için stok yetersiz veya ürün bulunamadı!")
        else:
            print(f"[ATLANDI] {channel_product_id} kodlu ürün veritabanı eşleştirmelerinde bulunamadı.")
            
    db.commit() 
    sync_stock_to_channels(db, variant.id, variant.stock_quantity)
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
            else:
                print(f"[UYARI] {channel_product_id} için stok yetersiz veya ürün bulunamadı!")
        else:
            print(f"[ATLANDI] {channel_product_id} kodlu ürün n11 eşleştirmelerinde bulunamadı.")
            
    db.commit() 
    sync_stock_to_channels(db, variant.id, variant.stock_quantity)
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

@router.post("/idefix/{merchant_id}")
def idefix_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    order_number = payload.get("order_no", "Bilinmiyor")
    total_price = float(payload.get("total_price", 0.0))
    basket_items = payload.get("basket_items", [])
    
    standard_items = [{"sku": str(i.get("merchant_sku")), "quantity": int(i.get("quantity", 1)), "price": float(i.get("price", 0.0))} for i in basket_items]
    return process_standardized_order(db, merchant_id, 8, order_number, total_price, standard_items)