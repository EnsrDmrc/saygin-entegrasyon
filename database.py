from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# PostgreSQL bağlantı cümlen (Kullanıcı adı ve şifreni kendi pgAdmin ayarlarına göre değiştir)
# Format: postgresql://kullanici_adi:sifre@localhost:5432/veritabani_adi
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:ensarbaba123@localhost:5432/ecommerce_db"

# Engine: Veritabanı ile asıl iletişimi kuran motordur
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# SessionLocal: Her bir API isteği (örneğin n11'den gelen sipariş) için veritabanında geçici bir oturum açar
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base: Tablo modellerimizi (class'ları) türeteceğimiz ana sınıf
Base = declarative_base()

# Veritabanı oturumunu güvenli bir şekilde açıp kapatacak yardımcı fonksiyon
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()