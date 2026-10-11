import streamlit as st
import os, re, hashlib, ffmpeg, shutil, subprocess, asyncio, time
import concurrent.futures
import numpy as np
import edge_tts
from PIL import Image, ImageDraw, ImageFont
import cv2
from gradio_client import Client, handle_file

os.environ["HF_HOME"] = "/tmp/hf_cache"

SPACES = [
    {"space": "openbmb/VoxCPM-Demo", "type": "demo"},
    {"space": "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts", "type": "burmese"},
]

PASSWORD = "voxcpm2026"
FONT_FILE = "MyanmarPadaung.ttf"

FS, BH, BA = 30, 100, 200
ENC_PRESET = "fast"
ENC_CRF = 18
FINAL_PRESET = "ultrafast"
FINAL_CRF = 20
USE_FAST_VAD = True
AUDIO_BITRATE = "128k"
TTS_CHUNK = 300      # သေးလေ subtitle sync ပိုတိကျ
EDGE_CHUNK = 200
TTS_WORKERS = 3
PNG_WORKERS = 4

CANVAS_URL = "https://gemini.google.com/share/a96d9ba3e76e"
WHISPER_MODEL = "tiny"
WHISPER_LANG = "my"

BOX_WIDTH_RATIO = 1.0
PADDING_Y = 15
CORNER_RADIUS = 20

EDGE_VOICES = {
    "female": "my-MM-NilarNeural",
    "male":   "my-MM-ThihaNeural",
}
EDGE_VOICE_FIXED = "male"

st.set_page_config(page_title="Myanmar TTS Recap", page_icon="🎬", layout="centered")


def _on_download_clear():
    """Download နှိပ်ရင် Script + Video + Paste ရှင်း (Ref Audio မထိ)"""
    st.session_state.script = ""
    st.session_state.last_paste = ""
    st.session_state.paste_big = ""
    st.session_state.pkey = None
    st.session_state.video_up_key = st.session_state.get("video_up_key", 0) + 1


def _on_paste():
    """Paste box ထဲ စာဝင်တာနဲ့ Script ထဲ ကူး (callback ထဲမှာမို့ widget value ပြင်လို့ရ)"""
    clean = (st.session_state.get("paste_big", "") or "").strip()
    if not clean:
        return
    lines = [l.strip().strip('"\u201c\u201d').strip() for l in clean.splitlines()]
    result = "\n\n".join(l for l in lines if l)
    st.session_state.script = result
    st.session_state.last_paste = clean
    st.session_state.paste_big = ""
    st.session_state.paste_msg = f"✅ Script ထဲ Auto ရောက်သွားပြီ — {len(result)} လုံး"


st.markdown("""
<style>
.stApp{background:linear-gradient(160deg,#0f0f23,#1a1a35,#0f0f23);color:#e8e8f0}
#MainMenu,footer,header{visibility:hidden}
.main-title{text-align:center;font-size:2.2rem;font-weight:900;
 background:linear-gradient(90deg,#ff6b9d,#c66bff,#6ba8ff);
 -webkit-background-clip:text;-webkit-text-fill-color:transparent;
 background-clip:text;margin-bottom:4px}
.main-sub{text-align:center;color:#8888aa;font-size:.9rem;margin-bottom:20px}
.stButton>button{background:linear-gradient(135deg,#667eea,#764ba2)!important;
 color:#fff!important;border:none!important;border-radius:10px!important;
 padding:12px 20px!important;font-weight:600!important;
 box-shadow:0 4px 15px rgba(102,126,234,.3)!important}
.stTextArea textarea{background:rgba(255,255,255,.04)!important;
 border:1px solid rgba(255,255,255,.1)!important;color:#fff!important;
 border-radius:10px!important}
.stFileUploader{background:rgba(255,255,255,.02);border-radius:10px;padding:8px}
.stAlert{border-radius:10px!important;border:none!important}
hr{border-color:rgba(255,255,255,.08);margin:24px 0}
.timer-box{background:linear-gradient(135deg,#667eea,#764ba2);
 border-radius:16px;padding:24px;text-align:center;margin:15px 0;
 box-shadow:0 8px 30px rgba(102,126,234,.4)}
.timer-title{color:#fff;font-size:.9rem;font-weight:600;letter-spacing:1px;margin-bottom:8px}
.timer-value{color:#fff;font-size:3.2rem;font-weight:900;line-height:1}
.timer-unit{font-size:1.5rem;font-weight:700;margin-left:8px}
.step-timer{background:rgba(255,255,255,.05);border-left:4px solid #667eea;
 border-radius:10px;padding:12px 18px;margin:8px 0;color:#e8e8f0;font-size:.95rem}
.step-timer b{color:#6ba8ff;font-size:1.05rem}
</style>
""", unsafe_allow_html=True)

if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.markdown("<div class='main-title'>🔐 Private App</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        pwd = st.text_input("Password", type="password", label_visibility="collapsed", placeholder="Password")
        if st.button("Login", use_container_width=True):
            if pwd == PASSWORD:
                st.session_state.auth = True
                st.rerun()
            else:
                st.error("Password မှား")
    st.stop()


# ==================== Session State Init ====================
if "script" not in st.session_state: st.session_state.script = ""
if "video_up_key" not in st.session_state: st.session_state.video_up_key = 0
if "paste_big" not in st.session_state: st.session_state.paste_big = ""


# ==================== Utility Functions ====================

def vid_info(p):
    pr = ffmpeg.probe(p)
    v = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    return int(v['width']), int(v['height']), float(pr['format']['duration'])


def audio_dur(p):
    return float(ffmpeg.probe(p)['format']['duration'])


def t2s(s):
    ms = int(round((s - int(s)) * 1000)); tot = int(s)
    if ms >= 1000: tot += 1; ms = 0
    h, r = divmod(tot, 3600); m, sec = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def s2t(ts):
    ts = ts.strip()
    m = re.match(r'^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$', ts)
    if m:
        h, mi, se, ms = m.groups()
        return int(h)*3600 + int(mi)*60 + int(se) + int(ms.ljust(3,'0'))/1000
    return None


# ==================== Cartoon Styles ====================
CARTOON_WORK_W = 560   # filter လုပ်မယ့် အကျယ် (မြန်ဖို့ downscale → ပြန် upscale)
_PAT = {}


def _halftone_pat(h, w, cell):
    key = (h, w, cell)
    p = _PAT.get(key)
    if p is None:
        if len(_PAT) > 6: _PAT.clear()
        g = np.mgrid[0:h, 0:w].astype(np.float32)
        k = 2.0 * np.pi / cell
        p = ((np.cos(g[1] * k) + np.cos(g[0] * k)) / 4.0 + 0.5).astype(np.float32)
        _PAT[key] = p
    return p


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _edges(img, blur=5, block=9, c=7):
    g = cv2.medianBlur(_gray(img), blur)
    return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                 cv2.THRESH_BINARY, block, c)


