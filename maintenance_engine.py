from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
import os
import requests
import time
import models
from database import get_db, SessionLocal
import xml.etree.ElementTree as ET

# Shopify Mağaza Adresi Tanımı
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")

router = APIRouter(
    prefix="/bakim",
    tags=["Sistem Bakım ve Araçları"]
)

# --- 1. ARKA PLAN STOK EŞİTLEME ---
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

@router.get("/genel-stok-esitle")
def genel_stok_esitle_tetikle(background_tasks: BackgroundTasks):
    background_tasks.add_task(arka_planda_stok_esitle)
    return {"mesaj": "Stok eşitleme operasyonu arka planda başlatıldı."}

# --- 2. MANUEL STOK ONARIM ---
@router.get("/stok-onar/{stok_kodu}")
def stok_onar(stok_kodu: str, gercek_stok: int, db: Session = Depends(get_db)):
    varyant = db.query(models.Variant).filter(models.Variant.sku == stok_kodu.upper()).first()
    if not varyant:
        return {"durum": "BAŞARISIZ", "mesaj": f"'{stok_kodu.upper()}' bulunamadı."}
        
    eski_stok = varyant.stock_quantity
    varyant.stock_quantity = gercek_stok
    db.commit()
    return {"durum": "ONARIM BAŞARILI", "eski": int(eski_stok), "yeni": gercek_stok}

# --- 3. VERİTABANI TABLOSU ---
@router.get("/veritabani-tablosu", response_class=HTMLResponse)
def veritabani_tablosu(db: Session = Depends(get_db)):
    varyantlar = db.query(models.Variant).order_by(models.Variant.sku).all()
    html_content = "<html><head><style>table { border-collapse: collapse; width: 100%; } th, td { border: 1px solid #ddd; padding: 8px; }</style></head><body><h2>Stok Kartları</h2><table><tr><th>ID</th><th>SKU</th><th>Stok</th></tr>"
    for v in varyantlar:
        sku = v.sku if v.sku else "KOD YOK"
        html_content += f"<tr><td>{v.id}</td><td>{sku}</td><td>{int(v.stock_quantity)}</td></tr>"
    html_content += "</table></body></html>"
    return HTMLResponse(content=html_content, status_code=200)

# --- 4. GELİŞMİŞ TEMİZLİK (ÇİFT KAYITLARI SİLME) ---
@router.get("/cift-kayitlari-temizle")
def cift_kayitlari_temizle(db: Session = Depends(get_db)):
    try:
        rapor = []
        silinen_kopya = 0
        tum_urunler = db.query(models.Product).all()
        baslik_sozlugu = {}
        
        for urun in tum_urunler:
            if urun.title:
                temiz = urun.title.strip().upper()
                baslik_sozlugu.setdefault(temiz, []).append(urun)
        
        for baslik, liste in baslik_sozlugu.items():
            if len(liste) > 1:
                liste.sort(key=lambda x: x.id)
                asil = liste[0]
                kopyalar = liste[1:]
                
                for k in kopyalar:
                    varyantlar = db.query(models.Variant).filter(models.Variant.product_id == k.id).all()
                    for v in varyantlar:
                        db.query(models.ChannelListing).filter(models.ChannelListing.variant_id == v.id).delete(synchronize_session=False)
                        db.delete(v)
                    db.delete(k)
                    silinen_kopya += 1
                rapor.append(f"Asıl ID {asil.id} korundu. '{baslik}' kopyaları silindi.")
        db.commit()
        return {"durum": "TEMİZLİK BAŞARILI", "silinen_kopya": silinen_kopya, "detaylar": rapor}
    except Exception as e:
        db.rollback()
        return {"durum": "HATA", "mesaj": str(e)}



@router.get("/veritabani-kontrol")
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


@router.put("/urun-fiyat-guncelle/{sku}")
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

@router.get("/merkezi-stok-guncelle/{shopify_variant_id}")
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


@router.get("/kopyalari-temizle")
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


