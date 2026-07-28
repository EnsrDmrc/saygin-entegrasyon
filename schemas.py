from pydantic import BaseModel
from typing import List, Optional


# --- SAAS: SATICI/MÜŞTERİ KAYIT ŞEMALARI ---
class MerchantCreate(BaseModel):
    company_name: str
    email: str
    password: str # Kullanıcıdan düz şifreyi alıyoruz

class MerchantResponse(BaseModel):
    id: int
    company_name: str
    email: str
    
    # Şifreyi (hashed_password) kesinlikle dışarıya döndürmüyoruz!
    model_config = {"from_attributes": True}

# Kullanıcıdan alınacak Varyant (Stok) bilgileri
class VariantCreate(BaseModel):
    sku: str
    barcode: Optional[str] = None
    stock_quantity: int
    base_price: float

# Kullanıcıdan alınacak Ana Ürün bilgileri
class ProductCreate(BaseModel):
    title: str
    brand: Optional[str] = None
    variants: List[VariantCreate] # Bir ürünün birden fazla stoğu/modeli olabilir

    # Okuma (GET) işlemi için Variant şeması
class VariantResponse(BaseModel):
    id: int
    sku: str
    barcode: Optional[str] = None
    stock_quantity: int
    base_price: float

    # Veritabanı modellerini (SQLAlchemy) okuyabilmesi için gerekli ayar
    model_config = {"from_attributes": True} 

# Okuma (GET) işlemi için Product şeması
class ProductResponse(BaseModel):
    id: int
    title: str
    brand: Optional[str] = None
    variants: List[VariantResponse] = [] # Ürünün içindeki varyantları (stokları) da otomatik getir

    model_config = {"from_attributes": True}

# Stok Güncelleme İşlemi İçin Dışarıdan Beklenen Veri
class VariantStockUpdate(BaseModel):
    stock_quantity: int


# --- SATIŞ KANALLARI İÇİN ŞEMALAR ---
class ChannelCreate(BaseModel):
    name: str # Örn: "n11" veya "Web Sitem"
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

class ChannelResponse(BaseModel):
    id: int
    name: str
    is_active: bool

    model_config = {"from_attributes": True}

# --- EŞLEŞTİRME (MAPPING) İÇİN ŞEMALAR ---
class ChannelListingCreate(BaseModel):
    variant_id: int # Bizim veritabanındaki stok ID'si
    channel_id: int # Hangi platform olduğu (Örn: 1 = n11)
    channel_product_id: str # n11'in kendi sistemindeki ürün ID'si
    channel_price: Optional[float] = None # n11'de farklı fiyat satmak istersen

class ChannelListingResponse(BaseModel):
    id: int
    variant_id: int
    channel_id: int
    channel_product_id: str
    channel_price: Optional[float] = None

    model_config = {"from_attributes": True}


# --- SİPARİŞ (ORDER) ŞEMALARI ---
class OrderItemCreate(BaseModel):
    channel_product_id: str # n11'in bize gönderdiği kendi ID'si (Örn: N11-BOSCH-999)
    quantity: int
    unit_price: float

class OrderCreate(BaseModel):
    channel_id: int # Hangi platformdan geldi? (1 = n11)
    order_number: str # n11 Sipariş Numarası
    items: List[OrderItemCreate] # Sepetteki ürünlerin listesi


# --- SAAS: GİRİŞ YAPMA (LOGIN) VE TOKEN ŞEMALARI ---
class MerchantLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str