import streamlit as st
import streamlit.components.v1 as components
import os

# ===== Page Config =====
st.set_page_config(
    page_title="Myanmar Movie Recap AI",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ===== Hide Streamlit Branding + Full Width =====
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
.stApp { background: #0b0f19; }
.block-container { 
    padding: 0 !important; 
    max-width: 100% !important; 
}
iframe { 
    border: none; 
    width: 100%;
}
</style>
""", unsafe_allow_html=True)

# ===== Load HTML App =====
HTML_FILE = "app.html"

if not os.path.exists(HTML_FILE):
    st.error("❌ app.html ဖိုင် မတွေ့ဘူး — Repo ထဲ ထည့်ပါ")
    st.info("📌 Repo Root မှာ — `app.html` — ဆိုတဲ့ ဖိုင် ဖန်တီးပြီး — HTML Code Paste လုပ်ပါ")
else:
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    # Full Height Embed
    components.html(html_content, height=1400, scrolling=True)