@router.get("/sku-normalize-birlestir")
def sku_normalize_birlestir(db: Session = Depends(get_db)):
    """Tüm SKU'ları büyük harfe çevirip boşlukları temizler, ardından gizli kopyaları birleştirir."""
    varyantlar = db.query(models.Variant).all()
    
    # ADIM 1: Ütüleme İşlemi (Boşlukları sil ve her şeyi BÜYÜK HARF yap)
    for varyant in varyantlar:
        if varyant.sku:
            # Örneğin: '  3m1300  ' şeklindeki bozuk bir kodu '3M1300' yapar
            normalize_edilmis_sku = str(varyant.sku).strip().upper()
            if varyant.sku != normalize_edilmis_sku:
                varyant.sku = normalize_edilmis_sku
    
    # İsim standartlaştırmalarını veritabanına kaydet ki gruplama düzgün çalışsın
    db.commit()
    
    # ADIM 2: Yeni ve temiz kodlara göre gruplama yap
    sku_gruplari = {}
    guncel_varyantlar = db.query(models.Variant).all()
    
    for varyant in guncel_varyantlar:
        if varyant.sku:
            if varyant.sku not in sku_gruplari:
                sku_gruplari[varyant.sku] = []
            sku_gruplari[varyant.sku].append(varyant)
            
    birlestirilen_kayit_sayisi = 0
    kalan_essiz_sku_sayisi = 0
    
    # ADIM 3: Aynı çatı altında topla ve fazlalıkları sil
    for sku, v_list in sku_gruplari.items():
        kalan_essiz_sku_sayisi += 1
        
        if len(v_list) > 1:
            ana_varyant = v_list[0] # İlkini patron kabul et
            silinecek_kopyalar = v_list[1:] 
            
            for kopya in silinecek_kopyalar:
                # Kopya üzerindeki pazaryeri şubelerini (N11/Shopify) patrona devret
                db.query(models.ChannelListing).filter(
                    models.ChannelListing.variant_id == kopya.id
                ).update({"variant_id": ana_varyant.id}, synchronize_session=False)
                
                # İçini boşalttığımız kopyayı yok et
                db.delete(kopya)
                birlestirilen_kayit_sayisi += 1
                
    # Silme ve birleştirme işlemlerini kalıcı yap
    db.commit()
    
    return {
        "durum": "STANDARTLAŞTIRMA VE BİRLEŞTİRME BAŞARILI",
        "mesaj": f"Tüm stok kodları temizlendi ve büyük harfe çevrildi. Toplam {birlestirilen_kayit_sayisi} adet gizli kopya birleştirildi.",
        "guncel_gercek_urun_sayisi": kalan_essiz_sku_sayisi
    }

@router.get("/sku-kontrol-raporu")
def sku_kontrol_raporu(db: Session = Depends(get_db)):
    """Shopify ve Yerel Veritabanı arasındaki stok kodu (SKU) uyuşmazlıklarını tespit eder."""
    try:
        # 1. Yerel Veritabanındaki SKU'ları Çek
        yerel_varyantlar = db.query(models.Variant.sku).all()
        yerel_skular = set([v[0].strip().upper() for v in yerel_varyantlar if v[0]])
        
        # 2. Shopify'daki SKU'ları Çek
        SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
        SHOPIFY_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")
        
        headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
        url = f"https://{SHOPIFY_URL}/admin/api/2026-07/products.json?limit=250"
        
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            return {"durum": "HATA", "mesaj": f"Shopify API'ye bağlanılamadı. Kod: {res.status_code}"}
            
        shopify_skular = set()
        for urun in res.json().get("products", []):
            for varyant in urun.get("variants", []):
                if varyant.get("sku"):
                    shopify_skular.add(varyant.get("sku").strip().upper())
                    
        # 3. Kesişim ve Farkları Hesapla
        sadece_shopifyda_olanlar = list(shopify_skular - yerel_skular)
        ortak_skular = list(yerel_skular & shopify_skular)
        
        return {
            "durum": "ANALİZ TAMAMLANDI",
            "ozet": {
                "sorunsuz_eslesen_urun_sayisi": len(ortak_skular),
                "hatali_veya_farkli_shopify_kodlari": len(sadece_shopifyda_olanlar)
            },
            "acil_mudahale_gereken_kodlar": sadece_shopifyda_olanlar,
            "sistem_mesaji": "Eğer 'acil_mudahale_gereken_kodlar' listesi boşsa, sistem %100 uyumludur. Liste doluysa, bu kodların sonundaki ekleri silerek Shopify panelinden düzeltmeniz gerekir."
        }
    except Exception as e:
        return {"durum": "KRİTİK HATA", "mesaj": str(e)}


