from fastapi import FastAPI, Depends, HTTPException, status, Request, Body
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import models, schemas
from database import engine, get_db 
from typing import List
import bcrypt
import jwt
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os
import xml.etree.ElementTree as ET
import requests
from fastapi.responses import RedirectResponse
from database import SessionLocal
import psycopg2
from psycopg2.extras import execute_values
import time
from fastapi import BackgroundTasks

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# FastAPI uygulamasını başlatıyoruz
app = FastAPI(title="Çok Kanallı Entegrasyon API")

templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Geliştirme aşamasında her yerden gelen isteğe izin ver
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Bu komut, models.py içindeki sınıflara bakar ve eğer PostgreSQL'de bu tablolar yoksa sıfırdan oluşturur.
models.Base.metadata.create_all(bind=engine)

def get_password_hash(password: str) -> str:
    # Şifreyi güvenlik için bytelara çevirip tuzluyoruz (salt) ve hash'liyoruz
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed_bytes.decode('utf-8')

# YENİ EKLENEN KISIM: Müşteri Kayıt (Register) İşlemi
@app.post("/register/", response_model=schemas.MerchantResponse)
def register_merchant(merchant: schemas.MerchantCreate, db: Session = Depends(get_db)):
    # 1. Bu email veya şirket adıyla daha önce kayıt olunmuş mu kontrol et
    db_merchant = db.query(models.Merchant).filter(
        (models.Merchant.email == merchant.email) | 
        (models.Merchant.company_name == merchant.company_name)
    ).first()
    
    if db_merchant:
        raise HTTPException(status_code=400, detail="Bu email veya şirket adı zaten sistemde kayıtlı.")
    
    # 2. Şifreyi geri döndürülemez şekilde hash'le
    hashed_pwd = get_password_hash(merchant.password)
    
    # 3. Yeni satıcıyı (müşteriyi) veritabanına kaydet
    new_merchant = models.Merchant(
        company_name=merchant.company_name,
        email=merchant.email,
        hashed_password=hashed_pwd
    )
    
    db.add(new_merchant)
    db.commit()
    db.refresh(new_merchant)
    
    return new_merchant


# Dijital imzayı oluşturacağımız gizli anahtar (Gerçek projelerde gizli tutulur)
SECRET_KEY = "saygin_hirdavat_cok_gizli_anahtar_123"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 # Kartın geçerlilik süresi (Dakika)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Gelen düz şifreyi, veritabanındaki karmaşık şifreyle karşılaştırır
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


