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
import requests
from fastapi.responses import RedirectResponse


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
    auth_url = f"https://{SHOPIFY_STORE_URL}/admin/oauth/authorize?client_id={SHOPIFY_CLIENT_ID}&scope=read_products,read_inventory&redirect_uri=https://saygin-entegrasyon.onrender.com/shopify/callback"
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
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/products.json"
    
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