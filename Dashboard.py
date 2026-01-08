import streamlit as st
import firebase_admin
from firebase_admin import credentials, db, storage
import time
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
from PIL import Image as PILImage, ImageOps
import io
import requests
import json

# ====================================================================
# 1. AYARLAR VE GÜVENLİ BAĞLANTI (SIFIR HARDCODE)
# ====================================================================
st.set_page_config(
    page_title="ETM 7/24 Panel",
    page_icon="🌸",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- ANAHTARLARI SADECE SECRETS'TAN AL ---
try:
    # Kodun içinde asla varsayılan değer (default) bırakmıyoruz.
    # Sadece Streamlit'in kasasından (secrets) okuyacak.
    ADMIN_EMAIL = st.secrets["ADMIN_EMAIL"]
    FIREBASE_WEB_API_KEY = st.secrets["FIREBASE_WEB_API_KEY"]
    STORAGE_BUCKET_NAME = st.secrets["STORAGE_BUCKET_NAME"]
    DB_URL = st.secrets["DB_URL"]
except KeyError as e:
    st.error(f"HATA: Streamlit Secrets ayarlarında {e} eksik! Lütfen Dashboard ayarlarından ekleyin.")
    st.stop()

# --- FIREBASE BAŞLATMA ---
if not firebase_admin._apps:
    try:
        if "textkey" in st.secrets:
            key_dict = json.loads(st.secrets["textkey"])
            cred = credentials.Certificate(key_dict)
            
            firebase_admin.initialize_app(cred, {
                'databaseURL': DB_URL,
                'storageBucket': STORAGE_BUCKET_NAME  # Hatayı çözen kısım burası (Secrets'tan geliyor)
            })
        else:
            st.error("Secrets içinde 'textkey' bulunamadı.")
            st.stop()
    except Exception as e:
        st.error(f"Firebase Bağlantı Hatası: {e}")
        st.stop()

# ====================================================================
# 2. YARDIMCI FONKSİYONLAR
# ====================================================================
def auth_request(endpoint, email, password):
    try:
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:{endpoint}?key={FIREBASE_WEB_API_KEY}"
        headers = {"Content-Type": "application/json"}
        data = {"email": email, "password": password, "returnSecureToken": True}
        response = requests.post(url, headers=headers, json=data)
        return response.json()
    except: return None

def create_user_db_entry(uid, email, full_name):
    try:
        db.reference(f'users/{uid}').set({"email": email, "full_name": full_name, "approved": False, "machines": ["DEMO"]})
        return True
    except: return False

def get_user_data(uid):
    try: return db.reference(f'users/{uid}').get()
    except: return None

def get_all_users():
    try: return db.reference('users').get()
    except: return {}

def get_all_machines():
    try:
        data = db.reference('machines').get()
        return list(data.keys()) if data else []
    except: return []

def update_user_status(uid, approved, machines):
    try:
        db.reference(f'users/{uid}').update({"approved": approved, "machines": machines})
        return True
    except: return False

def get_machine_status(mid):
    try: return db.reference(f'machines/{mid}/info').get()
    except: return None

def get_slots(mid):
    try:
        data = db.reference(f'machines/{mid}/slots').get()
        if isinstance(data, list):
            return {str(i): item for i, item in enumerate(data) if item}
        return data if data else {}
    except: return {}

def get_sales_history(mid):
    try:
        data = db.reference(f'machines/{mid}/satis_hareketleri').get()
        if not data: return None
        
        if isinstance(data, dict):
            sales = list(data.values())
        else:
            sales = data
            
        df = pd.DataFrame(sales)
        if df.empty: return None
        
        cols = {c.lower(): c for c in df.columns}
        
        if 'tarih' in cols: df['Tarih'] = pd.to_datetime(df[cols['tarih']])
        elif 'date' in cols: df['Tarih'] = pd.to_datetime(df[cols['date']])
        
        if 'fiyat' in cols: df['Tutar'] = df[cols['fiyat']]
        elif 'price' in cols: df['Tutar'] = df[cols['price']]
        
        if 'kutu' in cols: df['Kutu'] = df[cols['kutu']]
        elif 'kutu_no' in cols: df['Kutu'] = df[cols['kutu_no']]
        
        if 'durum' in cols: df['Durum'] = df[cols['durum']]
        
        if 'urun' in cols: df['Ürün'] = df[cols['urun']]
        elif 'Kutu' in df.columns: df['Ürün'] = df['Kutu'].astype(str)
        else: df['Ürün'] = "Bilinmiyor"
            
        return df
    except Exception as e: 
        print(f"Log Hatası: {e}")
        return None

def send_open_command(mid, sid):
    try:
        db.reference(f'machines/{mid}/commands').update({"open_gate": str(sid), "timestamp": time.time()})
        st.toast(f"🚪 {sid}. Kapak İçin Sinyal Gönderildi!", icon="✅")
    except Exception as e: st.error(f"Hata: {e}")
    
def update_slot(mid, sid, price, enabled):
    db.reference(f'machines/{mid}/slots/{sid}').update({"price": price, "enabled": enabled})

def update_product_info(mid, sid, name, price, url):
    data = {"price": price, "product_name": name, "enabled": True, "last_restock": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if url: data["image_url"] = url
    db.reference(f'machines/{mid}/slots/{sid}').update(data)

def upload_image_to_firebase(image_file, mid, sid):
    try:
        image = PILImage.open(image_file)
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "P"): image = image.convert("RGB")
        image.thumbnail((500, 500)) 
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG', quality=70)
        img_byte_arr = img_byte_arr.getvalue()
        
        # DÜZELTME: Bucket ismini koddan değil Secrets değişkeninden alıyor
        bucket = storage.bucket(name=STORAGE_BUCKET_NAME)
        
        blob = bucket.blob(f"machines/{mid}/current_slot_{sid}.jpg")
        blob.cache_control = 'public, max-age=0'
        blob.upload_from_string(img_byte_arr, content_type='image/jpeg')
        blob.make_public()
        return blob.public_url
    except Exception as e:
        st.error(f"Resim Hatası: {e}"); return None

# ====================================================================
# 3. GİRİŞ VE KAYIT SAYFALARI
# ====================================================================
def login_page():
    st.markdown("<h1 style='text-align: center; color: #D9007E;'>ETM 7/24 Bayi Paneli</h1>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        t1, t2 = st.tabs(["Giriş Yap", "Kayıt Ol"])
        with t1:
            with st.form("login"):
                email = st.text_input("E-Posta")
                pw = st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", type="primary"):
                    res = auth_request("signInWithPassword", email, pw)
                    if res and "localId" in res:
                        uid = res["localId"]
                        if email == ADMIN_EMAIL:
                            st.session_state.update({'logged_in':True, 'user':"Yönetici", 'is_admin':True, 'machines': get_all_machines() or ["ETM_001"]})
                            st.rerun()
                        u_data = get_user_data(uid)
                        if u_data and u_data.get("approved"):
                            st.session_state.update({'logged_in':True, 'user':u_data.get("full_name"), 'is_admin':False, 'machines':u_data.get("machines", [])})
                            st.rerun()
                        else: st.warning("Hesap onayı bekleniyor.")
                    else: st.error("Hatalı giriş.")
        with t2:
            with st.form("reg"):
                name = st.text_input("Ad Soyad")
                email = st.text_input("E-Posta")
                pw = st.text_input("Şifre", type="password")
                if st.form_submit_button("Kayıt Ol"):
                    res = auth_request("signUp", email, pw)
                    if res and "localId" in res:
                        create_user_db_entry(res["localId"], email, name)
                        st.success("Kayıt alındı.")
                    else: st.error("Kayıt başarısız.")

# ====================================================================
# 4. YÖNETİCİ PANELİ
# ====================================================================
def admin_management_panel():
    st.markdown("### 🛡️ Yönetici Paneli")
    users = get_all_users()
    all_machines = get_all_machines()
    if not users: return
    for uid, udata in users.items():
        if udata.get("email") == ADMIN_EMAIL: continue
        with st.expander(f"👤 {udata.get('full_name')} - {'✅' if udata.get('approved') else '⏳'}"):
            c1, c2 = st.columns([2, 1])
            with c1:
                curr = udata.get("machines", [])
                if isinstance(curr, str): curr = [curr]
                sel = st.multiselect("Makineler", all_machines, default=[m for m in curr if m in all_machines], key=f"m_{uid}")
            with c2:
                appr = st.checkbox("Onayla", value=udata.get("approved", False), key=f"a_{uid}")
                if st.button("Kaydet", key=f"s_{uid}"):
                    update_user_status(uid, appr, sel); st.success("Güncellendi!"); st.rerun()

# ====================================================================
# 5. DASHBOARD VE MAKİNE YÖNETİMİ
# ====================================================================
def dashboard_page():
    c1, c2, c3 = st.columns([6, 2, 1])
    with c1: 
        role = " (Yönetici)" if st.session_state.get('is_admin') else ""
        st.title(f"👋 Hoşgeldin, {st.session_state.get('user')}{role}")
    with c2: 
        if st.button("🔄 Yenile", use_container_width=True): st.rerun()
    with c3:
        if st.button("Çıkış", use_container_width=True):
            st.session_state['logged_in'] = False
            st.rerun()
    st.divider()
    
    if st.session_state.get('is_admin'):
        admin_management_panel()
        st.divider()
        st.markdown("### 🖥️ Tüm Makinelerin Durumu")
    
    machines = st.session_state.get('machines', [])
    if not machines: st.info("Görüntülenecek makine yok."); return

    for mid in machines:
        info = get_machine_status(mid)
        with st.container():
            c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
            is_online = False; temp = 0; location = "---"
            if info:
                last_seen_str = info.get('last_seen', '---')
                temp = info.get('temperature', 0)
                location = info.get('location', '---')
                try:
                    last_seen = datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                    now_tr = datetime.utcnow() + timedelta(hours=3)
                    diff = (now_tr - last_seen).total_seconds()
                    if info.get('online_status', False) and diff < 300:
                        is_online = True
                except: 
                    is_online = False

            with c1:
                st.subheader(f"🖥️ {mid}")
                st.caption(f"Konum: {location}")
            with c2: st.metric("Sıcaklık", f"{temp} °C")
            with c3:
                if is_online: st.metric("Durum", "🟢 Çevrimiçi")
                else: st.metric("Durum", "🔴 Çevrimdışı")
            with c4:
                st.write("")
                if st.button("⚙️ YÖNET", key=f"btn_{mid}", type="primary", use_container_width=True):
                    st.session_state['selected_machine'] = mid
                    st.rerun()
        st.divider()

def manage_machine_page():
    mid = st.session_state['selected_machine']
    c_back, c_refresh = st.columns([1, 6])
    with c_back:
        if st.button("⬅️ Geri"): del st.session_state['selected_machine']; st.rerun()
    with c_refresh:
        if st.button("🔄 Yenile", key="refresh_manage"): st.rerun()

    st.header(f"🔧 Ayarlar: {mid}")
    slots = get_slots(mid)
    if not slots: st.warning("Veri yok veya cihaz çevrimdışı."); return

    tab1, tab2, tab3, tab4 = st.tabs(["💰 Fiyat & Stok", "🎮 Uzaktan Kontrol", "📊 Satış & Analiz", "🌷 Akıllı Dolum"])
    
    with tab1:
        st.info("💡 Ürün fotoğrafına tıklayarak büyütebilirsiniz.")
        sorted_ids = sorted(slots.keys(), key=lambda x: int(x) if x.isdigit() else 999)
        with st.form("slots_form"):
            sub_top = st.form_submit_button("💾 TÜMÜNÜ KAYDET", type="primary", use_container_width=True, key="save_top")
            st.divider()
            cols = st.columns(2) 
            for i, sid in enumerate(sorted_ids):
                data = slots[sid]
                with cols[i % 2]:
                    with st.container(border=True):
                        c_img, c_info = st.columns([1, 2.5]) 
                        with c_img:
                            if 'image_url' in data: st.image(data['image_url'], width=120)
                            else: st.write("📷 *Yok*")
                        with c_info:
                            st.markdown(f"##### 📦 Raf {sid}")
                            st.markdown(f"**{data.get('product_name', '---')}**")
                            sub_c1, sub_c2 = st.columns(2)
                            with sub_c1: new_price = st.number_input(f"Fiyat", min_value=0, value=int(data.get('price', 0)), key=f"p_{sid}", label_visibility="collapsed")
                            with sub_c2: is_active = st.checkbox("Satışta", value=data.get('enabled', False), key=f"e_{sid}")
            st.write("")
            sub_btm = st.form_submit_button("💾 TÜMÜNÜ KAYDET", type="primary", use_container_width=True, key="save_bottom")
            if sub_top or sub_btm:
                bar = st.progress(0, text="Kaydediliyor..."); total = len(sorted_ids)
                for i, sid in enumerate(sorted_ids):
                    update_slot(mid, sid, st.session_state[f"p_{sid}"], st.session_state[f"e_{sid}"])
                    bar.progress((i+1)/total)
                st.success("Kaydedildi."); time.sleep(1); st.rerun()

    with tab2:
        st.warning("⚠️ Butonlar fiziksel kapağı anında açar!")
        cols_remote = st.columns(4)
        for i, sid in enumerate(sorted_ids):
            with cols_remote[i % 4]:
                if st.button(f"🔓 AÇ: Raf {sid}", key=f"open_{sid}", use_container_width=True): send_open_command(mid, sid)

    with tab3:
        st.subheader("📊 Satış Analizi")
        df = get_sales_history(mid)
        if df is not None and not df.empty and 'Tarih' in df.columns:
            now = datetime.now()
            today = df[df['Tarih'] >= now.replace(hour=0, minute=0, second=0)]
            month = df[df['Tarih'] >= now.replace(day=1, hour=0, minute=0)]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Bugün Ciro", f"{today['Tutar'].sum()} ₺", f"{len(today)} Adet")
            c2.metric("Bu Ay Ciro", f"{month['Tutar'].sum()} ₺", f"{len(month)} Adet")
            c3.metric("Toplam Ciro", f"{df['Tutar'].sum()} ₺", f"{len(df)} Adet")
            c4.metric("Ortalama Sepet", f"{df['Tutar'].mean():.1f} ₺")
            st.divider()
            ch1, ch2 = st.columns(2)
            with ch1:
                st.markdown("##### 🕒 Saatlik Yoğunluk")
                df['Saat'] = df['Tarih'].dt.hour
                full_hours = pd.DataFrame({'Saat': range(24)})
                hourly = df.groupby('Saat').size().reset_index(name='Adet')
                final_h = pd.merge(full_hours, hourly, on='Saat', how='left').fillna(0)
                st.plotly_chart(px.bar(final_h, x='Saat', y='Adet', text='Adet', color_discrete_sequence=['#D9007E']), use_container_width=True)
            with ch2:
                st.markdown("##### 🏆 En Çok Satan Ürünler")
                top = df['Ürün'].value_counts().reset_index()
                top.columns = ['Ürün', 'Adet']
                fig_top = px.bar(top, x='Ürün', y='Adet', text='Adet', color_discrete_sequence=['#2ECC71'])
                fig_top.update_layout(xaxis_title="Ürün Adı", yaxis_title="Satış Adedi")
                st.plotly_chart(fig_top, use_container_width=True)
            st.dataframe(df[['Tarih', 'Kutu', 'Ürün', 'Tutar', 'Durum']], use_container_width=True, hide_index=True)
        else: st.info("Henüz satış verisi yok.")

    with tab4:
        st.subheader("🌷 Akıllı Dolum Asistanı")
        c_left, c_right = st.columns([1, 1])
        with c_left:
            sel_slot = st.selectbox("Hangi Kutuyu Dolduruyorsun?", sorted_ids, key="restock_select")
            curr = slots[sel_slot]
            st.write(f"**Durum:** {curr.get('price')} TL - {'Aktif' if curr.get('enabled') else 'Pasif'}")
            n_name = st.text_input("Çiçek Adı", value=curr.get('product_name', ''))
            n_price = st.number_input("Satış Fiyatı", value=int(curr.get('price', 0)), key="n_price")
        with c_right:
            st.write("📸 **Ürün Fotoğrafı**")
            method = st.radio("Yükleme", ["Kamera (Mobil)", "Dosya (PC)"], horizontal=True)
            img_file = None
            if method == "Kamera (Mobil)":
                if st.checkbox("Kamerayı Başlat 📷"): img_file = st.camera_input("Çek")
            else: img_file = st.file_uploader("Seç", type=['jpg', 'png', 'jpeg'])
        st.divider()
        if st.button("🚀 DOLUMU TAMAMLA VE KAYDET", type="primary", use_container_width=True):
            if not n_name: st.error("Çiçek adı giriniz.")
            else:
                with st.spinner("Yükleniyor..."):
                    url = None
                    if img_file:
                        url = upload_image_to_firebase(img_file, mid, sel_slot)
                    else:
                        url = curr.get('image_url')
                        
                    update_product_info(mid, sel_slot, n_name, n_price, url)
                st.success("Başarılı!"); time.sleep(1); st.rerun()

# ====================================================================
# 6. AKIŞ
# ====================================================================
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False

if not st.session_state['logged_in']: login_page()
elif 'selected_machine' in st.session_state: manage_machine_page()
else: dashboard_page()