def _smooth(img, n=2, d=7, sc=45, ss=7):
    for _ in range(n):
        img = cv2.bilateralFilter(img, d, sc, ss)
    return img


def _quant(img, levels):
    step = max(1, 256 // levels)
    q = (img.astype(np.int16) // step) * step + step // 2
    return np.clip(q, 0, 255).astype(np.uint8)


def _sat(img, s=1.3, v=1.0):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= s
    hsv[..., 2] *= v
    return cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)


def _with_edges(color, edges):
    return cv2.bitwise_and(color, color, mask=edges)


def _g2b(g):
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def st_classic(img):
    c = _quant(_smooth(img, 3), 10)
    return _with_edges(_sat(c, 1.2), _edges(img, 7, 9, 7))


def st_anime(img):
    c = _quant(_smooth(img, 2, 7, 40, 7), 18)
    c = _sat(c, 1.25, 1.05)
    return _with_edges(c, _edges(img, 5, 11, 9))


def st_cel(img):
    s = _smooth(img, 2)
    hsv = cv2.cvtColor(s, cv2.COLOR_BGR2HSV)
    hsv[..., 2] = _quant(hsv[..., 2], 4)
    hsv[..., 1] = _quant(hsv[..., 1], 6)
    c = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return _with_edges(c, _edges(img, 7, 7, 5))


def st_comic(img):
    c = _sat(_quant(_smooth(img, 2), 5), 1.6)
    h, w = c.shape[:2]
    dark = 1.0 - _gray(img).astype(np.float32) / 255.0
    ink = (_halftone_pat(h, w, 6) > (1.0 - dark)) & (dark > 0.35)
    c = c.copy()
    c[ink] = (c[ink] * 0.55).astype(np.uint8)
    return _with_edges(c, _edges(img, 7, 7, 5))


def st_popart(img):
    g = _quant(_gray(cv2.bilateralFilter(img, 7, 50, 7)), 4)
    g = cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX)
    c = cv2.applyColorMap(g, cv2.COLORMAP_PLASMA)
    return _with_edges(c, _edges(img, 7, 9, 8))


def st_manga(img):
    g = _gray(img)
    g = cv2.convertScaleAbs(g, alpha=1.25, beta=-15)
    h, w = g.shape
    dark = 1.0 - g.astype(np.float32) / 255.0
    ink = _halftone_pat(h, w, 5) > (1.0 - dark)
    out = np.where(ink, 0, 255).astype(np.uint8)
    out = cv2.bitwise_and(out, _edges(img, 5, 9, 8))
    return _g2b(out)


def st_ink(img):
    g = cv2.medianBlur(_gray(img), 7)
    out = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 11, 6)
    return _g2b(out)


def st_sketch(img):
    g = _gray(img)
    inv = 255 - g
    b = cv2.GaussianBlur(inv, (0, 0), 9)
    return _g2b(cv2.divide(g, 255 - b, scale=256))


def st_colorpencil(img):
    sk = st_sketch(img).astype(np.float32) / 255.0
    col = _sat(_smooth(img, 2), 1.1).astype(np.float32)
    out = col * (0.35 + 0.65 * sk)
    out = out * 0.6 + 255.0 * 0.4 * sk
    return np.clip(out, 0, 255).astype(np.uint8)


def st_watercolor(img):
    c = _smooth(img, 3, 9, 60, 9)
    c = cv2.medianBlur(c, 7)
    c = cv2.GaussianBlur(c, (0, 0), 1.2)
    c = _sat(c, 1.15, 1.05)
    paper = cv2.GaussianBlur(np.random.normal(0, 6, c.shape).astype(np.float32), (0, 0), 1.0)
    c = c.astype(np.float32) * 0.9 + 255.0 * 0.1 + paper
    soft = cv2.GaussianBlur(_gray(img), (0, 0), 1.0)
    edge = cv2.Canny(soft, 40, 110).astype(np.float32)[..., None] / 255.0
    c = c * (1.0 - 0.25 * edge)
    return np.clip(c, 0, 255).astype(np.uint8)


def st_oil(img):
    c = cv2.edgePreservingFilter(img, flags=1, sigma_s=40, sigma_r=0.3)
    c = cv2.medianBlur(c, 5)
    return _sat(_quant(c, 22), 1.25)


def st_pastel(img):
    c = _smooth(img, 3)
    c = _sat(c, 0.75)
    c = cv2.addWeighted(c, 0.75, np.full_like(c, 255), 0.25, 0)
    e = _edges(img, 7, 9, 7)
    soft = cv2.addWeighted(c, 1.0, _g2b(255 - e), -0.35, 0)
    return np.clip(soft, 0, 255).astype(np.uint8)


def st_neon(img):
    g = cv2.GaussianBlur(_gray(img), (5, 5), 0)
    e = cv2.dilate(cv2.Canny(g, 60, 140), np.ones((2, 2), np.uint8))
    col = _sat(_smooth(img, 1), 2.2, 1.4)
    line = cv2.bitwise_and(col, col, mask=e)
    glow = cv2.GaussianBlur(line, (0, 0), 5)
    out = cv2.addWeighted(line, 1.0, glow, 1.6, 0)
    return np.clip(out, 0, 255).astype(np.uint8)


