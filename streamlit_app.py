# ============================================================
# 🎬 Myanmar TTS Recap — Fixed Widget Error
# Multilingual (English + Chinese + …)
# Video → Whisper Multi → Gemini 3.8 → TTS → Recap
# + Canvas + Auto/Manual Mode
# ============================================================

import subprocess, sys, importlib

# ============================================================
# ============ Auto-install missing packages =================
# ============================================================
def _ensure_pkg(import_name, pip_name):
    try:
        importlib.import_module(import_name)
    except ImportError:
        print(f"📦 Installing {pip_name}...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", pip_name]
            )
            print(f"✅ {pip_name} installed")
        except Exception as e:
            print(f"❌ {pip_name} install failed: {e}")

_ensure_pkg("google.generativeai", "google-generativeai")
_ensure_pkg("edge_tts", "edge-tts")
_ensure_pkg("faster_whisper", "faster-whisper")
_ensure_pkg("gradio_client", "gradio_client")
_ensure_pkg("cv2", "opencv-python-headless")
_ensure_pkg("PIL", "Pillow")
_ensure_pkg("ffmpeg", "ffmpeg-python")
_ensure_pkg("numpy", "numpy")

# ============================================================
# ============ Main Imports ==================================
# ============================================================
import streamlit as st
import os, re, hashlib, ffmpeg, shutil, asyncio, time
import tempfile, threading, queue, math, json
import concurrent.futures
import numpy as np
import edge_tts
from PIL import Image, ImageDraw, ImageFont
import cv2
from gradio_client import Client, handle_file

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ============================================================
# ================ Key Storage (persistent) ==================
# ============================================================
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".myanmar_recap")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def load_saved_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(data):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"save_config error: {e}")
        return False


_SAVED = load_saved_config()

# ============================================================
# ==================== Config ================================
# ============================================================
os.environ["HF_HOME"] = os.path.join(tempfile.gettempdir(), "hf_cache")

GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY", "").strip()
    or _SAVED.get("gemini_api_key", "").strip()
)

SPACES = [
    {"space": "openbmb/VoxCPM-Demo", "type": "demo"},
    {"space": "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts", "type": "burmese"},
]

# 🌐 Google Canvas URL
CANVAS_URL = "https://gemini.google.com/share/a96d9ba3e76e"

FONT_FILE = "MyanmarPadaung.ttf"
FS, BH, BA = 30, 100, 200
USE_FAST_VAD = True
AUDIO_BITRATE = "128k"
TTS_CHUNK = 300
EDGE_CHUNK = 200
TTS_WORKERS = 3
PNG_WORKERS = 4

# ============================================================
# ================ WHISPER CONFIG (MULTILINGUAL) =============
# ============================================================
WHISPER_MULTILINGUAL = True           # ← True = Chinese support
WHISPER_MODEL_EN = "small.en"         # ← English-only fallback
WHISPER_MODEL_MM = "tiny"             # ← VAD (မြန်မာ)
WHISPER_MODEL_MULTI = "small"         # ← Multilingual (auto-detect)
WHISPER_LANG_MM = "my"

# ============================================================
# ================ GEMINI MODELS (Updated 2026) ==============
# ============================================================
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.8-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

BOX_WIDTH_RATIO = 1.0
PADDING_Y = 15
CORNER_RADIUS = 20

EDGE_VOICES = {
    "female": "my-MM-NilarNeural",
    "male":   "my-MM-ThihaNeural",
}
EDGE_VOICE_FIXED = "male"

st.set_page_config(page_title="Myanmar TTS Recap", page_icon="🎬", layout="centered")


