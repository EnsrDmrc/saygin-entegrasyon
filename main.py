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
from shopify_engine import router as shopify_router
from n11_engine import router as n11_router
from webhook_engine import router as webhook_router
from maintenance_engine import router as maintenance_router


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# FastAPI uygulamasını başlatıyoruz
app = FastAPI(title="Çok Kanallı Entegrasyon API")

app.include_router(shopify_router)
app.include_router(n11_router)
app.include_router(webhook_router)
app.include_router(maintenance_router)

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


SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SHOPIFY_STORE_URL = "saygin-grup.myshopify.com"


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