def st_chalk(img):
    g = cv2.GaussianBlur(_gray(img), (5, 5), 0)
    e = cv2.dilate(cv2.Canny(g, 50, 130), np.ones((2, 2), np.uint8))
    bg = np.full_like(img, (52, 62, 40))
    soft = _g2b(cv2.GaussianBlur(_gray(img), (0, 0), 3))
    bg = cv2.addWeighted(bg, 0.8, soft, 0.2, 0)
    bg[e > 0] = (240, 240, 235)
    return bg


def st_pixel(img):
    h, w = img.shape[:2]
    sw = max(24, w // 8)
    sh = max(16, int(h * sw / w))
    small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
    small = _sat(_quant(small, 8), 1.3)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def st_retro(img):
    c = st_classic(img)
    k = np.array([[0.131, 0.534, 0.272],
                  [0.168, 0.686, 0.349],
                  [0.189, 0.769, 0.393]], np.float32)
    c = cv2.transform(c, k)
    noise = np.random.normal(0, 10, c.shape).astype(np.float32)
    return np.clip(c.astype(np.float32) + noise, 0, 255).astype(np.uint8)


CARTOON_STYLES = [
    ("none",        "⚪ မူရင်း (Cartoon မလုပ်)", None),
    ("classic",     "🎨 Classic Cartoon",        st_classic),
    ("anime",       "🌸 Anime",                  st_anime),
    ("cel",         "🧊 Cel Shading (Game)",     st_cel),
    ("comic",       "💥 Comic Book (Halftone)",  st_comic),
    ("popart",      "🟣 Pop Art",                st_popart),
    ("manga",       "📖 Manga B&W (Screentone)", st_manga),
    ("ink",         "🖋️ Ink Drawing",            st_ink),
    ("sketch",      "✏️ Pencil Sketch",          st_sketch),
    ("colorpencil", "🖍️ Color Pencil",           st_colorpencil),
    ("watercolor",  "💧 Watercolor",             st_watercolor),
    ("oil",         "🖼️ Oil Painting",           st_oil),
    ("pastel",      "🍬 Pastel Soft",            st_pastel),
    ("neon",        "🌈 Neon Outline",           st_neon),
    ("chalk",       "🧑‍🏫 Chalkboard",             st_chalk),
    ("pixel",       "👾 Pixel Art",              st_pixel),
    ("retro",       "📺 Retro Toon (Sepia)",     st_retro),
]
CARTOON_MAP = {k: f for k, _, f in CARTOON_STYLES}
CARTOON_LABELS = {k: l for k, l, _ in CARTOON_STYLES}


def apply_cartoon(frame, key):
    """BGR uint8 frame → Cartoon BGR uint8 frame (shape အတူတူ၊ error ဖြစ်ရင် original ပြန်)"""
    fn = CARTOON_MAP.get(key)
    if fn is None:
        return frame
    try:
        h, w = frame.shape[:2]
        sc = min(1.0, CARTOON_WORK_W / float(w))
        small = frame
        if sc < 1.0:
            small = cv2.resize(frame, (max(8, int(w * sc)), max(8, int(h * sc))),
                               interpolation=cv2.INTER_AREA)
        small = np.ascontiguousarray(small)
        out = fn(small)
        if out.ndim == 2:
            out = _g2b(out)
        if out.shape[0] != h or out.shape[1] != w:
            out = cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.ascontiguousarray(out.astype(np.uint8))
    except Exception:
        return frame



def render_png(text, out, fp, W, H, fs=30, pos_y=100, bh=100, ba=100,
               box_width_ratio=BOX_WIDTH_RATIO,
               padding_y=PADDING_Y, corner_radius=CORNER_RADIUS):
    img = Image.new("RGBA", (W, H), (0,0,0,0))
    d = ImageDraw.Draw(img)
    try: f = ImageFont.truetype(fp, fs)
    except: f = ImageFont.load_default()

    mc = max(15, int(W/(fs*0.9)))
    lines, cur = [], ""
    for w in text.split():
        if len(cur)+len(w)+1 <= mc: cur = cur+" "+w if cur else w
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    if not lines: return out

    lh = int(fs * 1.3)
    text_h = len(lines) * lh
    box_w = int(W * box_width_ratio)
    box_h = text_h + padding_y * 2
    box_x = (W - box_w) // 2
    max_y = H - box_h
    box_y = int((pos_y / 100) * max_y)
    box_y = max(0, min(box_y, max_y))

    d.rounded_rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        radius=corner_radius, fill=(0, 0, 0, ba)
    )

    ty = box_y + padding_y
    for ln in lines:
        bb = d.textbbox((0, 0), ln, font=f)
        lw = bb[2] - bb[0]
        lx = box_x + (box_w - lw) // 2
        for dx in [-2,-1,0,1,2]:
            for dy in [-2,-1,0,1,2]:
                d.text((lx+dx, ty+dy), ln, font=f, fill=(0,0,0,255))
        d.text((lx, ty), ln, font=f, fill=(255,255,255,255))
        ty += lh

    img.save(out, "PNG")
    return out


def split_for_sub(text, mc=30):
    parts = []
    sents = [s.strip() + "။" for s in text.replace("။", "။|").split("|") if s.strip()]
    for s in sents:
        s = s.replace("။။", "။")
        if len(s) <= mc:
            parts.append(s); continue
        cur = ""
        for w in s.split():
            if len(cur) + len(w) + 1 <= mc: cur = cur + " " + w if cur else w
            else:
                if cur: parts.append(cur.strip())
                cur = w
        if cur: parts.append(cur.strip())
    return parts