# ============================================================
# ==================== Session State =========================
# ============================================================
def _init_state():
    defaults = {
        "script": "",
        "last_paste": "",
        # ⭐ widget key မဟုတ်တဲ့ session value (paste box အတွက်)
        "paste_box_value": "",
        "pkey": None,
        "ref": None,
        "video_up_key": 0,
        "english_transcript": "",
        "gemini_key": GEMINI_API_KEY,
        "mode": "auto",
        "final_video": None,
        "active_model": None,
        "detected_lang": None,
        "_clear_paste": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


def _clear_paste_box():
    """Paste box ကို လုံခြုံစွာ ရှင်း (widget key မထိ)"""
    st.session_state.paste_box_value = ""
    st.session_state._clear_paste = True


def _on_download_clear():
    st.session_state.script = ""
    st.session_state.last_paste = ""
    st.session_state.paste_box_value = ""
    st.session_state.pkey = None
    st.session_state.video_up_key = st.session_state.get("video_up_key", 0) + 1
    st.session_state.english_transcript = ""
    st.session_state.final_video = None
    st.session_state.active_model = None
    st.session_state.detected_lang = None
    for f in ("preview.mp4", "prev.png", "prev_out.png",
              "english_transcript.txt", "gemini_script.txt",
              "final.mp4", "video_only.mp4", "voice.mp3"):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


# ============================================================
# ==================== CSS ===================================
# ============================================================
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
.key-ok{background:rgba(0,201,167,.15);border-left:4px solid #00c9a7;
 border-radius:10px;padding:10px 16px;margin:8px 0;color:#a8ffef;font-size:.9rem}
.key-bad{background:rgba(255,107,157,.15);border-left:4px solid #ff6b9d;
 border-radius:10px;padding:10px 16px;margin:8px 0;color:#ffc4d8;font-size:.9rem}
.model-info{background:rgba(0,180,216,.12);border-left:4px solid #00b4d8;
 border-radius:10px;padding:10px 16px;margin:8px 0;color:#a8e8ff;font-size:.85rem}
.lang-info{background:rgba(255,193,7,.12);border-left:4px solid #ffc107;
 border-radius:10px;padding:10px 16px;margin:8px 0;color:#ffe9a8;font-size:.9rem}
.canvas-box{background:linear-gradient(135deg,rgba(102,126,234,.15),rgba(118,75,162,.15));
 border:2px solid rgba(198,107,255,.4);border-radius:14px;padding:18px;margin:12px 0}
.big-recap-btn .stButton>button{
 background:linear-gradient(135deg,#ff6b9d,#c66bff,#6ba8ff)!important;
 font-size:1.3rem!important;padding:22px!important;font-weight:900!important;
 letter-spacing:1px!important;
 box-shadow:0 8px 30px rgba(198,107,255,.6)!important;
}
.paste-box textarea{
 background:linear-gradient(135deg,rgba(255,107,157,.12),rgba(198,107,255,.12))!important;
 border:2px dashed rgba(198,107,255,.55)!important;
 border-radius:18px!important;
 color:#fff!important;font-size:1.05rem!important;
 padding:22px!important;text-align:center!important;
}
.paste-box textarea:focus{
 border-color:#C66BFF!important;border-style:solid!important;
 box-shadow:0 0 24px rgba(198,107,255,.5)!important;
}
.paste-box textarea::placeholder{
 color:#b48cff!important;font-weight:600!important;
}
.mode-badge{display:inline-block;padding:6px 14px;border-radius:20px;
 font-size:.8rem;font-weight:700;margin-right:6px}
.mode-auto{background:linear-gradient(135deg,#00c9a7,#00b4d8);color:#fff}
.mode-manual{background:linear-gradient(135deg,#ff6b9d,#c66bff);color:#fff}
</style>
""", unsafe_allow_html=True)


# ============================================================
# ==================== Utility Functions =====================
# ============================================================

def vid_info(p):
    pr = ffmpeg.probe(p)
    v = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    return int(v['width']), int(v['height']), float(pr['format']['duration'])


def audio_dur(p):
    return float(ffmpeg.probe(p)['format']['duration'])


def t2s(s):
    ms = int(round((s - int(s)) * 1000))
    tot = int(s)
    if ms >= 1000:
        tot += 1
        ms = 0
    h, r = divmod(tot, 3600)
    m, sec = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def s2t(ts):
    ts = ts.strip()
    m = re.match(r'^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$', ts)
    if m:
        h, mi, se, ms = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, '0')) / 1000
    return None


def render_png(text, out, fp, W, H, fs=30, pos_y=100, bh=100, ba=100,
               box_width_ratio=BOX_WIDTH_RATIO,
               padding_y=PADDING_Y, corner_radius=CORNER_RADIUS):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype(fp, fs)
    except Exception:
        f = ImageFont.load_default()

    mc = max(15, int(W / (fs * 0.9)))
    lines, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 <= mc:
            cur = cur + " " + w if cur else w
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if not lines:
        return out

    lh = int(fs * 1.3)
    text_h = len(lines) * lh
    box_w = int(W * box_width_ratio)
    box_h = text_h + padding_y * 2

    if box_h > H:
        box_h = H
        padding_y = max(0, (H - text_h) // 2)

    box_x = (W - box_w) // 2
    max_y = H - box_h
    box_y = int((pos_y / 100.0) * max_y) if max_y > 0 else 0
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
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                if dx or dy:
                    d.text((lx + dx, ty + dy), ln, font=f, fill=(0, 0, 0, 255))
        d.text((lx, ty), ln, font=f, fill=(255, 255, 255, 255))
        ty += lh

    img.save(out, "PNG")
    return out


def split_for_sub(text, mc=30):
    parts = []
    sents = [s.strip() + "။" for s in text.replace("။", "။|").split("|") if s.strip()]
    for s in sents:
        s = s.replace("။။", "။")
        if len(s) <= mc:
            parts.append(s)
            continue
        cur = ""
        for w in s.split():
            if len(cur) + len(w) + 1 <= mc:
                cur = cur + " " + w if cur else w
            else:
                if cur:
                    parts.append(cur.strip())
                cur = w
        if cur:
            parts.append(cur.strip())
    return parts


def scr_to_srt_timed(pieces, total, path, mc=30):
    raw = sum(d for _, d in pieces)
    if raw <= 0:
        return None
    k = total / raw if raw > 0 else 1.0
    cur, idx, wrote = 0.0, 1, False
    with open(path, "w", encoding="utf-8") as f:
        for text, d in pieces:
            parts = split_for_sub(text, mc)
            if not parts:
                cur += d * k
                continue
            tot = sum(len(p) for p in parts)
            for p in parts:
                dd = (len(p) / tot) * d * k
                f.write(f"{idx}\n{t2s(cur)} --> {t2s(cur + dd)}\n{p}\n\n")
                cur += dd
                idx += 1
                wrote = True
    return path if wrote else None


def parse_srt(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().replace("\r\n", "\n").replace("\r", "\n")
    segs = []
    for ck in re.split(r"\n\s*\n", raw.strip()):
        ls = [l for l in ck.split("\n") if l.strip()]
        if len(ls) < 3:
            continue
        ts = next((l for l in ls if "-->" in l), None)
        if not ts:
            continue
        p = re.split(r"\s*-->\s*", ts)
        if len(p) != 2:
            continue
        a, b = s2t(p[0]), s2t(p[1])
        if a is None or b is None:
            continue
        idx = ls.index(ts)
        txt = " ".join(ls[idx + 1:]).strip()
        if txt:
            segs.append({"start": a, "end": b, "text": txt})
    return segs


def normalize_script(t):
    t = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]', '', t)
    t = re.sub(
        r'[\u201c\u201d"`*_#<>\[\]{}()\uff08\uff09\u300c\u300d\u300e\u300f\u00ab\u00bb~^|\\/]',
        ' ', t
    )
    out = []
    for ln in t.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln:
            continue
        if not ln.endswith(("။", "၊", "!", "?")):
            ln += "။"
        out.append(ln)
    t = " ".join(out)
    t = re.sub(r"။(\s*။)+", "။", t)
    return t.strip()


def has_speech(t):
    return re.search(r"[\u1000-\u1049\u1050-\u109F\w]", t) is not None


def split_scr(t, mc=TTS_CHUNK):
    sents = []
    for p in t.replace("။", "။|").split("|"):
        p = p.strip()
        if not p:
            continue
        sents.append(p if p.endswith("။") else p + "။")
    out, cur = [], ""
    for s in sents:
        if len(cur) + len(s) <= mc:
            cur += s
        else:
            if cur:
                out.append(cur)
            if len(s) > mc:
                for i in range(0, len(s), mc):
                    out.append(s[i:i + mc])
                cur = ""
            else:
                cur = s
    if cur:
        out.append(cur)
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
    cmd = ["ffmpeg", "-y", "-i", video_in, "-i", audio_in]
    if segments:
        sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segments)
        cmd += ["-vf", f"select='{sel}',setpts=N/FRAME_RATE/TB"]
    cmd += [
        "-af", f"atempo={tempo}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE,
        "-shortest",
        output_video
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")
    return output_video


def mux_audio(video_in, audio_in, output_video, tempo):
    cmd = ["ffmpeg", "-y", "-i", video_in, "-i", audio_in,
           "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
           "-shortest", output_video]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"Mux: {(r.stderr or '')[-300:]}")
    return output_video


def prepare_video_job(video_in, fw_model=None):
    segments = whisper_fast(video_in, fw_model)
    if not segments:
        raise Exception("Speech မတွေ့")
    kept, vfps = keep_count(video_in, segments)
    if kept == 0:
        raise Exception("Speech မတွေ့")
    return {"segments": segments, "vdur": kept / vfps}


# ============================================================
# ==================== Final Render ==========================
# ============================================================

def final_render(video_in, audio_in, output_video, tempo,
                 srt_path=None, fp=FONT_FILE, fs=FS, pos_y=100,
                 bh=BH, ba=BA, use_neon=True, thickness=15,
                 speed=0.4, tail=0.5, crop_ratio=0.95, mirror=True,
                 segments=None):
    W0, H0, _ = vid_info(video_in)
    pr = ffmpeg.probe(video_in)
    vs = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    n, d = vs['r_frame_rate'].split('/')
    fps = float(n) / float(d)

    if crop_ratio != 1.0:
        cw = int(W0 * crop_ratio)
        ch = int(H0 * crop_ratio)
        if cw % 2:
            cw -= 1
        if ch % 2:
            ch -= 1
        cx = (W0 - cw) // 2
        cy = (H0 - ch) // 2
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
            try:
                os.remove(p)
            except Exception:
                pass
            if rgba is None:
                return None
            rows = np.where(rgba[:, :, 3].any(axis=1))[0]
            if len(rows) == 0:
                return None
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
        cmd += ["-i", audio_in, "-af", f"atempo={tempo}",
                "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", "20", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-threads", "0"]
    if audio_in:
        cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest"]
    cmd += [output_video]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    q_in = queue.Queue(maxsize=8)
    q_out = queue.Queue(maxsize=8)
    stop = threading.Event()
    err = []

    def qput(q, item):
        while not stop.is_set():
            try:
                q.put(item, timeout=0.5)
                return True
            except queue.Full:
                pass
        return False

    def qget(q):
        while True:
            try:
                return q.get(timeout=0.5)
            except queue.Empty:
                if stop.is_set():
                    return None

    def reader():
        cap = cv2.VideoCapture(video_in)
        i = 0
        p = 0
        try:
            while not stop.is_set():
                if not cap.grab():
                    break
                t_in = i / fps
                i += 1
                if segments:
                    while p < len(segments) and segments[p][1] < t_in:
                        p += 1
                    if not (p < len(segments) and segments[p][0] <= t_in):
                        continue
                ok, fr = cap.retrieve()
                if not ok:
                    break
                if not qput(q_in, fr):
                    break
        except Exception as e:
            err.append(e)
            stop.set()
        finally:
            cap.release()
            qput(q_in, None)

    def writer():
        try:
            while True:
                bts = qget(q_out)
                if bts is None:
                    break
                proc.stdin.write(bts)
        except Exception as e:
            err.append(e)
            stop.set()

    th_r = threading.Thread(target=reader, daemon=True)
    th_w = threading.Thread(target=writer, daemon=True)
    th_r.start()
    th_w.start()

    j = 0
    k = 0
    try:
        while True:
            fr = qget(q_in)
            if fr is None:
                break
            t = j / fps
            fr = fr[cy:cy + ch, cx:cx + cw]
            if mirror:
                fr = fr[:, ::-1]
            fr = np.ascontiguousarray(fr)

            if use_neon:
                head = (t * speed * P / 4.0) % P
                fr[ys, xs] = make_strip(head)[Si]

            while k < len(subs) and subs[k]["b"] < t:
                k += 1
            if k < len(subs) and subs[k]["a"] <= t:
                sb = subs[k]
                reg = fr[sb["y0"]:sb["y1"]].astype(np.float32) * sb["inv"] + sb["pre"]
                fr[sb["y0"]:sb["y1"]] = reg.astype(np.uint8)

            if not qput(q_out, fr.tobytes()):
                break
            j += 1
    except Exception as e:
        err.append(e)
        stop.set()
    finally:
        if not stop.is_set():
            qput(q_out, None)
        th_w.join()
        stop.set()
        th_r.join()
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()

    if err:
        raise err[0]
    if proc.returncode != 0:
        raise Exception("Final render: ffmpeg fail")
    return output_video


# ============================================================
# ==================== Preview ===============================
# ============================================================

def draw_tiktok_border_preview(img, thickness=30, animated_phase=0.0):
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


# ============================================================
# ==================== Gemini Script (Multilingual) ==========
# ============================================================

GEMINI_SYSTEM_PROMPT = """You are an expert Burmese (Myanmar) recap script writer for TikTok/YouTube Shorts recap videos.

Your task: Take a transcript from a movie/story recap video — the transcript may be in **English, Chinese, Hindi, Korean, Japanese, Thai, or ANY other language** — and write a natural, engaging Burmese narration script.

RULES:
1. Write ONLY in Burmese (မြန်မာစာ). No English words except well-known proper nouns.
2. **The transcript may be in ANY language** — translate it directly to Burmese.
   - If English → translate to Burmese.
   - If Chinese (中文) → translate to Burmese directly.
   - If Hindi / Thai / Korean / Japanese / etc. → translate to Burmese directly.
3. Keep the ENTIRE story — no summaries, no skipping, no shortening.
4. Use natural spoken Burmese like a narrator telling a story to a friend.
5. Keep character names, relationships, and places consistent throughout.
6. Every sentence must end with ။ (Burmese period).
7. Split into short sentences (max ~25 Burmese characters per sentence) for subtitle sync.
8. Use ၊ (comma) for natural pauses within longer thoughts.
9. NO emojis, NO markdown, NO bullet points, NO English punctuation like ! ? .
10. If a character/place name is foreign, transliterate into Burmese phonetics.
11. Preserve emotional tone — excitement, tension, sadness — in word choice.

OUTPUT FORMAT:
- Plain text only.
- One paragraph, no line breaks between sentences (or one sentence per line).
- No headers, no titles, no intro like "Here is the script".
- Start directly with the story."""


LANG_HINTS = {
    "zh": "The transcript is in CHINESE (中文). Translate it directly to Burmese.",
    "en": "The transcript is in English. Translate it to Burmese.",
    "hi": "The transcript is in Hindi. Translate it directly to Burmese.",
    "ko": "The transcript is in Korean. Translate it directly to Burmese.",
    "ja": "The transcript is in Japanese. Translate it directly to Burmese.",
    "th": "The transcript is in Thai. Translate it directly to Burmese.",
    "vi": "The transcript is in Vietnamese. Translate it directly to Burmese.",
    "id": "The transcript is in Indonesian. Translate it directly to Burmese.",
    "my": "The transcript is in Burmese. Keep it in Burmese.",
}


def _lang_hint(lang_code):
    return LANG_HINTS.get(
        lang_code,
        f"The transcript is in '{lang_code}'. Translate it directly to Burmese."
    )


def _gemini_client():
    key = st.session_state.get("gemini_key", "").strip() or GEMINI_API_KEY
    if not key:
        raise Exception("Gemini API Key မထည့်ရသေးပါ — 🔑 API Key Settings မှာ ထည့်ပါ")
    try:
        import google.generativeai as genai
    except ImportError:
        raise Exception("pip install google-generativeai")
    genai.configure(api_key=key)
    return genai


def _is_model_missing(err_str):
    s = err_str.lower()
    return (
        "404" in s
        or "not found" in s
        or "no longer available" in s
        or "is not supported" in s
        or "does not exist" in s
    )


# ============================================================
# ============ Whisper (Multilingual) ========================
# ============================================================

def transcribe_english(video_path):
    """
    Whisper → Transcript (multilingual)
    - WHISPER_MULTILINGUAL=True  → Auto language detect
    - WHISPER_MULTILINGUAL=False → English only (small.en)
    """
    wav = "english_audio.wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", wav
    ], capture_output=True, check=True)

    detected_lang = "?"
    confidence = 0.0

    if WHISPER_MULTILINGUAL:
        model_name = WHISPER_MODEL_MULTI
        lang_arg = None
    else:
        model_name = WHISPER_MODEL_EN
        lang_arg = "en"

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            cpu_threads=os.cpu_count() or 4
        )

        kwargs = {
            "vad_filter": True,
            "beam_size": 1,
            "condition_on_previous_text": False,
            "temperature": 0
        }
        if lang_arg:
            kwargs["language"] = lang_arg

        segments, info = model.transcribe(wav, **kwargs)
        detected_lang = info.language
        confidence = info.language_probability
        lines = [seg.text.strip() for seg in segments if seg.text.strip()]

    except Exception:
        import whisper
        m = whisper.load_model(model_name)
        kwargs = {
            "condition_on_previous_text": False,
            "beam_size": 1,
            "temperature": 0
        }
        if lang_arg:
            kwargs["language"] = lang_arg

        result = m.transcribe(wav, **kwargs)
        detected_lang = result.get("language", "?")
        confidence = 0.0
        lines = [s["text"].strip() for s in result["segments"] if s["text"].strip()]

    st.session_state["detected_lang"] = detected_lang

    if WHISPER_MULTILINGUAL and detected_lang != "?":
        st.info(
            f"🗣️ Detected: **{detected_lang}** "
            f"({confidence:.0%} confidence)"
        )

    return " ".join(lines).strip()


def split_transcript(text, chunk_chars=4000):
    if len(text) <= chunk_chars:
        return [text]
    sentences = re.split(r'(?<=[.!?။。！？])\s+', text)
    chunks, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) + 1 <= chunk_chars:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks


def _get_working_model(genai):
    last_err = None
    for mname in GEMINI_MODELS:
        try:
            model = genai.GenerativeModel(
                model_name=mname,
                system_instruction=GEMINI_SYSTEM_PROMPT
            )
            test = model.generate_content(
                "hi",
                generation_config={"max_output_tokens": 3}
            )
            _ = test.text
            st.session_state.active_model = mname
            return model, mname
        except Exception as e:
            last_err = e
            if _is_model_missing(str(e)):
                continue
            raise
    raise Exception(
        f"Gemini model အားလုံး မရပါ။ နောက်ဆုံး error: {last_err}\n"
        f"စမ်းခဲ့တဲ့ models: {GEMINI_MODELS}"
    )


def gemini_write_script(english_text, progress_cb=None):
    genai = _gemini_client()
    chunks = split_transcript(english_text, chunk_chars=4000)
    n = len(chunks)
    full_script = []

    try:
        model, active_name = _get_working_model(genai)
        st.success(f"✅ Gemini Model: `{active_name}`")
    except Exception as e:
        raise Exception(f"Gemini model မရပါ: {e}")

    lang_code = st.session_state.get("detected_lang", "en") or "en"
    hint = _lang_hint(lang_code)
    st.caption(f"🌐 Source language: `{lang_code}`")

    for i, chunk in enumerate(chunks):
        if progress_cb:
            progress_cb(i, n, f"Chunk {i+1}/{n} — {active_name}")

        if n == 1:
            prompt = f"""{hint}

Here is the transcript. Write the full Burmese recap script.

TRANSCRIPT:
{chunk}

Remember: Full story, no summary, natural spoken Burmese, every sentence ends with ။"""
        else:
            if i == 0:
                prompt = f"""{hint}

This is PART 1 of {n} of a transcript.
Write the Burmese recap script for THIS PART ONLY.
Do NOT summarize. Do NOT add intro. Continue the story flow.

PART 1:
{chunk}"""
            elif i == n - 1:
                prev_tail = full_script[-1][-400:] if full_script else ""
                prompt = f"""{hint}

This is the FINAL PART ({i+1}/{n}) of the transcript.
Continue naturally from the previous Burmese text.

PREVIOUS BURMESE (end of last part):
{prev_tail}

FINAL PART:
{chunk}

Write the Burmese recap for this final part only."""
            else:
                prev_tail = full_script[-1][-400:] if full_script else ""
                prompt = f"""{hint}

This is PART {i+1}/{n} of the transcript.
Continue naturally from the previous Burmese text.

PREVIOUS BURMESE (end of last part):
{prev_tail}

PART {i+1}:
{chunk}

Write the Burmese recap for this part only. No summary."""

        resp = None
        for retry_model in GEMINI_MODELS:
            try:
                m = genai.GenerativeModel(
                    model_name=retry_model,
                    system_instruction=GEMINI_SYSTEM_PROMPT
                )
                resp = m.generate_content(prompt)
                break
            except Exception as e:
                if _is_model_missing(str(e)):
                    continue
                raise

        if resp is None:
            raise Exception("Gemini chunk အားလုံး fail")

        out = (resp.text or "").strip()
        out = re.sub(r"^```[\w]*\n?", "", out)
        out = re.sub(r"\n?```$", "", out)
        full_script.append(out)

    combined = "\n".join(full_script)
    combined = normalize_script(combined)
    return combined


# ============================================================
# ==================== TTS Functions =========================
# ============================================================

def tts_demo(chunks, ref, space, cb=None):
    cl = Client(space)
    files = []
    rf = handle_file(ref) if ref else None
    for i, c in enumerate(chunks):
        if cb:
            cb(i, len(chunks), c)
        res = cl.predict(
            text_input=c,
            control_instruction="A warm young woman, calm and expressive",
            reference_wav_path_input=rf,
            use_prompt_text=False, prompt_text_input="",
            cfg_value_input=2.0, do_normalize=True, denoise=False,
            api_name="/generate"
        )
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = f"chunk_{i}.wav"
        shutil.copy(p, dst)
        files.append(dst)
    return files


def tts_burmese(chunks, ref, space, cb=None):
    cl = Client(space)
    files = []
    if not ref:
        raise Exception("Reference Audio needed")
    rf = handle_file(ref)
    for i, c in enumerate(chunks):
        if cb:
            cb(i, len(chunks), c)
        res = cl.predict(
            target_text=c, ref_audio=rf,
            ref_text="မြန်မာ အသံနမူနာ", cfg_value=2.0,
            inference_timesteps=10, api_name="/tts"
        )
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = f"chunk_b_{i}.wav"
        shutil.copy(p, dst)
        files.append(dst)
    return files


async def _edge_tts_async(text, out_file, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_file)


def edge_tts_run(chunks, out_path, cb=None, workers=TTS_WORKERS):
    voice_id = EDGE_VOICES[EDGE_VOICE_FIXED]

    filtered = []
    for i, c in enumerate(chunks):
        for x in split_scr(c, EDGE_CHUNK):
            if has_speech(x):
                filtered.append((i, x))
    if not filtered:
        raise Exception("TTS လုပ်စရာ စာမတွေ့ပါ")

    def run_async(coro_fn):
        try:
            asyncio.run(coro_fn())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro_fn())
            loop.close()

    def tts_one(args):
        pos, (orig_i, c) = args
        os.makedirs("tts_cache", exist_ok=True)
        key = hashlib.md5((voice_id + c).encode("utf-8")).hexdigest()
        dst = f"tts_cache/{key}.mp3"
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            return (pos, c, dst)
        for attempt in range(4):
            try:
                if os.path.exists(dst):
                    os.remove(dst)
                run_async(lambda: _edge_tts_async(c, dst, voice_id))
                if os.path.exists(dst) and os.path.getsize(dst) > 0:
                    time.sleep(0.3)
                    return (pos, c, dst)
            except Exception:
                pass
            time.sleep(3 * (attempt + 1))
        return (pos, c, None)

    results = [None] * len(filtered)
    skipped = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for pos, c, dst in ex.map(tts_one, enumerate(filtered)):
            results[pos] = (c, dst)
            if dst is None:
                skipped.append(c[:30])
            if cb:
                cb(pos, len(filtered), c)

    good = [(c, d) for c, d in results if d]
    if not good:
        raise Exception("Edge TTS အသံမရပါ")
    if skipped:
        st.warning("⚠️ ကျော်လိုက်တဲ့ အပိုင်း: " + " | ".join(skipped))

    pieces = [(c, audio_dur(d)) for c, d in good]

    with open("edge_concat.txt", "w", encoding="utf-8") as f:
        for _, d in good:
            f.write(f"file '{d}'\n")

    ffmpeg.input("edge_concat.txt", format="concat", safe=0).output(
        out_path, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
    ).run(overwrite_output=True)

    return out_path, pieces


def tts_free(chunks, out, cb=None):
    out, pieces = edge_tts_run(chunks, out, cb=cb)
    return out, pieces


def tts_all(text, out, ref=None, cb=None, use_voxcpm=True):
    chunks = split_scr(text, TTS_CHUNK)

    if not use_voxcpm:
        return tts_free(chunks, out, cb=cb)

    files = None
    for s in SPACES:
        try:
            if s["type"] == "demo":
                files = tts_demo(chunks, ref, s["space"], cb)
            else:
                files = tts_burmese(chunks, ref, s["space"], cb)
            break
        except Exception:
            files = None
            continue

    if files is None:
        return tts_free(chunks, out, cb=cb)

    pieces = [(chunks[i], audio_dur(f)) for i, f in enumerate(files)]

    with open("concat.txt", "w", encoding="utf-8") as f:
        for a in files:
            f.write(f"file '{a}'\n")

    ffmpeg.input("concat.txt", format="concat", safe=0).output(
        out, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
    ).run(overwrite_output=True)

    return out, pieces


# ============================================================
# ==================== Whisper / VAD =========================
# ============================================================

@st.cache_resource(show_spinner=False)
def get_fw_model():
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL_MM, device="cpu", compute_type="int8",
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
                audio,
                VadOptions(min_silence_duration_ms=700, speech_pad_ms=200)
            )
            segs_vad = [(t["start"] / 16000.0, t["end"] / 16000.0) for t in ts]
            if segs_vad:
                return segs_vad
        except Exception:
            pass

    speech_segments = []
    try:
        model = model or get_fw_model()
        segments, _ = model.transcribe(
            "whisper_audio.wav", language=WHISPER_LANG_MM,
            vad_filter=False, beam_size=1,
            condition_on_previous_text=False, temperature=0
        )
        for seg in segments:
            speech_segments.append((seg.start, seg.end))
    except Exception:
        import whisper
        m = whisper.load_model(WHISPER_MODEL_MM)
        result = m.transcribe(
            "whisper_audio.wav", language=WHISPER_LANG_MM,
            condition_on_previous_text=False, beam_size=1, temperature=0
        )
        for seg in result["segments"]:
            speech_segments.append((seg["start"], seg["end"]))
    return speech_segments


# ============================================================
# ==================== UI ====================================
# ============================================================

st.markdown("<div class='main-title'>🎬 Myanmar TTS Recap</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>Video → Whisper Multi → Gemini 3.8 → 🚀 Recap</div>",
            unsafe_allow_html=True)

# ============================================================
# ==================== 🌐 GOOGLE CANVAS ======================
# ============================================================
with st.expander("📄 Transcript ထုတ်ယူမယ် (Gemini Canvas)", expanded=False):
    st.markdown(f"""
    <div class='canvas-box'>
        <div style='font-weight:700;font-size:1.05rem;margin-bottom:8px'>
            🌐 Google Gemini Canvas
        </div>
        <div style='color:#c8c8e0;font-size:.9rem;line-height:1.5'>
            Canvas ဖွင့် → Transcript Copy → Manual Mode မှာ Paste လုပ်ပါ
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.link_button("↗️ Canvas ကို Tab အသစ်မှာ ဖွင့်", CANVAS_URL,
                   use_container_width=True)
    st.caption("💡 Canvas ကို သုံးပြီး Transcript ထုတ် → Manual mode မှာ paste")

st.divider()

# ==================== 🔑 API Key Settings ====================
with st.expander("🔑 API Key Settings", expanded=(not st.session_state.gemini_key)):
    st.caption("Gemini API key ကို **တစ်ခါပဲ** ထည့်ရမယ် — စက်ထဲ save ဖြစ်သွားမယ်")

    key_input = st.text_input(
        "Gemini API Key",
        value=st.session_state.get("gemini_key", ""),
        type="password",
        placeholder="AIza...",
        key="api_key_input",     # ← unique key
    )

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        st.caption("🔗 [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — အခမဲ့")
    with c2:
        if st.button("💾 Save Key", use_container_width=True):
            if key_input.strip():
                st.session_state.gemini_key = key_input.strip()
                cfg = load_saved_config()
                cfg["gemini_api_key"] = key_input.strip()
                if save_config(cfg):
                    st.success("✅ Key သိမ်းပြီး")
                    time.sleep(0.8)
                    st.rerun()
            else:
                st.warning("Key ထည့်ပါ")
    with c3:
        if st.button("🗑️ Clear Key", use_container_width=True):
            st.session_state.gemini_key = ""
            cfg = load_saved_config()
            cfg.pop("gemini_api_key", None)
            save_config(cfg)
            st.success("✅ ဖျက်ပြီး")
            time.sleep(0.8)
            st.rerun()

    if st.session_state.gemini_key:
        k = st.session_state.gemini_key
        masked = k[:6] + "..." + k[-4:] if len(k) > 12 else "***"
        st.markdown(
            f"<div class='key-ok'>✅ Key: <code>{masked}</code></div>",
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            "<div class='key-bad'>⚠️ Key မထည့်ရသေးပါ — Auto mode သုံးလို့ မရဘူး</div>",
            unsafe_allow_html=True
        )

    st.markdown(
        f"<div class='model-info'>🤖 Models: "
        f"<code>{' → '.join(GEMINI_MODELS)}</code></div>",
        unsafe_allow_html=True
    )

    mode_str = "🌍 Multilingual" if WHISPER_MULTILINGUAL else "🇬🇧 English only"
    st.markdown(
        f"<div class='lang-info'>🗣️ Whisper mode: <b>{mode_str}</b> — "
        f"model: <code>{WHISPER_MODEL_MULTI if WHISPER_MULTILINGUAL else WHISPER_MODEL_EN}</code></div>",
        unsafe_allow_html=True
    )

st.divider()

# ==================== 📁 Video Upload ====================
st.subheader("📁 Video")
vid = st.file_uploader(
    "📹",
    type=["mp4", "mov", "avi", "mkv"],
    label_visibility="collapsed",
    key=f"video_up_{st.session_state.video_up_key}"
)
if vid:
    st.success(f"✅ {vid.name} — {vid.size/(1024*1024):.1f} MB")

# ==================== 🎯 Mode Selector ====================
st.markdown("### 🎯 Mode ရွေးပါ")

mode_col1, mode_col2 = st.columns(2)
with mode_col1:
    auto_btn = st.button(
        "🤖 Auto (AI Script)",
        use_container_width=True,
        type="primary" if st.session_state.mode == "auto" else "secondary"
    )
    if auto_btn:
        st.session_state.mode = "auto"
        st.rerun()

with mode_col2:
    manual_btn = st.button(
        "✍️ Manual (Paste)",
        use_container_width=True,
        type="primary" if st.session_state.mode == "manual" else "secondary"
    )
    if manual_btn:
        st.session_state.mode = "manual"
        st.rerun()

if st.session_state.mode == "auto":
    st.markdown(
        "<span class='mode-badge mode-auto'>🤖 AUTO MODE</span>"
        "<span style='color:#8888aa;font-size:.85rem'>"
        "Whisper Multi → Gemini → Script (auto)</span>",
        unsafe_allow_html=True
    )
else:
    st.markdown(
        "<span class='mode-badge mode-manual'>✍️ MANUAL MODE</span>"
        "<span style='color:#8888aa;font-size:.85rem'>"
        "Script ကို ကိုယ်တိုင် paste</span>",
        unsafe_allow_html=True
    )

# ==================== Mode-specific UI ====================
if st.session_state.mode == "manual":
    st.markdown("#### 📋 Script Paste")
    st.caption("💡 **Canvas** ကို အပေါ်မှာ ဖွင့် → Transcript Copy → ဒီနေရာ Paste")

    # ⭐ widget key မသုံး — session value သုံး
    # _clear_paste flag စစ်ပြီး value ရှင်း
    if st.session_state.get("_clear_paste", False):
        st.session_state.paste_box_value = ""
        st.session_state._clear_paste = False

    paste_value = st.session_state.get("paste_box_value", "")

    pasted_now = st.text_area(
        "paste_box",
        value=paste_value,
        height=150,
        label_visibility="collapsed",
        placeholder="📋  မြန်မာ Script ကို ဒီနေရာ Long-press → Paste  (သို့)  Ctrl+V",
        key="paste_widget_manual",     # ⭐ unique key
    )

    # ---- Auto paste → script ----
    if pasted_now and pasted_now.strip():
        clean = pasted_now.strip()
        if clean != st.session_state.get("last_paste", ""):
            st.session_state.last_paste = clean

            lines = [l.strip().strip('"\u201c\u201d').strip()
                     for l in clean.splitlines()]
            result = "\n\n".join(l for l in lines if l)

            st.session_state.script = result
            # ⭐ clear flag (widget key ကို မထိ)
            st.session_state._clear_paste = True

            st.success(f"✅ Script ရောက်သွားပြီ — {len(result)} လုံး")
            st.rerun()

    # ---- Script Editor (unique key) ----
    script = st.text_area(
        "Script Editor",
        value=st.session_state.script,
        height=200,
        placeholder="မြန်မာ Script...",
        key="script_editor_manual",    # ⭐ unique key
    )
    st.session_state.script = script

    c1, c2 = st.columns([3, 1])
    with c1:
        st.caption(f"📝 {len(script):,} လုံး")
    with c2:
        if st.button("🗑️ Clear Script", use_container_width=True):
            st.session_state.script = ""
            st.session_state.last_paste = ""
            st.session_state._clear_paste = True
            st.rerun()

else:  # auto mode
    if st.session_state.english_transcript:
        with st.expander("📄 Transcript (raw)", expanded=False):
            st.text_area("eng_view", st.session_state.english_transcript,
                         height=200, label_visibility="collapsed",
                         key="transcript_viewer")
    if st.session_state.script:
        with st.expander("📝 Gemini Script (edit လို့ရ)", expanded=False):
            edited = st.text_area("mm_view", st.session_state.script,
                                  height=200, label_visibility="collapsed",
                                  key="script_viewer")
            if edited != st.session_state.script:
                st.session_state.script = edited

# ==================== ⚙️ Options ====================
with st.expander("⚙️ Options", expanded=False):
    use_voxcpm = st.toggle("VoxCPM2 သုံးမလား?", value=False, key="use_voxcpm_toggle")

    ref_file = None
    if use_voxcpm:
        ref_file = st.file_uploader("🎤 Ref Audio (VoxCPM2)",
                                    type=["wav", "mp3", "m4a"], key="ref_up")
        if ref_file:
            with open("ref.wav", "wb") as f:
                f.write(ref_file.read())
            st.session_state.ref = "ref.wav"
            st.success("✅ Ref Audio")
        elif os.path.exists("ref.wav"):
            st.session_state.ref = "ref.wav"
            st.caption("📎 Ref Audio (အရင် ထည့်ထားတာ ဆက်ရှိ)")
        else:
            st.session_state.ref = None
    else:
        st.session_state.ref = None

    use_sub = st.toggle("Subtitle Burn-in", value=True, key="use_sub_toggle")
    if use_sub:
        pos_y = st.slider("📍 Subtitle Position", 0, 100, 100, 1,
                          key="pos_y_slider")
    else:
        pos_y = 100

st.divider()

# ==================== 🚀 ONE-CLICK RECAP BUTTON ====================
st.markdown('<div class="big-recap-btn">', unsafe_allow_html=True)
recap_clicked = st.button("🚀 RECAP", type="primary", use_container_width=True,
                          key="recap_main_btn")
st.markdown('</div>', unsafe_allow_html=True)

st.caption(f"💡 Mode: **{st.session_state.mode.upper()}** — Video + "
           f"{'Script' if st.session_state.mode == 'manual' else 'Key'} ရှိပြီဆိုရင် "
           f"ဒီခလုတ်တစ်ခုပဲ နှိပ်ပါ")

# ==================== Main Process ====================
if recap_clicked:
    if vid is None:
        st.error("📹 Video upload လုပ်ပါ")
        st.stop()

    if st.session_state.mode == "auto":
        if not st.session_state.get("gemini_key", "").strip():
            st.error("⚠️ 🔑 API Key Settings မှာ Gemini key ထည့်ပါ")
            st.stop()
    else:
        if not st.session_state.script.strip():
            st.error("✍️ Script paste လုပ်ပါ")
            st.stop()

    total_start = time.time()
    step_times = {}

    vid.seek(0)
    with open("input.mp4", "wb") as f:
        f.write(vid.read())

    # ==================== AUTO MODE ====================
    if st.session_state.mode == "auto":
        t0 = time.time()
        with st.status("🎧 Step 1/4 — Whisper (multilingual)...",
                       expanded=True) as s:
            model_used = WHISPER_MODEL_MULTI if WHISPER_MULTILINGUAL else WHISPER_MODEL_EN
            st.write(f"Model: `{model_used}`")
            try:
                eng = transcribe_english("input.mp4")
            except Exception as e:
                st.error(f"Whisper fail: {e}")
                st.stop()
            if not eng.strip():
                st.error("Transcript မရပါ")
                st.stop()
            st.session_state.english_transcript = eng
            lang = st.session_state.get("detected_lang", "?")
            st.write(f"✅ {len(eng)} chars — lang: `{lang}`")
            s.update(label=f"✅ Step 1/4 — Transcript ({len(eng)} chars) "
                           f"[{lang}]",
                     state="complete")
        step_times["🎧 Whisper"] = time.time() - t0

        t0 = time.time()
        with st.status("🤖 Step 2/4 — Gemini script...", expanded=True) as s:
            pb = st.progress(0)
            txt = st.empty()

            def gp(i, n, msg):
                pb.progress(min(1.0, (i + 1) / max(1, n)))
                txt.caption(f"🤖 {msg}")

            try:
                mm_script = gemini_write_script(eng, progress_cb=gp)
            except Exception as e:
                st.error(f"Gemini fail: {e}")
                st.stop()

            if not mm_script.strip():
                st.error("Script မထုတ်ပေးပါ")
                st.stop()

            st.session_state.script = mm_script
            with open("gemini_script.txt", "w", encoding="utf-8") as f:
                f.write(mm_script)
            used_model = st.session_state.get("active_model", "unknown")
            st.write(f"✅ {len(mm_script)} chars — model: `{used_model}`")
            s.update(label=f"✅ Step 2/4 — Script ({len(mm_script)} chars) "
                           f"[{used_model}]",
                     state="complete")
        step_times["🤖 Gemini"] = time.time() - t0

    else:
        with st.status("✍️ Manual mode — Script ready",
                       expanded=False) as s:
            st.write(f"✅ Script: {len(st.session_state.script)} chars")
            s.update(label="✅ Script ready (manual)", state="complete")

    script_n = normalize_script(st.session_state.script)
    if not script_n.strip():
        st.error("Script မှာ စာမပါပါ")
        st.stop()

    # ==================== STEP 3: TTS + Cut ====================
    t0 = time.time()
    job_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job = job_pool.submit(prepare_video_job, "input.mp4", None)

    with st.status("🎙️ Step 3/4 — Voice ထုတ်နေသည်...",
                   expanded=True) as s:
        pb2 = st.progress(0)
        txt2 = st.empty()

        def cb(i, tot, c):
            pb2.progress(min(1.0, (i + 1) / max(1, tot)))
            txt2.caption(f"[{i+1}/{tot}] {c[:50]}")

        try:
            _, pieces = tts_all(
                script_n, "voice.mp3",
                ref=st.session_state.get("ref"),
                cb=cb, use_voxcpm=use_voxcpm
            )
        except Exception as e:
            st.error(f"TTS: {e}")
            st.stop()

        st.write("✅ Voice ready")
        s.update(label="✅ Step 3/4 — Voice", state="complete")

    try:
        info = job.result()
    except Exception as e:
        st.error(f"❌ Cut: {e}")
        st.stop()
    finally:
        job_pool.shutdown(wait=False)

    segments = info["segments"]
    vdur = info["vdur"]

    adur = audio_dur("voice.mp3")
    tempo = max(0.5, min(2.0, adur / vdur))
    eff = adur / tempo
    step_times["🎙️ TTS"] = time.time() - t0

    # ==================== STEP 4: Render ====================
    t0 = time.time()
    with st.status("🎬 Step 4/4 — Final render...",
                   expanded=True) as s:
        st.write("Video render လုပ်နေသည်...")
        try:
            sp = scr_to_srt_timed(pieces, eff, "sub.srt") if use_sub else None
            final_render(
                "input.mp4", None, "video_only.mp4", 1.0,
                srt_path=sp, fp=FONT_FILE, fs=FS, pos_y=pos_y,
                bh=BH, ba=BA, use_neon=True,
                thickness=15, speed=0.4,
                segments=segments
            )
            mux_audio("video_only.mp4", "voice.mp3", "final.mp4", tempo)
        except Exception as e:
            st.warning(f"⚠️ Fallback: {e}")
            simple_merge("input.mp4", "voice.mp3", "final.mp4",
                         tempo, segments)
        st.write("✅ Render ပြီး")
        s.update(label="✅ Step 4/4 — Render ပြီး", state="complete")
    step_times["🎬 Render"] = time.time() - t0

    total_elapsed = time.time() - total_start

    # ==================== OUTPUT ====================
    st.markdown(f"""
    <div class="timer-box">
        <div class="timer-title">⏱️ TOTAL TIME</div>
        <div class="timer-value">{total_elapsed:.1f}<span class="timer-unit">sec</span></div>
    </div>
    """, unsafe_allow_html=True)

    for name, t in step_times.items():
        st.markdown(
            f"<div class='step-timer'>{name} — <b>{t:.1f}s</b></div>",
            unsafe_allow_html=True
        )

    st.success(f"✅ Recap ပြီးပါပြီ — ⏱️ {total_elapsed:.1f}s")
    st.video("final.mp4")

    with st.expander("📝 Script ကြည့် / ပြင်", expanded=False):
        edited = st.text_area("script_edit_view", st.session_state.script,
                              height=200, label_visibility="collapsed",
                              key="script_edit_after_render")
        if edited != st.session_state.script:
            st.session_state.script = edited
            st.caption("💡 ပြင်ပြီးရင် 🚀 RECAP ပြန်နှိပ်ပါ")

    with open("final.mp4", "rb") as f:
        st.download_button(
            "📥 Download Recap Video",
            f,
            file_name="recap.mp4",
            on_click=_on_download_clear,
            use_container_width=True,
            key="download_btn_final",
        )