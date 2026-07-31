from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime


Base = declarative_base()

# --- YENİ EKLENEN: Sistemin En Tepesindeki Kullanıcı/Mağaza Tablosu ---
class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, unique=True, index=True, nullable=False) # Örn: Saygın Grup Hırdavat
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False) # Güvenlik için şifreleri gizleyerek tutacağız
    created_at = Column(DateTime, default=datetime.utcnow)

    # İlişkiler (Bir satıcının birden fazla ürünü, kanalı ve siparişi olabilir)
    products = relationship("Product", back_populates="merchant")
    channels = relationship("Channel", back_populates="merchant")
    orders = relationship("Order", back_populates="merchant")

# --- MERKEZ TABLOLAR ---
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False) # SAHİBİ KİM?
    title = Column(String, nullable=False)
    brand = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    merchant = relationship("Merchant", back_populates="products")
    variants = relationship("Variant", back_populates="product")

class Variant(Base):
    __tablename__ = "variants"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    sku = Column(String, index=True, nullable=False) # Global Unique kuralı SaaS için kaldırıldı!
    barcode = Column(String, nullable=True)
    stock_quantity = Column(Integer, default=0)
    base_price = Column(Float, nullable=False)

    product = relationship("Product", back_populates="variants")
    channel_listings = relationship("ChannelListing", back_populates="variant")

# --- ENTEGRASYON KÖPRÜSÜ ---
class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False) # SAHİBİ KİM?
    name = Column(String, nullable=False)
    api_key = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)

    merchant = relationship("Merchant", back_populates="channels")
    listings = relationship("ChannelListing", back_populates="channel")

class ChannelListing(Base):
    __tablename__ = "channel_listings"
    id = Column(Integer, primary_key=True, index=True)
    variant_id = Column(Integer, ForeignKey("variants.id"), nullable=False)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    channel_product_id = Column(String, nullable=False) 
    channel_price = Column(Float, nullable=True) 

    variant = relationship("Variant", back_populates="channel_listings")
    channel = relationship("Channel", back_populates="listings")

# --- SİPARİŞ AKIŞI ---
class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False) # SAHİBİ KİM?
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    order_number = Column(String, nullable=False) 
    status = Column(String, default="pending") 
    total_amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    merchant = relationship("Merchant", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")

class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("variants.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)

    order = relationship("Order", back_populates="items")