def scr_to_srt_timed(pieces, total, path, mc=30):
    """pieces = [(text, audio_seconds)] — chunk တစ်ခုချင်းစီမှာ အချိန် reset ဖြစ်လို့ drift မရှိ"""
    raw = sum(d for _, d in pieces)
    if raw <= 0: return None
    k = total / raw
    cur, idx, wrote = 0.0, 1, False
    with open(path, "w", encoding="utf-8") as f:
        for text, d in pieces:
            parts = split_for_sub(text, mc)
            d *= k
            if not parts:
                cur += d; continue
            tot = sum(len(p) for p in parts)
            for p in parts:
                dd = len(p) / tot * d
                f.write(f"{idx}\n{t2s(cur)} --> {t2s(cur+dd)}\n{p}\n\n")
                cur += dd; idx += 1; wrote = True
    return path if wrote else None


def parse_srt(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().replace("\r\n","\n").replace("\r","\n")
    segs = []
    for ck in re.split(r"\n\s*\n", raw.strip()):
        ls = [l for l in ck.split("\n") if l.strip()]
        if len(ls) < 3: continue
        ts = next((l for l in ls if "-->" in l), None)
        if not ts: continue
        p = re.split(r"\s*-->\s*", ts)
        if len(p) != 2: continue
        a, b = s2t(p[0]), s2t(p[1])
        if a is None or b is None: continue
        idx = ls.index(ts); txt = " ".join(ls[idx+1:]).strip()
        if txt: segs.append({"start": a, "end": b, "text": txt})
    return segs


def normalize_script(t):
    t = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]', '', t)
    t = re.sub(r'[\u201c\u201d"`*_#<>\[\]{}()\uff08\uff09\u300c\u300d\u300e\u300f\u00ab\u00bb~^|\\/]', ' ', t)
    out = []
    for ln in t.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln: continue
        if not ln.endswith(("။", "၊", "!", "?")): ln += "။"
        out.append(ln)
    t = " ".join(out)
    t = re.sub(r"။(\s*။)+", "။", t)
    return t.strip()


def has_speech(t):
    return re.search(r"[\u1000-\u1049\u1050-\u109F\w]", t) is not None


def split_scr(t, mc=TTS_CHUNK):
    sents = []
    for p in t.replace("။","။|").split("|"):
        p = p.strip()
        if not p: continue
        sents.append(p if p.endswith("။") else p + "။")
    out, cur = [], ""
    for s in sents:
        if len(cur)+len(s) <= mc: cur += s
        else:
            if cur: out.append(cur)
            if len(s) > mc:
                for i in range(0, len(s), mc): out.append(s[i:i+mc])
                cur = ""
            else: cur = s
    if cur: out.append(cur)
    return out


def keep_count(video_in, segments):
    pr = ffmpeg.probe(video_in)
    vs = next(x for x in pr['streams'] if x['codec_type'] == 'video')
    n_, d_ = vs['r_frame_rate'].split('/')
    fps = float(n_) / float(d_)
    total = int(round(float(pr['format']['duration']) * fps))
    t = np.arange(total) / fps
    mask = np.zeros(total, dtype=bool)
    for a, b in segments:
        mask |= (t >= a) & (t <= b)
    return int(mask.sum()), fps


def simple_merge(video_in, audio_in, output_video, tempo, segments=None):
    vf = []
    if segments:
        sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segments)
        vf = ["-vf", f"select='{sel}',setpts=N/FRAME_RATE/TB"]
    cmd = ["ffmpeg", "-y", "-i", video_in, "-i", audio_in,
           "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a"] + vf + [
           "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
           "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest", output_video]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")
    return output_video