# YENİ EKLENEN KISIM: Form Verisi İle Müşteri Girişi (Login)
@app.post("/login/", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # DİKKAT: OAuth2 standardı gereği Swagger'daki 'username' kutusuna yazılan e-postayı alıyoruz
    merchant = db.query(models.Merchant).filter(models.Merchant.email == form_data.username).first()
    
    if not merchant or not verify_password(form_data.password, merchant.hashed_password):
        raise HTTPException(status_code=401, detail="Hatalı e-posta veya şifre.")
    
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": str(merchant.id), "exp": expire}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    
    return {"access_token": encoded_jwt, "token_type": "bearer"}

# Swagger UI'da sağ üste "Authorize" (Kilit) butonu ekler
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# YENİ EKLENEN KISIM: Kapıdaki Güvenlik Görevlisi
def get_current_merchant(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Kimlik doğrulanamadı veya oturum süresi doldu.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Token'ın mührünü aç ve içindeki "sub" (merchant_id) değerini oku
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        merchant_id: str = payload.get("sub")
        if merchant_id is None:
            raise credentials_exception
    except jwt.InvalidTokenError: # Geçersiz veya süresi dolmuş token
        raise credentials_exception
        
    # Kimliği doğrulanan satıcıyı veritabanından bul ve getir
    merchant = db.query(models.Merchant).filter(models.Merchant.id == int(merchant_id)).first()
    if merchant is None:
        raise credentials_exception
    return merchant

@app.get("/", response_class=HTMLResponse)
async def read_dashboard(request: Request, db: Session = Depends(get_db)):
    # Veritabanındaki ürünleri ve kanalları arayüze göndermek için çekiyoruz
    products = db.query(models.Product).all()
    channels = db.query(models.Channel).all()
    
    # Yeni FastAPI/Starlette sürümüne uygun TemplateResponse kullanımı
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={
            "request": request,
            "products": products,
            "channels": channels
        }
    )


# YENİ EKLENEN KISIM: Korumalı Ürün Ekleme İşlemi
@app.post("/products/")
def create_product(
    product: schemas.ProductCreate, 
    db: Session = Depends(get_db),
    current_merchant: models.Merchant = Depends(get_current_merchant) # GÜVENLİK KONTROLÜ!
):
    # 1. Ana ürünü eklerken, SAHİBİNİN KİM OLDUĞUNU (merchant_id) otomatik atıyoruz
    db_product = models.Product(
        merchant_id=current_merchant.id, # Token'dan gelen satıcı ID'si
        title=product.title, 
        brand=product.brand
    )
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    
    # 2. Ürüne ait stok kartlarını (varyantları) ekliyoruz
    for var in product.variants:
        db_variant = models.Variant(
            product_id=db_product.id,
            sku=var.sku, # Artık farklı satıcılar aynı SKU'yu kullanabilir!
            barcode=var.barcode,
            stock_quantity=var.stock_quantity,
            base_price=var.base_price
        )
        db.add(db_variant)
        
    db.commit()
    return {"mesaj": "Ürün başarıyla eklendi!", "product_id": db_product.id, "sahibi": current_merchant.company_name}




# YENİ EKLENEN KISIM: Korumalı Ürün Listeleme (GET) İşlemi
@app.get("/products/", response_model=List[schemas.ProductResponse])
def get_products(
    db: Session = Depends(get_db),
    current_merchant: models.Merchant = Depends(get_current_merchant) # GÜVENLİK KONTROLÜ!
):
    # Artık tüm ürünleri (.all()) değil, sadece bu satıcıya ait (merchant_id) ürünleri getiriyoruz!
    products = db.query(models.Product).filter(models.Product.merchant_id == current_merchant.id).all()
    return products

# YENİ EKLENEN KISIM: Stok Güncelleme (PATCH) İşlemi
@app.patch("/variants/{variant_id}/stock", response_model=schemas.VariantResponse)
def update_stock(variant_id: int, stock_update: schemas.VariantStockUpdate, db: Session = Depends(get_db)):
    # 1. Veritabanında bu ID'ye sahip varyantı (stok kartını) bul
    db_variant = db.query(models.Variant).filter(models.Variant.id == variant_id).first()
    
    # 2. Eğer böyle bir ürün yoksa sistemi durdur ve hata fırlat
    if not db_variant:
        raise HTTPException(status_code=404, detail="Belirtilen ID'ye sahip stok kartı bulunamadı.")
    
    # 3. Ürün varsa stoğunu yeni gelen değerle değiştir
    db_variant.stock_quantity = stock_update.stock_quantity
    
    db.commit() # Değişikliği veritabanına kalıcı olarak kaydet
    db.refresh(db_variant) # Nesnenin güncel halini geri al
    
    return db_variant


@app.post("/channels/", response_model=schemas.ChannelResponse)
def create_channel(
    channel: schemas.ChannelCreate, 
    db: Session = Depends(get_db), 
    current_merchant: models.Merchant = Depends(get_current_merchant) # 1. GÜVENLİK KONTROLÜ EKLENDİ
):
    db_channel = models.Channel(
        merchant_id=current_merchant.id, # 2. İŞTE HATAYA SEBEP OLAN EKSİK SATIR BURASIYDI!
        name=channel.name, 
        api_key=channel.api_key, 
        api_secret=channel.api_secret
    )
    db.add(db_channel)
    db.commit()
    db.refresh(db_channel)
    return db_channel


@app.get("/channels/")
def get_channels(db: Session = Depends(get_db)):
    channels = db.query(models.Channel).all()
    return channels

# YENİ EKLENEN KISIM 2: Ürünü Pazaryeri İle Eşleştirme (Mapping)
@app.post("/listings/", response_model=schemas.ChannelListingResponse)
def create_listing(listing: schemas.ChannelListingCreate, db: Session = Depends(get_db)):
    db_listing = models.ChannelListing(
        variant_id=listing.variant_id,
        channel_id=listing.channel_id,
        channel_product_id=listing.channel_product_id,
        channel_price=listing.channel_price
    )
    db.add(db_listing)
    db.commit()
    db.refresh(db_listing)
    return db_listing


@app.post("/orders/webhook", response_model=dict)
def receive_order(
    order: schemas.OrderCreate, 
    db: Session = Depends(get_db), 
    current_merchant: models.Merchant = Depends(get_current_merchant) # 1. GÜVENLİK KONTROLÜ EKLENDİ
):
    toplam_tutar = sum(item.quantity * item.unit_price for item in order.items)
    
    db_order = models.Order(
        merchant_id=current_merchant.id, # 2. İŞTE HATAYA SEBEP OLAN EKSİK SATIR BURASIYDI!
        channel_id=order.channel_id,
        order_number=order.order_number,
        total_amount=toplam_tutar,
        status="approved"
    )
    db.add(db_order)
    db.flush()

    for item in order.items:
        # Sadece bu satıcının eşleşmelerini arıyoruz
        listing = db.query(models.ChannelListing).join(models.Channel).filter(
            models.ChannelListing.channel_id == order.channel_id,
            models.ChannelListing.channel_product_id == item.channel_product_id,
            models.Channel.merchant_id == current_merchant.id 
        ).first()

        if not listing:
            raise HTTPException(status_code=404, detail=f"{item.channel_product_id} urunu bulunamadi.")

        variant = db.query(models.Variant).filter(models.Variant.id == listing.variant_id).first()
        if variant.stock_quantity < item.quantity:
            raise HTTPException(status_code=400, detail="Yetersiz stok!")

        db_order_item = models.OrderItem(
            order_id=db_order.id,
            variant_id=variant.id,
            quantity=item.quantity,
            unit_price=item.unit_price
        )
        db.add(db_order_item)
        variant.stock_quantity -= item.quantity # STOK BURADA OTOMATİK DÜŞÜYOR

    db.commit() 
    return {"mesaj": "Siparis onayi ve stok dusumu basarili!", "order_id": db_order.id}


# --- ÇİFT YÖNLÜ SENKRONİZASYON MOTORU ---
def sync_stock_to_channels(db: Session, variant_id: int, new_stock_quantity: int):
    # Bu ürünün satışta olduğu tüm pazar yeri eşleştirmelerini bul
    listings = db.query(models.ChannelListing).filter(models.ChannelListing.variant_id == variant_id).all()
    
    print(f"\n--- 🔄 STOK SENKRONİZASYONU BAŞLADI (Varyant ID: {variant_id} | Yeni Stok: {new_stock_quantity}) ---")
    
    for listing in listings:
        # Hangi kanal olduğunu tespit edelim (Şimdilik 3=n11, 4=Shopify)
        if listing.channel_id == 3:
            channel_name = "n11"
        elif listing.channel_id == 4:
            channel_name = "Shopify"
        else:
            channel_name = f"Kanal ID: {listing.channel_id}"
            
        print(f"> {channel_name} sunucularına bağlanılıyor...")
        print(f"> {channel_name} API'sine '{listing.channel_product_id}' kodu için '{new_stock_quantity}' adet yeni stok bilgisi iletiliyor...")
        
        # Gerçek hayatta burada requests.post() ile pazar yerinin API'sine paket gönderilir.
        # Biz şimdilik API'ye başarıyla iletildiğini simüle ediyoruz.
        
        print(f"[BAŞARILI] {channel_name} vitrini güncellendi! Yeni Stok: {new_stock_quantity}")
        
    print("--- 🔄 TÜM KANALLARLA SENKRONİZASYON TAMAMLANDI ---\n")


# --- MERKEZİ SİPARİŞ İŞLEME MOTORU (GATEWAY) ---
def process_standardized_order(db: Session, merchant_id: int, channel_id: int, order_number: str, total_price: float, items: list):
    """Tüm kanallardan gelen siparişleri tek bir merkezden yönetir."""
    
    print(f"\n--- {merchant_id} ID'Lİ SATICI İÇİN {channel_id} NUMARALI KANALDAN SİPARİŞ İŞLENİYOR ({order_number}) ---")
    
    # 1. Siparişi ana tabloya kaydet
    db_order = models.Order(
        merchant_id=merchant_id,
        channel_id=channel_id, 
        order_number=order_number,
        total_amount=total_price,
        status="approved"
    )
    db.add(db_order)
    db.flush() 
    
    # 2. Ürünleri dön ve stoğu yönet
    for item in items:
        sku = str(item.get("sku"))
        quantity = int(item.get("quantity", 1)) # Kesin tamsayı mantığı devrede
        price = float(item.get("price", 0.0))
        
        # Doğru kanalda, doğru ürünü bul
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
                
                # Stoğu düşür ve veritabanını güncelle
                variant.stock_quantity -= quantity
                db.commit()
                
                print(f"[BAŞARILI] {sku} eşleşti! Stoktan {quantity} adet düşüldü. Kalan Stok: {variant.stock_quantity}")
                
                # Yeni stoğu diğer kanallara fırlat
                sync_stock_to_channels(db, variant.id, variant.stock_quantity)
            else:
                print(f"[UYARI] {sku} için stok yetersiz veya ürün bulunamadı!")
        else:
            print(f"[ATLANDI] {sku} bu kanalda eşleştirilemedi.")
            
    db.commit()
    return {"status": "success", "message": f"Kanal {channel_id} siparişi merkezi motorda başarıyla işlendi."}

@app.post("/webhooks/shopify/{merchant_id}")
async def shopify_webhook(merchant_id: int, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    
    # 1. Shopify paketinden ana sipariş bilgilerini cımbızlıyoruz
    order_number = payload.get("name", "Bilinmiyor")
    total_price = float(payload.get("total_price", 0.0))
    line_items = payload.get("line_items", []) # Sepetteki ürünlerin listesi
    
    print(f"\n--- {merchant_id} ID'Lİ SATICI İÇİN SHOPIFY SİPARİŞİ İŞLENİYOR ({order_number}) ---")
    
    # 2. Siparişi ana tabloya kaydediyoruz (Shopify ID'miz 4)
    db_order = models.Order(
        merchant_id=merchant_id,
        channel_id=4, 
        order_number=order_number,
        total_amount=total_price,
        status="approved"
    )
    db.add(db_order)
    db.flush() # ID oluşması için geçici olarak veritabanına yansıtıyoruz
    
    # 3. Sepetteki ürünleri (line_items) tek tek dönüp eşleştirme ve stok düşümü yapıyoruz
    for item in line_items:
        # Shopify'da satılan ürünün barkodunu (SKU) alıyoruz
        channel_product_id = item.get("sku")
        if not channel_product_id:
            channel_product_id = str(item.get("variant_id"))
        
        # Hırdavat malzemelerinde yarım bir ürün olamayacağı için adedi kesinlikle tam sayı yapıyoruz
        quantity = int(item.get("quantity", 1))
        unit_price = float(item.get("price", 0.0))
        
        # Veritabanımızda 4 numaralı kanalda (Shopify) bu SKU'ya sahip ürünü arıyoruz
        listing = db.query(models.ChannelListing).join(models.Channel).filter(
            models.ChannelListing.channel_id == 4,
            models.ChannelListing.channel_product_id == channel_product_id,
            models.Channel.merchant_id == merchant_id
        ).first()
        
        if listing:
            variant = db.query(models.Variant).filter(models.Variant.id == listing.variant_id).first()
            
            if variant and variant.stock_quantity >= quantity:
                # Sipariş kalemini tabloya ekle
                db_order_item = models.OrderItem(
                    order_id=db_order.id,
                    variant_id=variant.id,
                    quantity=quantity,
                    unit_price=unit_price
                )
                db.add(db_order_item)
                
                # STOĞU DÜŞÜR
                variant.stock_quantity -= quantity
                print(f"[BAŞARILI] {channel_product_id} eşleşti! Stoktan {quantity} adet düşüldü. Kalan Stok: {variant.stock_quantity}")
            else:
                print(f"[UYARI] {channel_product_id} için stok yetersiz veya ürün bulunamadı!")
        else:
            print(f"[ATLANDI] {channel_product_id} kodlu ürün veritabanı eşleştirmelerinde (Listings) bulunamadı.")
            
    db.commit() # Tüm işlemleri kalıcı olarak kaydet
    sync_stock_to_channels(db, variant.id, variant.stock_quantity)
    return {"status": "success", "message": "Shopify siparişi işlendi ve stoklar güncellendi."}


@app.post("/webhooks/n11/{merchant_id}")
async def n11_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    
    # 1. n11 paketinden verileri alıyoruz
    order_number = payload.get("orderNumber", "Bilinmiyor")
    total_price = float(payload.get("totalAmount", 0.0))
    items = payload.get("items", []) 
    
    print(f"\n--- {merchant_id} ID'Lİ SATICI İÇİN N11 SİPARİŞİ İŞLENİYOR ({order_number}) ---")
    
    # 2. Siparişi ana tabloya kaydediyoruz
    db_order = models.Order(
        merchant_id=merchant_id,
        channel_id=3, 
        order_number=order_number,
        total_amount=total_price,
        status="approved"
    )
    db.add(db_order)
    db.flush() 
    
    # 3. Sepetteki ürünleri dönüp eşleştirme ve stok düşümü yapıyoruz
    for item in items:
        channel_product_id = str(item.get("sku"))
        
        # Tam sayı kontrolü
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
                    order_id=db_order.id,
                    variant_id=variant.id,
                    quantity=quantity,
                    unit_price=unit_price
                )
                db.add(db_order_item)
                
                # STOĞU DÜŞÜR
                variant.stock_quantity -= quantity
                print(f"[BAŞARILI] n11 ürünü ({channel_product_id}) eşleşti! Stoktan {quantity} adet düşüldü. Kalan Stok: {variant.stock_quantity}")
            else:
                print(f"[UYARI] {channel_product_id} için stok yetersiz veya ürün bulunamadı!")
        else:
            print(f"[ATLANDI] {channel_product_id} kodlu ürün n11 eşleştirmelerinde bulunamadı.")
            
    db.commit() 
    sync_stock_to_channels(db, variant.id, variant.stock_quantity)
    return {"status": "success", "message": "n11 siparişi başarıyla işlendi ve stoklar güncellendi."}


@app.post("/webhooks/trendyol/{merchant_id}")
def trendyol_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    
    # 1. Trendyol'un kendine has paket yapısını (JSON) okuma
    order_number = payload.get("orderNumber", "Bilinmiyor")
    total_price = float(payload.get("totalPrice", 0.0))
    lines = payload.get("lines", []) # Trendyol ürün listesine 'lines' der
    
    # 2. Merkezi motorun anlayacağı standart formata çevirme
    standard_items = []
    for line in lines:
        standard_items.append({
            "sku": str(line.get("barcode")), # Trendyol'da ürün kodu genelde 'barcode' olarak gelir
            "quantity": int(line.get("quantity", 1)),
            "price": float(line.get("price", 0.0))
        })
        
    # 3. İşi merkezi motora (Gateway) devretme (Trendyol Kanal ID'si: 5)
    result = process_standardized_order(
        db=db,
        merchant_id=merchant_id,
        channel_id=5,
        order_number=order_number,
        total_price=total_price,
        items=standard_items
    )
    
    return result

@app.post("/webhooks/hepsiburada/{merchant_id}")
def hepsiburada_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    
    # 1. Hepsiburada'nın JSON yapısını okuma
    order_number = payload.get("orderId", "Bilinmiyor") # Hepsiburada 'orderId' kullanır
    total_price = float(payload.get("totalAmount", 0.0))
    order_items = payload.get("orderItems", []) # Ürün listesi 'orderItems' olarak gelir
    
    # 2. Merkezi motorun anlayacağı standart formata çevirme
    standard_items = []
    for item in order_items:
        standard_items.append({
            "sku": str(item.get("merchantSku")), # Hepsiburada satıcı koduna 'merchantSku' der
            "quantity": int(item.get("quantity", 1)),
            "price": float(item.get("price", 0.0))
        })
        
    # 3. İşi merkezi motora devretme (Hepsiburada Kanal ID'si: 6)
    result = process_standardized_order(
        db=db,
        merchant_id=merchant_id,
        channel_id=6,
        order_number=order_number,
        total_price=total_price,
        items=standard_items
    )
    
    return result

@app.post("/webhooks/pazarama/{merchant_id}")
def pazarama_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    # Pazarama'nın JSON yapısı: orderCode ve products (satıcı kodu: sellerSku)
    order_number = payload.get("orderCode", "Bilinmiyor")
    total_price = float(payload.get("totalAmount", 0.0))
    products = payload.get("products", [])
    
    standard_items = [{"sku": str(i.get("sellerSku")), "quantity": int(i.get("quantity", 1)), "price": float(i.get("price", 0.0))} for i in products]
        
    return process_standardized_order(db, merchant_id, 7, order_number, total_price, standard_items)

@app.post("/webhooks/idefix/{merchant_id}")
def idefix_webhook(merchant_id: int, db: Session = Depends(get_db), payload: dict = Body(...)):
    # Idefix'in JSON yapısı: order_no ve basket_items (satıcı kodu: merchant_sku)
    order_number = payload.get("order_no", "Bilinmiyor")
    total_price = float(payload.get("total_price", 0.0))
    basket_items = payload.get("basket_items", [])
    
    standard_items = [{"sku": str(i.get("merchant_sku")), "quantity": int(i.get("quantity", 1)), "price": float(i.get("price", 0.0))} for i in basket_items]
        
    return process_standardized_order(db, merchant_id, 8, order_number, total_price, standard_items)




SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SHOPIFY_STORE_URL = "saygin-grup.myshopify.com"

@app.get("/shopify/install")
def shopify_install():
    # Mağazaya gidip ürün ve stok okuma yetkisi istiyoruz
    auth_url = f"https://{SHOPIFY_STORE_URL}/admin/oauth/authorize?client_id={SHOPIFY_CLIENT_ID}&scope=read_products,write_products,read_orders,write_orders,read_inventory,write_inventory,read_locations,read_customers,write_customers,read_fulfillments,write_fulfillments&redirect_uri=https://saygin-entegrasyon.onrender.com/shopify/callback"
    return RedirectResponse(auth_url)

@app.get("/shopify/callback")
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

@app.get("/shopify/products")
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



@app.get("/shopify/sync")
def sync_shopify_products(db: Session = Depends(get_db)):
    SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
    SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")
    
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/products.json?limit=250"
    
    merchant = db.query(models.Merchant).filter(models.Merchant.id == 1).first()
    if not merchant:
        merchant = models.Merchant(
            id=1,
            company_name="Saygın Grup Hırdavat",
            email="info@saygingruphirdavat.com.tr",
            hashed_password="entegrasyon_gecici_sifre_123"
        )
        db.add(merchant)
        db.commit()
        db.refresh(merchant)

    yeni_urun_sayisi = 0
    yeni_varyant_sayisi = 0

    # DÖNGÜ BAŞLIYOR: Tüm sayfalar bitene kadar ürünleri çekmeye devam edecek
    while url:
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            return {"hata": "Shopify verisi çekilemedi.", "detay": response.text}
            
        products_data = response.json().get("products", [])
        
        for item in products_data:
            mevcut_urun = db.query(models.Product).filter(models.Product.title == item.get("title")).first()
            
            if not mevcut_urun:
                yeni_urun = models.Product(
                    merchant_id=merchant.id,
                    title=item.get("title"),
                    brand=item.get("vendor")
                )
                db.add(yeni_urun)
                db.commit()
                db.refresh(yeni_urun)
                urun_id = yeni_urun.id
                yeni_urun_sayisi += 1
            else:
                urun_id = mevcut_urun.id

            variants_data = item.get("variants", [])
            for var in variants_data:
                shopify_variant_id = str(var.get("id"))
                mevcut_varyant = db.query(models.Variant).filter(models.Variant.sku == shopify_variant_id).first()
                
                if not mevcut_varyant:
                    yeni_varyant = models.Variant(
                        product_id=urun_id,
                        sku=shopify_variant_id, 
                        base_price=var.get("price"),
                        stock_quantity=var.get("inventory_quantity") or 0
                    )
                    db.add(yeni_varyant)
                    yeni_varyant_sayisi += 1
                    
        db.commit()

        # SAYFALAMA: 250'den sonraki sayfaya geçiş linkini bul
        link_header = response.headers.get('Link')
        url = None
        if link_header and 'rel="next"' in link_header:
            url = [link[link.find("<")+1:link.find(">")] for link in link_header.split(',') if 'rel="next"' in link][0]

    return {
        "mesaj": "Sınırsız Tarama Tamamlandı! Fiyat ve Stok Senkronizasyonu Başarılı!",
        "yeni_eklenen_urun_sayisi": yeni_urun_sayisi,
        "yeni_eklenen_varyant_sayisi": yeni_varyant_sayisi
    }

@app.get("/veritabani-kontrol")
def veritabani_kontrol(db: Session = Depends(get_db)):
    # 1. Emniyet kemerini kaldırdık: Tüm ürünleri baştan sona (ID'ye göre artan şekilde) çekiyoruz
    tum_urunler = db.query(models.Product).order_by(models.Product.id.asc()).all()
    
    liste = []
    for u in tum_urunler:
        # 2. Her ürünün ID'sini alıp, Variant tablosuna giderek o ürüne ait stok/fiyatları buluyoruz
        varyantlar = db.query(models.Variant).filter(models.Variant.product_id == u.id).all()
        
        varyant_listesi = []
        for v in varyantlar:
            varyant_listesi.append({
                "varyant_sku": v.sku,
                "fiyat": v.base_price,
                "stok_adedi": v.stock_quantity
            })
            
        # 3. Ana ürün bilgisiyle alt varyantları birleştirip paketliyoruz
        liste.append({
            "veritabani_id": u.id,
            "urun_adi": u.title,
            "marka": u.brand,
            "varyantlar": varyant_listesi
        })
        
    return {
        "sistem_mesaji": "Tüm Ürünler ve Stoklar Başarıyla Çekildi",
        "veritabanindaki_toplam_urun_sayisi": len(tum_urunler),
        "urun_katalogu": liste
    }

@app.post("/shopify/webhook/orders")
def shopify_order_webhook(payload: dict, db: Session = Depends(get_db)):
    # Shopify'dan gelen sipariş paketinin içindeki satır kalemlerini (sepeti) alıyoruz
    line_items = payload.get("line_items", [])
    guncellenen_varyant_sayisi = 0
    
    for item in line_items:
        # Satılan varyantın Shopify'daki ID'sini ve kaç adet satıldığını çekiyoruz
        shopify_variant_id = str(item.get("variant_id"))
        satilan_adet = item.get("quantity", 0)
        
        # Hatırlarsan varyantları kaydederken sku sütununa shopify_variant_id değerini yazmıştık.
        # Şimdi o ID ile PostgreSQL veritabanımızdan ilgili varyantı buluyoruz.
        varyant = db.query(models.Variant).filter(models.Variant.sku == shopify_variant_id).first()
        
    if varyant and satilan_adet > 0:
            eski_stok = varyant.stock_quantity
            yeni_stok = eski_stok - satilan_adet
            varyant.stock_quantity = max(0, yeni_stok)
            guncellenen_varyant_sayisi += 1
            
            # Render loglarında görmek için özel bir mesaj yazdırıyoruz
            print(f"BINGO! Shopify'dan sipariş geldi. SKU: {shopify_variant_id} | Stok {eski_stok} -> {varyant.stock_quantity} olarak güncellendi!")
            
    # Tüm sepet dönüldükten ve stoklar düşüldükten sonra veritabanına kalıcı olarak kaydediyoruz (Commit)
    db.commit()
    
    # Shopify'a "Haberi aldım, işlemi yaptım, her şey yolunda" (HTTP 200) sinyali dönüyoruz
    return {
        "mesaj": "Siparis basariyla islendi ve stoklar dusuldu", 
        "guncellenen_varyant_sayisi": guncellenen_varyant_sayisi
    }


@app.put("/urun-fiyat-guncelle/{sku}")
def urun_fiyat_guncelle(sku: str, yeni_fiyat: float, db: Session = Depends(get_db)):
    # 1. Önce kendi veritabanımızda ilgili ürünü (varyantı) buluyoruz
    varyant = db.query(models.Variant).filter(models.Variant.sku == sku).first()
    
    if not varyant:
        return {"hata": "Bu SKU'ya ait varyant veritabanında bulunamadı!"}

    # 2. Kendi sistemimizde fiyatı güncelliyoruz (Burası bizim merkezimiz)
    varyant.base_price = yeni_fiyat
    db.commit()

    # 3. Değişikliği anında Shopify vitrinine fırlatıyoruz (Push Mimarisi)
    SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
    
    # Shopify Variant Update API URL'si
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/variants/{sku}.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    # Shopify'ın bizden beklediği JSON paketi
    payload = {
        "variant": {
            "id": sku,
            "price": yeni_fiyat
        }
    }
    
    # Veriyi yolluyoruz (PUT metodu güncellemeler için kullanılır)
    response = requests.put(url, headers=headers, json=payload)
    
    if response.status_code == 200:
        return {
            "mesaj": "Mükemmel! Fiyat hem yerel veritabanında hem de Shopify vitrininde eşzamanlı olarak güncellendi.",
            "eski_fiyat": varyant.base_price,
            "yeni_fiyat": yeni_fiyat
        }
    else:
        return {
            "hata": "Kendi veritabanımız güncellendi ancak Shopify'a bağlanırken sorun oluştu.", 
            "shopify_yaniti": response.json()
        }



@app.post("/test-siparis-yarat/{sku}")
def test_siparis_yarat(sku: str, adet: int = 1):
    SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
    SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
    
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/orders.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    # Shopify'a gönderilecek sipariş paketi
    # inventory_behaviour parametresi, Shopify'a stoğu anında düşmesini emreder.
    payload = {
        "order": {
            "line_items": [
                {
                    "variant_id": sku,
                    "quantity": adet
                }
            ],
            "inventory_behaviour": "decrement_obeying_policy",
            "financial_status": "paid"
        }
    }
    
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 201:
        return {
            "mesaj": f"Başarılı! API üzerinden test siparişi oluşturuldu ve {adet} adet stok düşüldü.",
            "siparis_id": response.json()["order"]["id"]
        }
    else:
        return {
            "hata": "Sipariş oluşturulamadı.", 
            "detay": response.json()
        }


@app.get("/merkezi-stok-guncelle/{shopify_variant_id}")
def merkezi_stok_guncelle(shopify_variant_id: str, yeni_stok: int, db: Session = Depends(get_db)):
    # 1. YEREL VERİTABANI GÜNCELLEMESİ
    varyant = db.query(models.Variant).filter(models.Variant.sku == shopify_variant_id).first()
    
    if varyant:
        varyant.stock_quantity = yeni_stok
        db.commit()

    operasyon_raporu = {
        "yerel_veritabani": "Başarılı" if varyant else "Varyant bulunamadı, es geçildi",
        "shopify_durumu": "Bekliyor",
        "n11_durumu": "Bekliyor"
    }

    gercek_sku = None 

    # 2. SHOPIFY STOK GÜNCELLEMESİ VE SKU OKUMA
    try:
        SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
        SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
        headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}
        
        var_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/variants/{shopify_variant_id}.json"
        var_res = requests.get(var_url, headers=headers).json()
        
        inv_item_id = var_res["variant"]["inventory_item_id"]
        gercek_sku = var_res["variant"].get("sku") 

        loc_res = requests.get(f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/locations.json", headers=headers).json()
        location_id = loc_res["locations"][0]["id"]

        inv_set_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/inventory_levels/set.json"
        requests.post(inv_set_url, headers=headers, json={
            "location_id": location_id,
            "inventory_item_id": inv_item_id,
            "available": yeni_stok 
        })
        operasyon_raporu["shopify_durumu"] = "Başarılı"
    except Exception as e:
        operasyon_raporu["shopify_durumu"] = "Başarısız: Shopify'a bağlanılamadı."

    # 3. N11 STOK GÜNCELLEMESİ (NİHAİ ÇÖZÜM)
    if not gercek_sku:
        operasyon_raporu["n11_durumu"] = "Başarısız: Shopify'dan ortak SKU okunamadığı için N11'e gidilemedi."
    else:
        try:
            N11_APP_KEY = os.getenv("N11_APP_KEY", "").replace('"', '').replace("'", "").strip()
            N11_APP_SECRET = os.getenv("N11_APP_SECRET", "").replace('"', '').replace("'", "").strip()
            
            n11_xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
            <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:sch="http://www.n11.com/ws/schemas">
               <soapenv:Header/>
               <soapenv:Body>
                  <sch:UpdateStockByStockSellerCodeRequest>
                     <auth>
                        <appKey>{N11_APP_KEY}</appKey>
                        <appSecret>{N11_APP_SECRET}</appSecret>
                     </auth>
                     <stockItems>
                        <stockItem>
                           <sellerStockCode>{gercek_sku}</sellerStockCode>
                           <quantity>{yeni_stok}</quantity>
                        </stockItem>
                     </stockItems>
                  </sch:UpdateStockByStockSellerCodeRequest>
               </soapenv:Body>
            </soapenv:Envelope>"""
            
            n11_headers = {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "" 
            }
            
            # TEK EKSİK BURADAKİ EĞİK ÇİZGİYDİ (/)
            n11_url = "https://api.n11.com/ws/stockService/"
            
            n11_res = requests.post(n11_url, headers=n11_headers, data=n11_xml_payload.encode('utf-8'))
            
            if n11_res.status_code == 200 and "<status>success</status>" in n11_res.text:
                operasyon_raporu["n11_durumu"] = "Başarılı"
            else:
                operasyon_raporu["n11_durumu"] = f"Başarısız (HTTP {n11_res.status_code}) - Yanıt: {n11_res.text[:150]}"
                
        except Exception as e:
            operasyon_raporu["n11_durumu"] = f"Başarısız: N11'e bağlanılamadı. Hata: {str(e)}"

    return {
        "sistem_mesaji": "Çoklu kanal operasyonu tamamlandı.",
        "iletilen_shopify_id": shopify_variant_id,
        "kesfedilen_ortak_sku": gercek_sku,
        "yeni_merkez_stok": yeni_stok,
        "detayli_rapor": operasyon_raporu
    }

def merkez_stok_dagitici(stok_kodu: str, yeni_stok_adedi):
    """
    Veritabanında stok değiştiğinde bu fonksiyon tetiklenir ve 
    yeni stoğu tüm pazaryerlerine ve özel web sitesine fırlatır (Push).
    """
    try:
        # Fiziksel donanım ürünlerinde kesirli/ondalıklı değerler (0.5 vb.) gerçekçi 
        # olmadığı için, dışarıya fırlatılacak stoğun kesin bir tam sayı olduğundan emin oluyoruz.
        guncel_stok = int(yeni_stok_adedi) 
        
        islem_raporu = []
        
        # 1. SHOPIFY BİLDİRİMİ (Spoke 1)
        # shopify_sonuc = shopify_stok_guncelle(stok_kodu, guncel_stok)
        # islem_raporu.append(f"Shopify: {shopify_sonuc}")
        
        # 2. ÖZEL WEB SİTESİ BİLDİRİMİ (Spoke 2)
        # ozel_site_sonuc = ozel_site_stok_guncelle(stok_kodu, guncel_stok)
        # islem_raporu.append(f"Özel Site: {ozel_site_sonuc}")
        
        # 3. İLERİDE EKLENECEK DİĞER PAZARYERLERİ (Trendyol, Hepsiburada vb.)
        
        return {"durum": "basarili", "detay": islem_raporu}
        
    except Exception as e:
        print(f"Dagitici Hatasi: {str(e)}")
        return {"durum": "hata", "detay": str(e)}

@app.get("/sistemi-onar")
def sistemi_onar(db: Session = Depends(get_db)):
    """Veritabanındaki SKU hatalarını çözer ve Shopify stok ID'lerini eşleştirir."""
    
    # --- 1. EMNİYET KİLİDİ: EKSİK KANALI OLUŞTUR (HATA ÇÖZÜMÜ) ---
    merchant = db.query(models.Merchant).filter(models.Merchant.id == 1).first()
    if not merchant:
        merchant = models.Merchant(
            id=1,
            company_name="Saygın Grup Hırdavat",
            email="info@saygingruphirdavat.com.tr",
            hashed_password="entegrasyon_gecici_sifre_123"
        )
        db.add(merchant)
        db.commit()
        
    shopify_channel = db.query(models.Channel).filter(models.Channel.id == 4).first()
    if not shopify_channel:
        shopify_channel = models.Channel(
            id=4,
            merchant_id=1, # Sahibi Saygın Grup
            name="Shopify",
            api_key="sistem_otomatik_olusturdu",
            api_secret="sistem_otomatik_olusturdu"
        )
        db.add(shopify_channel)
        db.commit()
    # -------------------------------------------------------------

    # --- 2. ASIL ONARIM VE EŞLEŞTİRME İŞLEMİ ---
    SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
    SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")
    
    headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/products.json?limit=250"
    
    guncellenen = 0
    while url:
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            return {"hata": "Shopify API bağlantı sorunu."}
            
        for product in res.json().get('products', []):
            for variant in product.get('variants', []):
                gercek_sku = variant.get('sku')
                inv_id = variant.get('inventory_item_id')
                var_id = str(variant.get('id'))
                
                if gercek_sku and inv_id:
                    # 1. Eski ID'yi gerçek SKU ile değiştir
                    db_var = db.query(models.Variant).filter(models.Variant.sku == var_id).first()
                    if db_var:
                        db_var.sku = gercek_sku
                    else:
                        db_var = db.query(models.Variant).filter(models.Variant.sku == gercek_sku).first()
                        
                    # 2. Shopify Eşleştirmesini (Kanal 4) ekle
                    if db_var:
                        listing = db.query(models.ChannelListing).filter(
                            models.ChannelListing.variant_id == db_var.id,
                            models.ChannelListing.channel_id == 4
                        ).first()
                        
                        if not listing:
                            yeni_listing = models.ChannelListing(
                                variant_id=db_var.id,
                                channel_id=4,
                                channel_product_id=str(inv_id), 
                                channel_price=db_var.base_price
                            )
                            db.add(yeni_listing)
                        else:
                            listing.channel_product_id = str(inv_id)
                            
                        guncellenen += 1
        db.commit()
        
        link_header = res.headers.get('Link')
        url = None
        if link_header and 'rel="next"' in link_header:
            url = [link[link.find("<")+1:link.find(">")] for link in link_header.split(',') if 'rel="next"' in link][0]
                    
    return {"mesaj": f"Operasyon Tamam! Toplam {guncellenen} ürünün gerçek SKU'su onarıldı ve Shopify'a bağlandı."}

@app.get("/n11-siparisleri-cek")
def n11_siparisleri_cek(db: Session = Depends(get_db)):
    try:
        N11_APP_KEY = os.getenv("N11_APP_KEY", "").strip()
        N11_APP_SECRET = os.getenv("N11_APP_SECRET", "").strip()
        
        bugun = datetime.now().strftime("%d/%m/%Y")
        uc_gun_once = (datetime.now() - timedelta(days=3)).strftime("%d/%m/%Y")
        
        n11_xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:sch="http://www.n11.com/ws/schemas">
           <soapenv:Header/>
           <soapenv:Body>
              <sch:DetailedOrderListRequest>
                 <auth>
                    <appKey>{N11_APP_KEY}</appKey>
                    <appSecret>{N11_APP_SECRET}</appSecret>
                 </auth>
                 <searchData>
                    <status>New</status>
                    <period>
                       <startDate>{uc_gun_once}</startDate>
                       <endDate>{bugun}</endDate>
                    </period>
                 </searchData>
                 <pagingData>
                    <currentPage>0</currentPage>
                    <pageSize>100</pageSize>
                 </pagingData>
              </sch:DetailedOrderListRequest>
           </soapenv:Body>
        </soapenv:Envelope>"""
        
        n11_headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "" 
        }
        
        n11_url = "https://api.n11.com/ws/orderService/"
        n11_res = requests.post(n11_url, headers=n11_headers, data=n11_xml_payload.encode('utf-8'))
        
        if n11_res.status_code == 200:
            root = ET.fromstring(n11_res.content)
            ns = {'ns3': 'http://www.n11.com/ws/schemas'}
            
            status_tag = root.find('.//status')
            if status_tag is None:
                status_tag = root.find('.//ns3:status', ns)
            
            if status_tag is not None and status_tag.text == 'success':
                siparisler = root.findall('.//orderList/order')
                if not siparisler:
                    siparisler = root.findall('.//ns3:order', ns)
                
                if not siparisler:
                    return {"sistem_mesaji": "Devriye tamamlandi. Su an merkez stogu etkileyecek yeni bir N11 siparisi yok."}
                
                # --- ANA STOK DÜŞÜM MOTORU ---
                islem_raporu = []
                
                for siparis in siparisler:
                    # 1. SİPARİŞ NUMARASINI ÇEK (Yeni Eklenen Kısım)
                    order_number_tag = siparis.find('.//orderNumber')
                    if order_number_tag is None:
                        order_number_tag = siparis.find('.//ns3:orderNumber', ns)
                    order_number = order_number_tag.text.strip() if order_number_tag is not None else "Bilinmiyor"
                    
                    # 2. MÜKERRER SİPARİŞ KONTROLÜ - GÜVENLİK KİLİDİ (Yeni Eklenen Kısım)
                    mevcut_siparis = db.query(models.Order).filter(models.Order.order_number == order_number).first()
                    if mevcut_siparis:
                        islem_raporu.append(f"ATLANDI: {order_number} numaralı sipariş daha önce işlenmiş.")
                        continue # Bu siparişi atla, sıradaki siparişe geç
                        
                    stok_dusumu_yapildi_mi = False
                    
                    kalemler = siparis.findall('.//orderItemList/orderItem')
                    if not kalemler:
                        kalemler = siparis.findall('.//ns3:orderItem', ns)
                        
                    for kalem in kalemler:
                        sku_tag = kalem.find('.//productSellerCode')
                        if sku_tag is None:
                            sku_tag = kalem.find('.//ns3:productSellerCode', ns)
                            
                        adet_tag = kalem.find('.//quantity')
                        if adet_tag is None:
                            adet_tag = kalem.find('.//ns3:quantity', ns)
                            
                        if sku_tag is not None and adet_tag is not None:
                            stok_kodu = sku_tag.text.strip()
                            satilan_adet = int(adet_tag.text)
                            
                            varyant = db.query(models.Variant).filter(models.Variant.sku == stok_kodu).first()
                            
                            if varyant:
                                # Merkez Veritabanını Güncelle
                                eski_stok = varyant.stock_quantity
                                yeni_stok = eski_stok - satilan_adet
                                varyant.stock_quantity = yeni_stok
                                db.commit() 
                                islem_raporu.append(f"MERKEZ BAŞARILI: {stok_kodu} yerel stok güncellendi ({eski_stok} -> {yeni_stok})")
                                stok_dusumu_yapildi_mi = True
                                
                                # SHOPIFY'A OTOMATİK BİLDİRİM GÖNDER
                                listing = db.query(models.ChannelListing).filter(
                                    models.ChannelListing.variant_id == varyant.id,
                                    models.ChannelListing.channel_id == 4 
                                ).first()
                                
                                if listing:
                                    SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
                                    SHOPIFY_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")
                                    inventory_item_id = listing.channel_product_id
                                    
                                    loc_res = requests.get(f"https://{SHOPIFY_URL}/admin/api/2026-07/locations.json", headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN})
                                    if loc_res.status_code == 200:
                                        loc_id = loc_res.json()["locations"][0]["id"]
                                        
                                        inv_url = f"https://{SHOPIFY_URL}/admin/api/2026-07/inventory_levels/set.json"
                                        inv_res = requests.post(inv_url, headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}, json={
                                            "location_id": loc_id,
                                            "inventory_item_id": inventory_item_id,
                                            "available": yeni_stok
                                        })
                                        
                                        if inv_res.status_code == 200:
                                            islem_raporu.append(f"SHOPIFY BAŞARILI: {stok_kodu} vitrin stoğu {yeni_stok} adet olarak eşitlendi.")
                                        else:
                                            islem_raporu.append(f"SHOPIFY HATASI: Stok güncellenemedi. Hata: {inv_res.text}")
                            else:
                                islem_raporu.append(f"HATA: {stok_kodu} kodlu urun yerel veritabaninda bulunamadi.")
                                
                    # 3. İŞLEM BİTİNCE SİPARİŞİ HAFIZAYA KAYDET (Yeni Eklenen Kısım)
                    if stok_dusumu_yapildi_mi or order_number != "Bilinmiyor":
                        yeni_siparis = models.Order(
                            merchant_id=1,
                            channel_id=3, # 3 numara N11 Kanalı
                            order_number=order_number,
                            total_amount=0.0,
                            status="approved"
                        )
                        db.add(yeni_siparis)
                        db.commit()
                        islem_raporu.append(f"KAYIT BAŞARILI: {order_number} numaralı sipariş hafızaya işlendi.")
                                
                return {
                    "sistem_mesaji": "Siparis devriyesi tamamlandi ve veritabani senkronize edildi.",
                    "detaylar": islem_raporu
                }
            else:
                error_msg = root.find('.//errorMessage')
                if error_msg is None:
                    error_msg = root.find('.//ns3:errorMessage', ns)
                hata_metni = error_msg.text if error_msg is not None else "Bilinmeyen N11 hatasi."
                return {"hata": f"N11 islemi reddetti: {hata_metni}"}
        else:
            return {"hata": f"HTTP {n11_res.status_code} - Baglanti sorunu."}
            
    except Exception as e:
        return {"hata": f"Bir seyler ters gitti: {str(e)}"}


# --- 1. PARÇA: ARKA PLAN MOTORU ---
def arka_planda_stok_esitle():
    db = SessionLocal()
    try:
        SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
        SHOPIFY_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")
        N11_APP_KEY = os.getenv("N11_APP_KEY", "").strip()
        N11_APP_SECRET = os.getenv("N11_APP_SECRET", "").strip()

        loc_id = None
        if SHOPIFY_TOKEN:
            loc_res = requests.get(f"https://{SHOPIFY_URL}/admin/api/2026-07/locations.json", headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN})
            if loc_res.status_code == 200:
                loc_id = loc_res.json()["locations"][0]["id"]

        varyantlar = db.query(models.Variant).all()
        print(f"\n--- 🔄 ARKA PLAN: {len(varyantlar)} ÜRÜN İÇİN GENEL STOK EŞİTLEME BAŞLADI ---")
        
        for varyant in varyantlar:
            guncel_stok = int(varyant.stock_quantity) 
            gercek_sku = varyant.sku

            # === SHOPIFY ===
            listing = db.query(models.ChannelListing).filter(
                models.ChannelListing.variant_id == varyant.id,
                models.ChannelListing.channel_id == 4
            ).first()

            if listing and loc_id:
                inv_url = f"https://{SHOPIFY_URL}/admin/api/2026-07/inventory_levels/set.json"
                requests.post(inv_url, headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}, json={
                    "location_id": loc_id,
                    "inventory_item_id": listing.channel_product_id,
                    "available": guncel_stok
                })

            # === N11 ===
            if gercek_sku and N11_APP_KEY:
                n11_xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
                <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:sch="http://www.n11.com/ws/schemas">
                   <soapenv:Header/>
                   <soapenv:Body>
                      <sch:UpdateStockByStockSellerCodeRequest>
                         <auth>
                            <appKey>{N11_APP_KEY}</appKey>
                            <appSecret>{N11_APP_SECRET}</appSecret>
                         </auth>
                         <stockItems>
                            <stockItem>
                               <sellerStockCode>{gercek_sku}</sellerStockCode>
                               <quantity>{guncel_stok}</quantity>
                            </stockItem>
                         </stockItems>
                      </sch:UpdateStockByStockSellerCodeRequest>
                   </soapenv:Body>
                </soapenv:Envelope>"""
                n11_headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
                requests.post("https://api.n11.com/ws/stockService/", headers=n11_headers, data=n11_xml_payload.encode('utf-8'))
                    
            time.sleep(0.2)

        print("--- 🔄 ARKA PLAN: GENEL STOK EŞİTLEME BAŞARIYLA TAMAMLANDI ---\n")
    except Exception as e:
        print(f"Eşitleme Hatası: {str(e)}")
    finally:
        db.close() 

# --- 2. PARÇA: TARAYICIDAN TETİKLENECEK ŞALTER ---
@app.get("/genel-stok-esitle")
def genel_stok_esitle_tetikle(background_tasks: BackgroundTasks):
    background_tasks.add_task(arka_planda_stok_esitle)
    return {
        "mesaj": "Sistem emri aldı! Stok eşitleme operasyonu arka planda başlatıldı.",
        "detay": "Tüm ürünlerin taranıp güncellenmesi yaklaşık 3-5 dakika sürecektir. Bu sekmeyi güvenle kapatabilirsiniz."
    }


@app.get("/urun-bul")
def urun_bul(kelime: str = "", db: Session = Depends(get_db)):
    """İsme göre ürün arayıp Shopify ID'sini bulmayı sağlar."""
    # Ürünler ve Varyantlar tablolarını birleştirerek arama yapıyoruz
    sorgu = db.query(models.Variant).join(models.Product)
    
    if kelime:
        # Ürün adında, yazılan kelimeyi içerenleri (büyük/küçük harf duyarsız) filtrele
        sorgu = sorgu.filter(models.Product.title.ilike(f"%{kelime}%"))
        
    # Ekrana yüzlerce ürün yığılmasın diye ilk 50 sonucu getiriyoruz
    varyantlar = sorgu.limit(50).all() 
    
    sonuclar = []
    for v in varyantlar:
        sonuclar.append({
            "Urun_Adi": v.product.title if v.product else "Bilinmeyen Ürün",
            "Shopify_Varyant_ID": v.sku,  # Kurduğumuz yapıda Shopify ID'si SKU alanına kaydediliyor
            "Mevcut_Merkez_Stok": int(v.stock_quantity) # Hırdavat ürünlerinde küsurat olmaması için tam sayıya kilitliyoruz
        })
        
    return {
        "mesaj": f"'{kelime}' araması için {len(sonuclar)} sonuç bulundu." if kelime else "Sistemdeki son 50 ürün listeleniyor.",
        "urunler": sonuclar
    }


@app.get("/kopya-kontrol")
def kopya_kontrol(db: Session = Depends(get_db)):
    """Veritabanındaki tüm ürünleri tarayarak mükerrer SKU (Stok Kodu) kaydı olup olmadığını denetler."""
    varyantlar = db.query(models.Variant).all()
    
    sku_havuzu = {}
    kopya_listesi = []
    
    # Adım 1: Bütün stok kodlarını tek tek sayıyoruz
    for varyant in varyantlar:
        if varyant.sku: # Eğer ürünün bir stok kodu varsa
            if varyant.sku in sku_havuzu:
                sku_havuzu[varyant.sku] += 1
            else:
                sku_havuzu[varyant.sku] = 1
                
    # Adım 2: Sayısı 1'den fazla olanları (kopyaları) yakalıyoruz
    for sku, adet in sku_havuzu.items():
        if adet > 1:
            kopya_listesi.append({
                "Stok_Kodu": sku, 
                "Veritabanindaki_Kayit_Sayisi": adet
            })
            
    # Adım 3: Sonucu raporluyoruz
    if not kopya_listesi:
        return {
            "durum": "BAŞARILI",
            "mesaj": f"Harika haber! Taranan {len(varyantlar)} ürün arasında hiçbir mükerrer (kopya) stok kodu bulunamadı. Veritabanı tertemiz."
        }
        
    return {
        "durum": "UYARI",
        "mesaj": f"Sistemde {len(kopya_listesi)} adet ürünün birden fazla kaydı tespit edildi!",
        "kopya_detaylari": kopya_listesi
    }


@app.get("/kopyalari-temizle")
def kopyalari_temizle(db: Session = Depends(get_db)):
    """Veritabanındaki mükerrer varyantları ve onlara bağlı yetim kalacak listelemeleri temizler."""
    varyantlar = db.query(models.Variant).all()
    
    silinen_kayit_sayisi = 0
    islenen_skular = set()
    
    for varyant in varyantlar:
        if varyant.sku:
            if varyant.sku in islenen_skular:
                # 1. ADIM: Önce bu kopya varyanta bağlı olan channel_listings (pazaryeri listeleme) kayıtlarını sil
                db.query(models.ChannelListing).filter(models.ChannelListing.variant_id == varyant.id).delete(synchronize_session=False)
                
                # 2. ADIM: Alt bağlar koptuğuna göre artık asıl kopyayı güvenle silebiliriz
                db.delete(varyant)
                silinen_kayit_sayisi += 1
            else:
                # Bu SKU'yu ilk defa görüyoruz, listeye ekle ve koruma altına al.
                islenen_skular.add(varyant.sku)
                
    # Silme işlemlerini veritabanına kalıcı olarak kaydet
    db.commit()
    
    return {
        "durum": "TEMİZLİK BAŞARILI",
        "mesaj": f"Operasyon tamamlandı! Toplam {silinen_kayit_sayisi} adet mükerrer kopya ve bunlara bağlı alt kayıtlar veritabanından kalıcı olarak silindi.",
        "kalan_saglam_urun_sayisi": len(islenen_skular)
    }


@app.get("/sku-birlestir")
def sku_birlestir(db: Session = Depends(get_db)):
    """Aynı SKU'ya sahip farklı varyant kayıtlarını tek bir ana kayıt altında birleştirir."""
    varyantlar = db.query(models.Variant).all()
    
    # SKU'ları gruplamak için bir sözlük oluşturuyoruz
    sku_gruplari = {}
    for varyant in varyantlar:
        if varyant.sku:
            if varyant.sku not in sku_gruplari:
                sku_gruplari[varyant.sku] = []
            sku_gruplari[varyant.sku].append(varyant)
            
    birlestirilen_kayit_sayisi = 0
    kalan_essiz_sku_sayisi = 0
    
    for sku, v_list in sku_gruplari.items():
        kalan_essiz_sku_sayisi += 1
        # Eğer bir SKU'dan 1'den fazla kayıt varsa birleştirme yap
        if len(v_list) > 1:
            ana_varyant = v_list[0] # İlk kaydı merkez (patron) olarak kabul et
            silinecek_kopyalar = v_list[1:] # Geri kalanları silinecekler listesine al
            
            for kopya in silinecek_kopyalar:
                # 1. ADIM: Kopya varyanta bağlı pazaryeri kayıtlarını (N11/Shopify) ana varyanta bağla
                db.query(models.ChannelListing).filter(
                    models.ChannelListing.variant_id == kopya.id
                ).update({"variant_id": ana_varyant.id}, synchronize_session=False)
                
                # 2. ADIM: İçi boşalan ve yetim kalan kopya varyantı veritabanından tamamen sil
                db.delete(kopya)
                birlestirilen_kayit_sayisi += 1
                
    # Tüm güncellemeleri kalıcı olarak kaydet
    db.commit()
    
    return {
        "durum": "BİRLEŞTİRME BAŞARILI",
        "mesaj": f"Sistemdeki ayrı düşmüş kayıtlar aynı SKU çatısı altında birleştirildi. Toplam {birlestirilen_kayit_sayisi} adet fazla kayıt eritildi.",
        "guncel_gercek_urun_sayisi": kalan_essiz_sku_sayisi
    }