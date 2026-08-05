from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import os
import requests
# Bu çok önemli: main.py'deki models ve database dosyalarını buraya çağırıyoruz
import models
from database import get_db
from fastapi.responses import RedirectResponse


# Shopify Kimlik ve Bağlantı Tanımlamaları
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")

# APIRouter, main.py'nin yükünü hafifleten "Şube" yöneticisidir.
# prefix="/shopify" dediğimiz için buradaki tüm linkler otomatik olarak /shopify ile başlayacak.
router = APIRouter(
    prefix="/shopify",
    tags=["Shopify İşlemleri"]
)

@router.get("/akilli-sync")
def sync_shopify_akilli(db: Session = Depends(get_db)):
    """İsimlere değil, SKU'lara bakarak kopya oluşturmadan akıllı eşitleme yapan motor."""
    SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
    SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")
    
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    # URL'de limit=250 ile tek seferde maksimum veriyi çekiyoruz
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/products.json?limit=250"
    
    merchant = db.query(models.Merchant).filter(models.Merchant.id == 1).first()
    if not merchant:
        merchant = models.Merchant(
            id=1, company_name="Saygın Grup Hırdavat", email="info@saygingruphirdavat.com.tr", hashed_password="gecici"
        )
        db.add(merchant)
        db.commit()

    yeni_urun_sayisi = 0
    yeni_varyant_sayisi = 0
    es_gecilen_varyant_sayisi = 0

    while url:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return {"hata": "Shopify API bağlantı sorunu."}
            
        products_data = response.json().get("products", [])
        
        for item in products_data:
            product_title = item.get("title")
            variants_data = item.get("variants", [])
            
            mevcut_product_id = None
            for var in variants_data:
                sku = str(var.get("sku") or var.get("id")).strip().upper()
                db_var = db.query(models.Variant).filter(models.Variant.sku == sku).first()
                if db_var:
                    mevcut_product_id = db_var.product_id
                    break 
            
            if not mevcut_product_id:
                yeni_urun = models.Product(merchant_id=merchant.id, title=product_title, brand=item.get("vendor"))
                db.add(yeni_urun)
                db.flush() 
                mevcut_product_id = yeni_urun.id
                yeni_urun_sayisi += 1
            
            for var in variants_data:
                sku = str(var.get("sku") or var.get("id")).strip().upper()
                stok = var.get("inventory_quantity") or 0
                fiyat = float(var.get("price", 0.0))
                
                mevcut_varyant = db.query(models.Variant).filter(models.Variant.sku == sku).first()
                
                if not mevcut_varyant:
                    yeni_var = models.Variant(
                        product_id=mevcut_product_id, sku=sku, base_price=fiyat, stock_quantity=stok
                    )
                    db.add(yeni_var)
                    db.flush()
                    
                    yeni_listing = models.ChannelListing(
                        variant_id=yeni_var.id, channel_id=4, channel_product_id=str(var.get("inventory_item_id")), channel_price=fiyat
                    )
                    db.add(yeni_listing)
                    yeni_varyant_sayisi += 1
                else:
                    es_gecilen_varyant_sayisi += 1

        db.commit()

        link_header = response.headers.get('Link')
        url = None
        if link_header and 'rel="next"' in link_header:
            url = [link[link.find("<")+1:link.find(">")] for link in link_header.split(',') if 'rel="next"' in link][0]

    return {
        "mesaj": "Akıllı Senkronizasyon Tamamlandı! Artık kopyalar oluşmayacak.",
        "yeni_eklenen_ana_urun_basligi": yeni_urun_sayisi,
        "yeni_eklenen_stok_karti": yeni_varyant_sayisi,
        "zaten_var_oldugu_icin_atlanan": es_gecilen_varyant_sayisi
    }


@router.get("/shopify/install")
def shopify_install():
    # Mağazaya gidip ürün ve stok okuma yetkisi istiyoruz
    auth_url = f"https://{SHOPIFY_STORE_URL}/admin/oauth/authorize?client_id={SHOPIFY_CLIENT_ID}&scope=read_products,write_products,read_orders,write_orders,read_inventory,write_inventory,read_locations,read_customers,write_customers,read_fulfillments,write_fulfillments&redirect_uri=https://saygin-entegrasyon.onrender.com/shopify/callback"
    return RedirectResponse(auth_url)



@router.get("/shopify/callback")
def shopify_callback(code: str, shop: str):
    # Onaydan sonra dönen kodu (code), asıl Token ile takas ediyoruz
    token_url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "code": code
    }
    response = requests.post(token_url, json=payload)
    access_token = response.json().get("access_token")

    # Şifreyi Render loglarına yazdırıyoruz!
    print("="*50)
    print(f"BINGO! İŞTE ARADIĞIMIZ TOKEN: {access_token}")
    print("="*50)

    return {"mesaj": "Yetkilendirme Basarili! Lutfen Render Log ekranina (Live tail) donup shpat_ ile baslayan token'i kopyala."}

# Ortam değişkeninden token'ı çekiyoruz
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")



@router.get("/shopify/products")
def get_shopify_products():
    if not SHOPIFY_ACCESS_TOKEN:
        return {"hata": "Shopify Token bulunamadı. Lütfen Render Environment ayarlarını kontrol edin."}
        
    # Shopify 2026-07 API versiyonunu kullanarak ürünler uç noktasına gidiyoruz
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/products.json?limit=250"
    
    # Kapıyı açacak olan özel VIP kartımız (Token) başlıklar (headers) arasına ekleniyor
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    # Mağazaya GET isteği atıyoruz
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        products_data = response.json().get("products", [])
        
        # Karmaşık JSON verisi içinden sadece hırdavat ürünlerinin adını ve fiyatını süzüyoruz
        vitrin = []
        for item in products_data:
            title = item.get("title")
            variants = item.get("variants", [])
            # Eğer ürünün varyantı varsa ilk varyantın fiyatını al
            price = variants[0].get("price") if variants else "0.00"
            vitrin.append({"urun_adi": title, "fiyat": price})
            
        return {
            "mesaj": "Veri çekme başarılı!",
            "toplam_urun_sayisi": len(vitrin),
            "urunler": vitrin
        }
    else:
        return {
            "hata": f"Mağazaya ulaşılamadı. Hata Kodu: {response.status_code}",
            "detay": response.text
        }