def final_render(video_in, audio_in, output_video, tempo,
                 srt_path=None, fp=FONT_FILE, fs=FS, pos_y=100, bh=BH, ba=BA,
                 use_neon=True, thickness=15, speed=0.4, tail=0.5,
                 crop_ratio=0.95, mirror=True, segments=None, cartoon="none"):
    W0, H0, _ = vid_info(video_in)
    pr = ffmpeg.probe(video_in)
    vs = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    n, d = vs['r_frame_rate'].split('/')
    fps = float(n) / float(d)

    if crop_ratio != 1.0:
        cw = int(W0 * crop_ratio); ch = int(H0 * crop_ratio)
        if cw % 2: cw -= 1
        if ch % 2: ch -= 1
        cx = (W0 - cw) // 2; cy = (H0 - ch) // 2
    else:
        cw, ch, cx, cy = W0, H0, 0, 0
    W, H = cw, ch

    subs = []
    if srt_path:
        segs = parse_srt(srt_path)
        os.makedirs("subtitle_pngs", exist_ok=True)

        def prep(args):
            i, sg = args
            p = f"subtitle_pngs/s_{i:04d}.png"
            render_png(sg["text"], p, fp, W, H, fs, pos_y, bh, ba,
                       box_width_ratio=BOX_WIDTH_RATIO)
            rgba = cv2.imread(p, cv2.IMREAD_UNCHANGED)
            try: os.remove(p)
            except: pass
            if rgba is None: return None
            rows = np.where(rgba[:, :, 3].any(axis=1))[0]
            if len(rows) == 0: return None
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
            crop = rgba[y0:y1]
            alpha = crop[:, :, 3:4].astype(np.float32) / 255.0
            pre = crop[:, :, :3].astype(np.float32) * alpha
            return {"a": sg["start"], "b": sg["end"], "y0": y0, "y1": y1,
                    "inv": 1.0 - alpha, "pre": pre}

        with concurrent.futures.ThreadPoolExecutor(max_workers=PNG_WORKERS) as ex:
            subs = [x for x in ex.map(prep, enumerate(segs)) if x]

    if use_neon:
        th = thickness
        P = 2 * (W + H)
        yy, xx = np.mgrid[0:H, 0:W]
        ring = (xx < th) | (xx >= W - th) | (yy < th) | (yy >= H - th)
        ys, xs = np.nonzero(ring)
        dt, db, dl, dr = ys, H - 1 - ys, xs, W - 1 - xs
        m = np.minimum.reduce([dt, db, dl, dr])
        S = np.where(m == dt, xs,
            np.where(m == dr, W + ys,
            np.where(m == db, 2 * W + H + (W - xs),
                     2 * W + 2 * H - ys))).astype(np.float32)
        BASE = np.array([12, 8, 10], np.float32)
        CYAN = np.array([255, 255, 0], np.float32)
        MAGENTA = np.array([110, 30, 255], np.float32)
        IDLE = 0.14

        Si = (S.astype(np.int64)) % P
        P_arr = np.arange(P, dtype=np.float32)

        def layer(h, color):
            dist = (h - P_arr) % P
            a_ = np.clip(1.0 - dist / (tail * P), 0, 1)
            glow = IDLE + (1.0 - IDLE) * (a_ ** 0.55)
            core = np.clip(1.0 - dist / (0.06 * P), 0, 1) ** 2
            return color * glow[:, None] + 255.0 * 0.9 * core[:, None]

        def make_strip(head):
            c_ = BASE + layer(head, CYAN) + layer(head + P / 2, MAGENTA)
            return np.clip(c_, 0, 255).astype(np.uint8)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
    ]
    if audio_in:
        cmd += ["-i", audio_in, "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", str(FINAL_CRF), "-preset", FINAL_PRESET,
            "-pix_fmt", "yuv420p", "-threads", "0"]
    if audio_in:
        cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest"]
    cmd += [output_video]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    import threading, queue
    q_in = queue.Queue(maxsize=8)
    q_out = queue.Queue(maxsize=8)
    stop = threading.Event()
    err = []

    def qput(q, item):
        while not stop.is_set():
            try:
                q.put(item, timeout=0.5); return True
            except queue.Full:
                pass
        return False

    def qget(q):
        while True:
            try:
                return q.get(timeout=0.5)
            except queue.Empty:
                if stop.is_set(): return None

    def reader():          # Decode thread
        cap = cv2.VideoCapture(video_in)
        i = 0; p = 0
        try:
            while not stop.is_set():
                if not cap.grab(): break
                t_in = i / fps
                i += 1
                if segments:
                    while p < len(segments) and segments[p][1] < t_in: p += 1
                    if not (p < len(segments) and segments[p][0] <= t_in): continue
                ok, fr = cap.retrieve()
                if not ok: break
                if not qput(q_in, fr): break
        except Exception as e:
            err.append(e); stop.set()
        finally:
            cap.release()
            qput(q_in, None)

    def writer():          # ffmpeg pipe write thread
        try:
            while True:
                bts = qget(q_out)
                if bts is None: break
                proc.stdin.write(bts)
        except Exception as e:
            err.append(e); stop.set()

    th_r = threading.Thread(target=reader, daemon=True)
    th_w = threading.Thread(target=writer, daemon=True)
    th_r.start(); th_w.start()

    use_cartoon = bool(cartoon) and cartoon != "none" and cartoon in CARTOON_MAP

    j = 0; k = 0
    try:
        while True:
            fr = qget(q_in)
            if fr is None: break
            t = j / fps
            fr = fr[cy:cy + ch, cx:cx + cw]
            if mirror: fr = fr[:, ::-1]
            fr = np.ascontiguousarray(fr)

            if use_cartoon:
                fr = apply_cartoon(fr, cartoon)      # Cartoon filter (neon/subtitle မတိုင်ခင်)

            if use_neon:
                head = (t * speed * P / 4.0) % P
                fr[ys, xs] = make_strip(head)[Si]

            while k < len(subs) and subs[k]["b"] < t: k += 1
            if k < len(subs) and subs[k]["a"] <= t:
                sb = subs[k]
                reg = fr[sb["y0"]:sb["y1"]].astype(np.float32) * sb["inv"] + sb["pre"]
                fr[sb["y0"]:sb["y1"]] = reg.astype(np.uint8)

            if not qput(q_out, fr.tobytes()): break
            j += 1
    except Exception as e:
        err.append(e); stop.set()
    finally:
        if not stop.is_set(): qput(q_out, None)
        th_w.join()
        stop.set()
        th_r.join()
        try: proc.stdin.close()
        except: pass
        proc.wait()
    if err: raise err[0]
    if proc.returncode != 0:
        raise Exception("Final render: ffmpeg fail")
    return output_video


def mux_audio(video_in, audio_in, output_video, tempo):
    cmd = ["ffmpeg", "-y", "-i", video_in, "-i", audio_in,
           "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
           "-shortest", output_video]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"Mux: {(r.stderr or '')[-300:]}")
    return output_video


def prepare_video_job(video_in, fw_model):
    """Whisper/VAD နဲ့ speech segments ပဲ ရှာ (TTS နဲ့ တပြိုင်နက် run)"""
    segments = whisper_fast(video_in, fw_model)
    if not segments: raise Exception("Speech မတွေ့")
    kept, vfps = keep_count(video_in, segments)
    if kept == 0: raise Exception("Speech မတွေ့")
    return {"segments": segments, "vdur": kept / vfps}


# ==================== Preview ====================

def draw_tiktok_border_preview(img, thickness=30, animated_phase=0.0):
    import math
    d = ImageDraw.Draw(img, "RGBA")
    W, H = img.size
    inner_c = max(3, thickness // 3)

    d.rectangle([0, 0, W - 1, H - 1], outline=(0, 0, 0, 255), width=thickness)

    if animated_phase > 0:
        phase = (math.sin(animated_phase * math.pi) + 1) / 2
        cyan_r = int(37 * (1 - phase) + 254 * phase)
        cyan_g = int(244 * (1 - phase) + 44 * phase)
        cyan_b = int(238 * (1 - phase) + 85 * phase)
        d.rectangle([0, 0, W - 1, H - 1],
                    outline=(cyan_r, cyan_g, cyan_b, 255), width=inner_c)
    else:
        d.rectangle([0, 0, W - 1, H - 1],
                    outline=(37, 244, 238, 255), width=inner_c)

    offset = thickness
    d.rectangle([offset, offset, W - 1 - offset, H - 1 - offset],
                outline=(254, 44, 85, 255), width=inner_c)
    return img


# ==================== TTS Functions ====================

def tts_demo(chunks, ref, space, cb=None):
    cl = Client(space); files = []; rf = handle_file(ref) if ref else None
    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks), c)
        res = cl.predict(
            text_input=c,
            control_instruction="A warm young woman, calm and expressive",
            reference_wav_path_input=rf,
            use_prompt_text=False, prompt_text_input="",
            cfg_value_input=2.0, do_normalize=True, denoise=False,
            api_name="/generate"
        )
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = f"chunk_{i}.wav"; shutil.copy(p, dst); files.append(dst)
    return files


