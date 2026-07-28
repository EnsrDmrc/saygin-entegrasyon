import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# Render'a kaydettiğimiz DATABASE_URL'yi çeker
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# Önemli Yama: SQLAlchemy artık 'postgres://' yerine 'postgresql://' formatını istiyor.
if SQLALCHEMY_DATABASE_URL and SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# FastAPI'nin her istekte veritabanına bağlanmasını sağlayan eksik fonksiyon
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()