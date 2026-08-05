from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# Veritabanı ve model bağlantıları
import models
from database import get_db

# N11 işlemleri için yönlendirici (Router)
router = APIRouter(
    tags=["N11 İşlemleri"]
)

@router.get("/n11-siparisleri-cek")
def n11_siparisleri_cek(db: Session = Depends(get_db)):
    """N11 üzerinden son 3 günün siparişlerini çeker ve stok düşümlerini otonom yapar."""
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
                
                islem_raporu = []
                
                for siparis in siparisler:
                    try:
                        order_number_tag = siparis.find('.//orderNumber')
                        if order_number_tag is None:
                            order_number_tag = siparis.find('.//ns3:orderNumber', ns)
                        order_number = order_number_tag.text.strip() if order_number_tag is not None else "Bilinmiyor"
                        
                        mevcut_siparis = db.query(models.Order).filter(models.Order.order_number == order_number).first()
                        if mevcut_siparis:
                            islem_raporu.append(f"ATLANDI: {order_number} numaralı sipariş daha önce işlenmiş.")
                            continue
                            
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
                                    eski_stok = varyant.stock_quantity
                                    yeni_stok = eski_stok - satilan_adet
                                    varyant.stock_quantity = yeni_stok
                                    
                                    islem_raporu.append(f"HAFIZA GÜNCELLENDİ: {stok_kodu} yerel stok ({eski_stok} -> {yeni_stok})")
                                    stok_dusumu_yapildi_mi = True
                                    
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
                                    
                        if stok_dusumu_yapildi_mi or order_number != "Bilinmiyor":
                            yeni_siparis = models.Order(
                                merchant_id=1,
                                channel_id=3, 
                                order_number=order_number,
                                total_amount=0.0,
                                status="approved"
                            )
                            db.add(yeni_siparis)
                            db.commit()
                            islem_raporu.append(f"KAYIT BAŞARILI: {order_number} numaralı sipariş hafızaya işlendi.")
                            
                    except Exception as siparis_hatasi:
                        db.rollback()
                        islem_raporu.append(f"KRİTİK HATA ({order_number}): İşlem iptal edildi ve stok düşümü geri alındı. Hata: {str(siparis_hatasi)}")
                        
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

@router.get("/n11-stok-guncelle-test/{sku}")
def n11_stok_guncelle_test(sku: str, miktar: int):
    """N11 REST API kullanarak belirtilen ürünün stoğunu N11 panelinde anında günceller."""
    n11_key = os.getenv("N11_APP_KEY")
    n11_secret = os.getenv("N11_APP_SECRET")
    
    if not n11_key or not n11_secret:
        return {"durum": "HATA", "mesaj": "Render ortam değişkenlerinde N11_APP_KEY veya N11_APP_SECRET eksik."}
        
    url = "https://api.n11.com/ms/product/tasks/price-stock-update"
    headers = {
        "appkey": n11_key,
        "appsecret": n11_secret,
        "Content-Type": "application/json"
    }
    
    payload = {
        "payload": {
            "integrator": "SayginGrupEntegrasyon", 
            "skus": [
                {
                    "stockCode": sku.strip().upper(),
                    "quantity": miktar
                }
            ]
        }
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers)
        if res.status_code == 200:
            return {
                "durum": "N11 KAPIYI AÇTI VE STOK GÜNCELLENDİ", 
                "guncellenen_urun": sku, 
                "n11_yeni_stok": miktar,
                "n11_merkez_cevabi": res.json()
            }
        else:
            return {"durum": "API HATASI", "hata_kodu": res.status_code, "mesaj": res.text}
    except Exception as e:
        return {"durum": "KRİTİK HATA", "mesaj": str(e)}