def tts_burmese(chunks, ref, space, cb=None):
    cl = Client(space); files = []
    if not ref: raise Exception("Reference Audio needed")
    rf = handle_file(ref)
    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks), c)
        res = cl.predict(
            target_text=c, ref_audio=rf,
            ref_text="မြန်မာ အသံနမူနာ", cfg_value=2.0,
            inference_timesteps=10, api_name="/tts"
        )
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = f"chunk_b_{i}.wav"; shutil.copy(p, dst); files.append(dst)
    return files


async def _edge_tts_async(text, out_file, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_file)


def edge_tts_run(chunks, out_path, cb=None, workers=TTS_WORKERS):
    voice_id = EDGE_VOICES[EDGE_VOICE_FIXED]

    small = []
    for c in chunks:
        for x in split_scr(c, EDGE_CHUNK):
            if has_speech(x): small.append(x)
    chunks = small
    if not chunks: raise Exception("TTS လုပ်စရာ စာမတွေ့ပါ")

    def run_async(coro_fn):
        try: asyncio.run(coro_fn())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro_fn())
            loop.close()

    def tts_one(args):
        i, c = args
        os.makedirs("tts_cache", exist_ok=True)
        key = hashlib.md5((voice_id + c).encode("utf-8")).hexdigest()
        dst = f"tts_cache/{key}.mp3"
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            return (i, dst)
        for attempt in range(4):
            try:
                if os.path.exists(dst): os.remove(dst)
                run_async(lambda: _edge_tts_async(c, dst, voice_id))
                if os.path.exists(dst) and os.path.getsize(dst) > 0:
                    time.sleep(0.3)
                    return (i, dst)
            except Exception:
                pass
            time.sleep(3 * (attempt + 1))
        return (i, None)

    results = [None] * len(chunks); done = 0; skipped = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for idx, dst in ex.map(tts_one, enumerate(chunks)):
            results[idx] = dst; done += 1
            if dst is None: skipped.append(idx)
            if cb: cb(done - 1, len(chunks), chunks[idx])

    good = [a for a in results if a]
    if not good:
        raise Exception("Edge TTS အသံမရပါ — edge-tts ကို update လုပ်ပါ (edge-tts>=7.0.0) / ခဏနေ ပြန်စမ်းပါ")
    if skipped:
        st.warning("⚠️ ကျော်လိုက်တဲ့ အပိုင်း: " + " | ".join(chunks[i][:30] for i in skipped))

    # chunk တစ်ခုချင်းရဲ့ အမှန်တကယ် အသံအရှည်
    pieces = [(chunks[i], audio_dur(a)) for i, a in enumerate(results) if a]

    with open("edge_concat.txt", "w", encoding="utf-8") as f:
        for a in good: f.write(f"file '{a}'\n")

    ffmpeg.input("edge_concat.txt", format="concat", safe=0).output(
        out_path, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
    ).run(overwrite_output=True)
    return out_path, pieces


def tts_free(chunks, out, cb=None):
    out, pieces = edge_tts_run(chunks, out, cb=cb)
    st.success("✅ Edge TTS — 👨 သီဟ (Thiha)")
    return out, pieces


def tts_all(text, out, ref=None, cb=None, use_voxcpm=True):
    chunks = split_scr(text, TTS_CHUNK)

    if not use_voxcpm:
        return tts_free(chunks, out, cb=cb)

    files = None
    for s in SPACES:
        try:
            st.info(f"🎙️ VoxCPM2 — {s['space']} — စမ်းနေသည်...")
            if s["type"] == "demo":
                files = tts_demo(chunks, ref, s["space"], cb)
            else:
                files = tts_burmese(chunks, ref, s["space"], cb)
            st.success("✅ VoxCPM2 — အောင်မြင်")
            break
        except Exception as e:
            st.warning(f"⚠️ VoxCPM2 — Fail: {str(e)[:80]}")
            files = None
            continue

    if files is None:
        st.warning("⚠️ VoxCPM2 — Busy/Fail — Edge TTS သီဟ Auto")
        return tts_free(chunks, out, cb=cb)

    pieces = [(chunks[i], audio_dur(f)) for i, f in enumerate(files)]

    with open("concat.txt", "w", encoding="utf-8") as f:
        for a in files: f.write(f"file '{a}'\n")
    ffmpeg.input("concat.txt", format="concat", safe=0).output(
        out, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
    ).run(overwrite_output=True)
    return out, pieces


@st.cache_resource(show_spinner=False)
def get_fw_model():
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                        cpu_threads=os.cpu_count() or 4)


def whisper_fast(video_path, model=None):
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", "whisper_audio.wav"
    ], capture_output=True, check=True)

    if USE_FAST_VAD:
        try:
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import get_speech_timestamps, VadOptions
            audio = decode_audio("whisper_audio.wav", sampling_rate=16000)
            ts = get_speech_timestamps(
                audio, VadOptions(min_silence_duration_ms=700, speech_pad_ms=200))
            segs_vad = [(t["start"] / 16000.0, t["end"] / 16000.0) for t in ts]
            if segs_vad:
                return segs_vad
        except Exception:
            pass

    speech_segments = []
    try:
        model = model or get_fw_model()
        segments, _ = model.transcribe(
            "whisper_audio.wav", language=WHISPER_LANG,
            vad_filter=False, beam_size=1,
            condition_on_previous_text=False, temperature=0
        )
        for seg in segments:
            speech_segments.append((seg.start, seg.end))
    except Exception:
        import whisper
        model = whisper.load_model(WHISPER_MODEL)
        result = model.transcribe(
            "whisper_audio.wav", language=WHISPER_LANG,
            condition_on_previous_text=False, beam_size=1, temperature=0
        )
        for seg in result["segments"]:
            speech_segments.append((seg["start"], seg["end"]))
    return speech_segments