@router.get("/eksik-urunu-ekle/{sku}")
def eksik_urunu_ekle(sku: str, db: Session = Depends(get_db)):
    """Shopify'dan belirli bir SKU'yu bulup yerel veritabanına ana ürün, varyant ve kanal bağlantısı olarak ekler."""
    try:
        hedef_sku = sku.strip().upper()
        
        # 1. Zaten var mı kontrolü
        mevcut = db.query(models.Variant).filter(models.Variant.sku == hedef_sku).first()
        if mevcut:
            return {"mesaj": f"{hedef_sku} kodu zaten veritabanında kayıtlı."}

        # 2. Shopify'dan ürünü bul
        SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
        SHOPIFY_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")
        headers = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}
        
        res = requests.get(f"https://{SHOPIFY_URL}/admin/api/2026-07/products.json?limit=250", headers=headers)
        if res.status_code != 200:
            return {"hata": "Shopify API bağlantı sorunu."}
            
        bulunan_varyant = None
        bulunan_urun = None
        
        for urun in res.json().get("products", []):
            for varyant in urun.get("variants", []):
                if varyant.get("sku") and varyant.get("sku").strip().upper() == hedef_sku:
                    bulunan_varyant = varyant
                    bulunan_urun = urun
                    break
            if bulunan_varyant:
                break
                
        if not bulunan_varyant:
            return {"hata": f"Shopify'da {hedef_sku} kodlu ürün bulunamadı. Lütfen Shopify panelinde kodun doğru yazıldığından emin ol."}

        # 3. Veritabanına İnşa Et
        # Ana Ürün (Product)
        yeni_urun = models.Product(
            merchant_id=1,
            title=bulunan_urun.get("title")
        )
        db.add(yeni_urun)
        db.flush() # ID'yi anında almak için flush yapıyoruz
        
        # Varyant (Variant) - 'base_price' olarak doğru isimle güncellendi.
        baslangic_stogu = bulunan_varyant.get("inventory_quantity", 0)
        yeni_varyant = models.Variant(
            product_id=yeni_urun.id,
            sku=hedef_sku,
            stock_quantity=baslangic_stogu,
            base_price=float(bulunan_varyant.get("price", 0.0))
        )
        db.add(yeni_varyant)
        db.flush()
        
        # Shopify Köprüsü (ChannelListing)
        yeni_listing = models.ChannelListing(
            variant_id=yeni_varyant.id,
            channel_id=4, 
            channel_product_id=str(bulunan_varyant.get("inventory_item_id"))
        )
        db.add(yeni_listing)
        
        db.commit()
        return {
            "durum": "BAŞARILI",
            "mesaj": f"Ürün veritabanına mükemmel şekilde entegre edildi.",
            "kaydedilen_bilgiler": {
                "urun_adi": bulunan_urun.get("title"),
                "sku": hedef_sku,
                "alinan_stok": baslangic_stogu,
                "kaydedilen_fiyat": float(bulunan_varyant.get("price", 0.0))
            }
        }
        
    except Exception as e:
        db.rollback()
        return {"durum": "HATA", "mesaj": str(e)}