# ==================== UI ====================

st.markdown("<div class='main-title'>🎬 Myanmar TTS Recap</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>Video → Script → VoxCPM2 / Edge TTS သီဟ → Cartoon Recap</div>", unsafe_allow_html=True)
st.divider()

with st.expander("📄 Transcript ထုတ်ယူမယ် (Gemini Canvas)", expanded=False):
    st.link_button("↗️ Canvas ကို Tab အသစ်မှာ ဖွင့်", CANVAS_URL, use_container_width=True)
    st.caption("Canvas ဖွင့် → Transcript Copy → အောက်က box ထဲ Paste လုပ်ပါ")

st.markdown("""
<div style="text-align:center;margin:8px 0 4px">
  <span style="font-size:1.1rem;font-weight:700;
    background:linear-gradient(90deg,#FF6B9D,#C66BFF,#6BA8FF);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;
    background-clip:text">
    📋  Paste Box
  </span>
</div>
""", unsafe_allow_html=True)

st.text_area(
    "paste",
    height=80,
    key="paste_big",
    on_change=_on_paste,
    label_visibility="collapsed",
    placeholder="📋  ဒီနေရာကို Long-press → Paste  (သို့)  Ctrl+V  —  ချက်ချင်း Script ထဲ ရောက်မယ်",
)

st.markdown("""
<style>
div[data-testid="stTextArea"]:has(textarea[aria-label="paste"]) textarea {
  background:linear-gradient(135deg,rgba(255,107,157,.12),rgba(198,107,255,.12))!important;
  border:2px dashed rgba(198,107,255,.55)!important;
  border-radius:18px!important;
  color:#fff!important;
  font-size:1.05rem!important;
  padding:22px!important;
  text-align:center!important;
}
div[data-testid="stTextArea"]:has(textarea[aria-label="paste"]) textarea:focus {
  border-color:#C66BFF!important;
  border-style:solid!important;
  box-shadow:0 0 24px rgba(198,107,255,.5)!important;
}
div[data-testid="stTextArea"]:has(textarea[aria-label="paste"]) textarea::placeholder {
  color:#b48cff!important;
  font-weight:600!important;
}
</style>
""", unsafe_allow_html=True)

_pm = st.session_state.pop("paste_msg", None)
if _pm:
    st.success(_pm)

script = st.text_area("Script", value=st.session_state.script, height=180,
                      label_visibility="collapsed", placeholder="မြန်မာ Script paste...")
st.session_state.script = script

c1, c2 = st.columns([3, 1])
with c1: st.caption(f"📝 {len(script):,}")
with c2:
    if st.button("🗑️ Clear", use_container_width=True):
        st.session_state.script = ""
        st.session_state.last_paste = ""
        st.rerun()
st.divider()

# ==================== Step 2 — Video ====================
st.subheader("📁 Step 2 — Video")
vid = st.file_uploader(
    "📹",
    type=["mp4","mov","avi","mkv"],
    label_visibility="collapsed",
    key=f"video_up_{st.session_state.video_up_key}"
)
if vid: st.success(f"✅ {vid.size/(1024*1024):.1f} MB")
st.divider()

use_neon = True
neon_animated = True
neon_thickness = 15
neon_speed = 0.4

# ==================== Step 3 — Cartoon Style ====================
st.subheader("🎨 Step 3 — Cartoon Style")
_style_keys = [k for k, _, _ in CARTOON_STYLES]
cartoon_key = st.selectbox(
    "Cartoon Style",
    _style_keys,
    index=_style_keys.index("classic"),
    format_func=lambda k: CARTOON_LABELS.get(k, k),
    label_visibility="collapsed",
    key="cartoon_style",
)
st.caption("💡 Step 5 Preview မှာ ရွေးထားတဲ့ Style ကို ကြိုကြည့်လို့ရ — Style အားလုံးကိုလည်း တစ်ပြိုင်နက်ကြည့်လို့ရ")
st.divider()

# ==================== Step 4 — Subtitle ====================
st.subheader("📝 Step 4 — Subtitle")
use_sub = st.toggle("Burn-in", value=True)
pos_y = 100
if use_sub:
    pos_y = st.slider("📍 Position", 0, 100, 100, 1)
st.divider()

# ==================== Step 5 — Preview ====================
if vid:
    st.subheader("🖼️ Step 5 — Preview")
    fr_full = None
    with st.spinner("Preview..."):
        pkey = (vid.name, vid.size)
        if st.session_state.get("pkey") != pkey or not os.path.exists("preview.mp4"):
            vid.seek(0)
            with open("preview.mp4", "wb") as f: f.write(vid.read())
            st.session_state.pkey = pkey
        W, H, _ = vid_info("preview.mp4")

        render_png("စာတန်းထိုး Preview", "prev.png", FONT_FILE, W, H, FS, pos_y, BH, BA,
                   box_width_ratio=BOX_WIDTH_RATIO)

        cap = cv2.VideoCapture("preview.mp4")
        nfr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if nfr > 8:
            cap.set(cv2.CAP_PROP_POS_FRAMES, nfr // 4)
        ok, fr = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, fr = cap.read()
        cap.release()

        if ok:
            fr_full = fr
            fr_c = apply_cartoon(fr, cartoon_key)       # Cartoon Preview
            bg = Image.fromarray(cv2.cvtColor(fr_c, cv2.COLOR_BGR2RGB)).convert("RGBA")
            fg = Image.open("prev.png").convert("RGBA")
            comp = Image.alpha_composite(bg, fg)

            if use_neon:
                phase = 0.5 if neon_animated else 0.0
                comp = draw_tiktok_border_preview(comp, thickness=neon_thickness,
                                                  animated_phase=phase)

            pw = 720
            comp.resize((pw, int(H * (pw / W))), Image.LANCZOS).convert("RGB").save("prev_out.png")
            st.image("prev_out.png", use_container_width=True)
            st.caption(f"🎨 {CARTOON_LABELS.get(cartoon_key, '')}")
            if use_neon and neon_animated:
                st.caption("🎬 Animated — Output Video မှာ အလင်းတန်း ပတ်ပြေးနေမည်")

    if fr_full is not None:
        if st.toggle("🖼️ Cartoon Style အားလုံး တစ်ပြိုင်နက် ကြည့်မယ်", value=False, key="gallery_on"):
            tw = 320
            th_ = max(16, int(fr_full.shape[0] * tw / fr_full.shape[1]))
            thumb = cv2.resize(fr_full, (tw, th_), interpolation=cv2.INTER_AREA)
            with st.spinner("Styles အားလုံး ပြင်နေ..."):
                gcols = st.columns(2)
                for n_i, (k_, l_, _f) in enumerate(CARTOON_STYLES):
                    o_ = apply_cartoon(thumb, k_)
                    gcols[n_i % 2].image(cv2.cvtColor(o_, cv2.COLOR_BGR2RGB),
                                         caption=l_, use_container_width=True)
st.divider()

# ==================== Step 6 — Generate ====================
st.subheader("🚀 Step 6 — Generate")

use_voxcpm = st.toggle("🎙️ VoxCPM2 သုံးမလား?", value=True)

if use_voxcpm:
    st.info("✅ VoxCPM2 သုံးမယ် — Fail/Busy ရင် — Edge TTS သီဟ Auto")
    ref = st.file_uploader("🎤 Ref Audio (VoxCPM2) — Optional",
                           type=["wav","mp3","m4a"], key="ref_up")
    if ref:
        with open("ref.wav", "wb") as f: f.write(ref.read())
        st.session_state.ref = "ref.wav"
        st.success("✅ Ref Audio")
    else:
        if os.path.exists("ref.wav"):
            st.session_state.ref = "ref.wav"
            st.caption("📎 Ref Audio (အရင် ထည့်ထားတာ ဆက်ရှိနေတယ်)")
        else:
            st.session_state.ref = None
else:
    st.info("⚡ Edge TTS သီဟ (Thiha) — ပဲ သုံးမယ်")
    st.session_state.ref = None

if st.button("✨ Generate Recap Video", type="primary", use_container_width=True):
    if not script.strip(): st.error("Script paste"); st.stop()
    if vid is None: st.error("Video Upload"); st.stop()

    total_start = time.time(); step_times = {}
    script_n = normalize_script(script)
    if not script_n.strip(): st.error("Script မှာ စာမပါပါ"); st.stop()
    vid.seek(0)
    with open("input.mp4", "wb") as f: f.write(vid.read())

    fw_model = None
    if not USE_FAST_VAD:
        try: fw_model = get_fw_model()
        except Exception: pass

    # Whisper/VAD ကို TTS နဲ့ တပြိုင်နက် run
    job_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job = job_pool.submit(prepare_video_job, "input.mp4", fw_model)

    t0 = time.time()
    pb = st.progress(0); txt = st.empty()
    def cb(i, tot, c): pb.progress((i+1)/tot); txt.caption(f"[{i+1}/{tot}]")
    try:
        _, pieces = tts_all(script_n, "voice.mp3", ref=st.session_state.get("ref"),
                            cb=cb, use_voxcpm=use_voxcpm)
    except Exception as e:
        st.error(f"TTS: {e}"); st.stop()
    step_times["🎙️ TTS"] = time.time() - t0

    t0 = time.time()
    try:
        info = job.result()
    except Exception as e:
        st.error(f"❌ Cut: {e}"); st.stop()
    job_pool.shutdown(wait=False)
    segments, vdur = info["segments"], info["vdur"]

    adur = audio_dur("voice.mp3")
    tempo = max(0.5, min(2.0, adur / vdur))
    eff = adur / tempo     # atempo ပြီးနောက် အသံအရှည်အမှန်

    with st.spinner(f"🎬 Finalizing — {CARTOON_LABELS.get(cartoon_key, '')}..."):
        try:
            sp = scr_to_srt_timed(pieces, eff, "sub.srt") if use_sub else None
            final_render("input.mp4", None, "video_only.mp4", 1.0,
                         srt_path=sp, fp=FONT_FILE, fs=FS, pos_y=pos_y,
                         bh=BH, ba=BA, use_neon=use_neon,
                         thickness=neon_thickness, speed=neon_speed,
                         segments=segments, cartoon=cartoon_key)
            mux_audio("video_only.mp4", "voice.mp3", "final.mp4", tempo)
        except Exception as e:
            st.warning(f"⚠️ Fail: {e} — Cartoon/Subtitle/Neon မပါဘဲ Simple Merge နဲ့ ထုတ်ပေးမယ်")
            simple_merge("input.mp4", "voice.mp3", "final.mp4", tempo, segments)
    step_times["🎬 Render"] = time.time() - t0

    total_elapsed = time.time() - total_start

    st.markdown(f"""
    <div class="timer-box">
        <div class="timer-title">⏱️ TOTAL TIME</div>
        <div class="timer-value">{total_elapsed:.1f}<span class="timer-unit">sec</span></div>
    </div>
    """, unsafe_allow_html=True)

    for name, t in step_times.items():
        st.markdown(f"<div class='step-timer'>{name} — <b>{t:.1f}s</b></div>", unsafe_allow_html=True)

    st.success(f"✅ Done — ⏱️ {total_elapsed:.1f}s")
    st.video("final.mp4")
    with open("final.mp4", "rb") as f:
        st.download_button(
            "📥 Download",
            f,
            file_name="recap.mp4",
            on_click=_on_download_clear,
            use_container_width=True,
        )