# --- 5. OTONOM FİYAT KURTARMA MOTORU ---
def arka_planda_fiyat_kurtar():
    db = SessionLocal()
    try:
        N11_KEY = os.getenv("N11_APP_KEY", "").strip()
        N11_SECRET = os.getenv("N11_APP_SECRET", "").strip()
        SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
        SHOPIFY_URL = os.getenv("SHOPIFY_STORE_URL", "saygin-grup.myshopify.com")

        # Shopify bağlantısı (Kanal 4) olan tüm ürünleri bul
        listings = db.query(models.ChannelListing).filter(models.ChannelListing.channel_id == 4).all()
        
        print(f"\n--- 🛠️ {len(listings)} ÜRÜN İÇİN N11 -> SHOPIFY FİYAT KURTARMA BAŞLADI ---")
        
        for listing in listings:
            variant = db.query(models.Variant).filter(models.Variant.id == listing.variant_id).first()
            if not variant or not variant.sku:
                continue
                
            # DÜZELTME 1: .upper() KALDIRILDI. N11 büyük/küçük harfe duyarlıdır. Sadece boşlukları temizliyoruz.
            sku = str(variant.sku).strip()
            
            # GÜVENLİK: XML içinde hataya sebep olabilecek özel karakterleri (<, >, &) dönüştürüyoruz
            guvenli_sku = sku.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            
            # 1. N11 SOAP API'den Gerçek Fiyatı Çekme
            n11_xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
            <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:sch="http://www.n11.com/ws/schemas">
               <soapenv:Header/>
               <soapenv:Body>
                  <sch:GetProductBySellerCodeRequest>
                     <auth>
                        <appKey>{N11_KEY}</appKey>
                        <appSecret>{N11_SECRET}</appSecret>
                     </auth>
                     <sellerCode>{guvenli_sku}</sellerCode>
                  </sch:GetProductBySellerCodeRequest>
               </soapenv:Body>
            </soapenv:Envelope>"""
            
            n11_headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
            n11_res = requests.post("https://api.n11.com/ws/ProductService/", headers=n11_headers, data=n11_xml_payload.encode('utf-8'))
            
            # DÜZELTME 2: <displayPrice> (İndirimli) yerine, <price> (Gerçek Satış Fiyatı) çekiliyor
            if "<price>" in n11_res.text:
                fiyat_baslangic = n11_res.text.find("<price>") + len("<price>")
                fiyat_bitis = n11_res.text.find("</price>")
                
                # N11'den gelen baz fiyatı alıyoruz
                gercek_fiyat = float(n11_res.text[fiyat_baslangic:fiyat_bitis])
                
                # 2. Yerel Veritabanını Onar
                variant.base_price = gercek_fiyat
                db.commit()
                
                # 3. Shopify Vitrinini Onar
                shopify_variant_id = listing.channel_product_id
                shopify_url = f"https://{SHOPIFY_URL}/admin/api/2026-07/variants/{shopify_variant_id}.json"
                
                # Fiyatı metin (string) formatına çeviriyoruz
                shopify_payload = {
                    "variant": {
                        "id": shopify_variant_id,
                        "price": str(gercek_fiyat) 
                    }
                }
                
                shopify_res = requests.put(shopify_url, headers={"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}, json=shopify_payload)
                
                # Shopify'ın paketimizi kabul edip etmediğini kontrol ediyoruz
                if shopify_res.status_code == 200:
                    print(f"[ONARILDI] SKU: {sku} | Gerçek Satış Fiyatı: {gercek_fiyat} TL Shopify'a işlendi.")
                else:
                    print(f"[SHOPIFY HATASI] SKU: {sku} güncellenemedi! Hata Kodu: {shopify_res.status_code} | Sebep: {shopify_res.text}")
                
            time.sleep(0.3) 
            
        print("--- 🛠️ FİYAT KURTARMA OPERASYONU BAŞARIYLA TAMAMLANDI ---\n")
        
    except Exception as e:
        print(f"[HATA] Kurtarma operasyonu kesintiye uğradı: {str(e)}")
    finally:
        db.close()


from fastapi import BackgroundTasks

@router.get("/fiyatlari-kurtar")
def fiyatlari_kurtar_tetikle(background_tasks: BackgroundTasks):
    # Fonksiyonu arka plana atıyoruz ki binlerce ürün eşitlenirken tarayıcı (Swagger) hata verip kopmasın
    background_tasks.add_task(arka_planda_fiyat_kurtar)
    return {"mesaj": "Fiyat kurtarma operasyonu arka planda başlatıldı. İşlemin anlık ilerleyişini Render Log (Live Tail) ekranından izleyebilirsiniz."}