import streamlit as st
import os, re, hashlib, ffmpeg, shutil, subprocess, asyncio, time, tempfile
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
USE_FAST_VAD = True   # Whisper မသုံးဘဲ VAD နဲ့ speech ရှာ (အမြန်ဆုံး)
AUDIO_BITRATE = "128k"
TTS_CHUNK = 600
EDGE_CHUNK = 400
TTS_WORKERS = 2
PNG_WORKERS = 4
MIN_KEEP_SECONDS = 1.0   # Speech ဒီထက်တိုရင် Video အပြည့်သုံး

CANVAS_URL = "https://gemini.google.com/share/a96d9ba3e76e"   # Gemini Canvas
WHISPER_MODEL = "tiny"
WHISPER_LANG = "my"

BOX_WIDTH_RATIO = 1.0
PADDING_Y = 15
CORNER_RADIUS = 20

# TikTok Logo Colors
TIKTOK_CYAN = "#25F4EE"
TIKTOK_MAGENTA = "#FE2C55"
TIKTOK_BLACK = "#000000"

EDGE_VOICES = {
    "female": "my-MM-NilarNeural",
    "male":   "my-MM-ThihaNeural",
}
EDGE_VOICE_FIXED = "male"

TTS_CACHE = os.path.join(tempfile.gettempdir(), "tts_cache")

# Font ကို cwd / script folder နှစ်နေရာလုံးမှာ ရှာ
try:
    _here = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _here = os.getcwd()
FONT_FOUND = False
for _c in (FONT_FILE, os.path.join(_here, FONT_FILE)):
    if os.path.exists(_c):
        FONT_FILE = _c
        FONT_FOUND = True
        break

st.set_page_config(page_title="Myanmar TTS Recap", page_icon="🎬", layout="centered")

# Session တစ်ခုချင်းစီအတွက် သီးသန့် work folder (ဖိုင်တွေ မရောအောင်)
if "wd" not in st.session_state or not os.path.isdir(st.session_state.wd):
    st.session_state.wd = tempfile.mkdtemp(prefix="recap_")
WD = st.session_state.wd


def wp(name):
    return os.path.join(WD, name)


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


# ==================== Utility Functions ====================

def _rate(s):
    """'30000/1001' → float (0/0, ဗလာ စတာတွေကို None)"""
    try:
        n, d = str(s).split('/')
        n, d = float(n), float(d)
        if d == 0 or n <= 0: return None
        return n / d
    except Exception:
        return None


def get_fps(path):
    try:
        pr = ffmpeg.probe(path)
        vs = next(s for s in pr['streams'] if s.get('codec_type') == 'video')
        for key in ("avg_frame_rate", "r_frame_rate"):
            f = _rate(vs.get(key))
            if f and 1 <= f <= 240: return f
    except Exception:
        pass
    try:
        cap = cv2.VideoCapture(path)
        f = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        if f and 1 <= f <= 240: return float(f)
    except Exception:
        pass
    return 30.0


def vid_info(p):
    pr = ffmpeg.probe(p)
    v = next((s for s in pr['streams'] if s.get('codec_type') == 'video'), None)
    if v is None: raise Exception("Video stream မတွေ့ပါ")
    W, H = int(v['width']), int(v['height'])
    dur = None
    for src in (pr.get('format', {}), v):
        try:
            d = float(src.get('duration'))
            if d > 0: dur = d; break
        except (TypeError, ValueError):
            pass
    if dur is None:
        cap = cv2.VideoCapture(p)
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT); f = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        dur = (n / f) if (n and f and n > 0 and f > 0) else 0.0
    return W, H, float(dur)


def frame_size(path):
    """cv2 က တကယ်ဖတ်ရတဲ့ frame အရွယ်အစား (rotation metadata ပါရင်လည်း မှန်)"""
    cap = cv2.VideoCapture(path)
    ok, fr = cap.read()
    cap.release()
    if not ok or fr is None: raise Exception("Video frame ဖတ်မရပါ")
    return int(fr.shape[1]), int(fr.shape[0])


def media_dur(path):
    try:
        return float(ffmpeg.probe(path)['format']['duration'])
    except Exception:
        return 0.0


def audio_ok(path):
    return os.path.exists(path) and os.path.getsize(path) > 0 and media_dur(path) > 0.05


def safe_remove(path):
    try:
        if os.path.exists(path): os.remove(path)
    except Exception:
        pass


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


def merge_segments(segs, total=0.0, gap=0.05):
    """Segment တွေကို စီ + ထပ်နေတာ ပေါင်း + မမှန်တာ ဖယ်"""
    clean = []
    for a, b in (segs or []):
        try:
            a = max(0.0, float(a)); b = float(b)
        except Exception:
            continue
        if total > 0: b = min(b, total)
        if b - a > 0.02: clean.append((a, b))
    clean.sort()
    merged = []
    for a, b in clean:
        if merged and a <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def render_png(text, out, fp, W, H, fs=30, pos_y=100, bh=100, ba=100,
                box_width_ratio=BOX_WIDTH_RATIO,
                padding_y=PADDING_Y, corner_radius=CORNER_RADIUS):
    img = Image.new("RGBA", (W, H), (0,0,0,0))
    d = ImageDraw.Draw(img)
    try: f = ImageFont.truetype(fp, fs)
    except Exception: f = ImageFont.load_default()

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
    max_y = max(0, H - box_h)
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


def scr_to_srt(scr, dur, path, mc=30):
    if dur <= 0: return None
    sents = [s.strip()+"။" for s in scr.replace("။","။|").split("|") if s.strip()]
    if not sents: return None
    parts = []
    for s in sents:
        s = s.replace("။။","။")
        if len(s) <= mc: parts.append(s)
        else:
            cur = ""
            for w in s.split():
                if len(cur)+len(w)+1 <= mc: cur = cur+" "+w if cur else w
                else:
                    if cur: parts.append(cur.strip())
                    cur = w
            if cur: parts.append(cur.strip())
    if not parts: return None
    tot = sum(len(p) for p in parts); cur = 0.0
    with open(path, "w", encoding="utf-8") as f:
        for i, p in enumerate(parts, 1):
            d = (len(p)/tot)*dur
            f.write(f"{i}\n{t2s(cur)} --> {t2s(cur+d)}\n{p}\n\n"); cur += d
    return path


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
    """Script ကို TTS/Subtitle အတွက် သန့်စင် (quote, emoji, စာကြောင်းလွတ်, ။ ထပ်)"""
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
    """ပြောလို့ရတဲ့ အက္ခရာ/ဂဏန်း ပါမပါ (။ ၊ သင်္ကေတချည်းဆိုရင် False)"""
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


def atempo_filter(tempo):
    """Audio ကို video အရှည်နဲ့ကိုက်အောင် tempo ချိန် + ကျန်ရင် တိတ်ဆိတ်စွာ ဖြည့် (apad)"""
    return f"atempo={tempo:.4f},apad"


def calc_tempo(adur, vdur):
    """(tempo, out_of_range) — atempo ကို 0.5~2.0 ကြားပဲ ထား"""
    if adur <= 0 or vdur <= 0: return 1.0, False
    raw = adur / vdur
    t = max(0.5, min(2.0, raw))
    return t, abs(t - raw) > 0.02


# ==================== Render ====================

def keep_count(video_in, segments):
    """Speech အပိုင်းထဲက frame အရေအတွက် (encode မလုပ်ဘဲ တွက်)"""
    fps = get_fps(video_in)
    _, _, dur = vid_info(video_in)
    total = max(1, int(round(dur * fps)))
    t = np.arange(total) / fps
    mask = np.zeros(total, dtype=bool)
    for a, b in segments:
        mask |= (t >= a) & (t <= b)
    return int(mask.sum()), fps


def simple_merge(video_in, audio_in, output_video, tempo, segments=None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video_in, "-i", audio_in,
           "-af", atempo_filter(tempo), "-map", "0:v:0", "-map", "1:a:0"]
    if segments:
        sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segments)
        cmd += ["-vf", f"select='{sel}',setpts=N/FRAME_RATE/TB"]
    cmd += ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
            "-shortest", "-movflags", "+faststart", output_video]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")
    return output_video


def mux_audio(video_in, audio_in, output_video, tempo):
    """Video ကို ပြန် encode မလုပ်ဘဲ အသံပေါင်းပေး (copy)"""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video_in, "-i", audio_in,
           "-af", atempo_filter(tempo), "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
           "-shortest", "-movflags", "+faststart", output_video]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"Mux: {(r.stderr or '')[-300:]}")
    return output_video


def final_render(video_in, audio_in, output_video, tempo,
                 srt_path=None, fp=FONT_FILE, fs=FS, pos_y=100, bh=BH, ba=BA,
                 use_neon=True, thickness=15, speed=0.4, tail=0.5,
                 crop_ratio=0.95, mirror=True, segments=None):
    """Crop + Mirror + Chase Neon + Subtitle + Audio — encode တစ်ခါတည်း"""
    W0, H0 = frame_size(video_in)
    fps = get_fps(video_in)

    if 0 < crop_ratio < 1.0:
        cw = int(W0 * crop_ratio); ch = int(H0 * crop_ratio)
    else:
        cw, ch = W0, H0
    cw -= cw % 2; ch -= ch % 2          # yuv420p အတွက် အရွယ်စုံ
    if cw < 16 or ch < 16: raise Exception("Video အရွယ်အစား သေးလွန်း")
    cx = (W0 - cw) // 2; cy = (H0 - ch) // 2
    W, H = cw, ch

    # ---- Subtitle PNG များကို အကြိုပြင် (parallel) ----
    subs = []
    if srt_path and os.path.exists(srt_path):
        segs_sub = parse_srt(srt_path)
        sub_dir = wp("subtitle_pngs")
        os.makedirs(sub_dir, exist_ok=True)

        def prep(args):
            i, sg = args
            p = os.path.join(sub_dir, f"s_{i:04d}.png")
            render_png(sg["text"], p, fp, W, H, fs, pos_y, bh, ba,
                       box_width_ratio=BOX_WIDTH_RATIO)
            rgba = cv2.imread(p, cv2.IMREAD_UNCHANGED)
            safe_remove(p)
            if rgba is None or rgba.ndim != 3 or rgba.shape[2] != 4: return None
            rows = np.where(rgba[:, :, 3].any(axis=1))[0]
            if len(rows) == 0: return None
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
            crop = rgba[y0:y1]
            alpha = crop[:, :, 3:4].astype(np.float32) / 255.0
            pre = crop[:, :, :3].astype(np.float32) * alpha
            return {"a": sg["start"], "b": sg["end"], "y0": y0, "y1": y1,
                    "inv": 1.0 - alpha, "pre": pre}

        with concurrent.futures.ThreadPoolExecutor(max_workers=PNG_WORKERS) as ex:
            subs = [x for x in ex.map(prep, enumerate(segs_sub)) if x]
        subs.sort(key=lambda s: s["a"])

    # ---- Neon ring ကြိုတွက် ----
    if use_neon:
        th = max(1, min(thickness, W // 4, H // 4))
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
        CYAN = np.array([255, 255, 0], np.float32)       # BGR
        MAGENTA = np.array([110, 30, 255], np.float32)   # BGR
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
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", f"{fps:.5f}", "-i", "-",
    ]
    if audio_in:
        cmd += ["-i", audio_in, "-af", atempo_filter(tempo), "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", str(FINAL_CRF), "-preset", FINAL_PRESET,
            "-pix_fmt", "yuv420p", "-threads", "0"]
    if audio_in:
        cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest"]
    cmd += ["-movflags", "+faststart", output_video]

    log_path = wp("render.log")
    logf = open(log_path, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=logf)

    cap = cv2.VideoCapture(video_in)
    if not cap.isOpened():
        logf.close(); proc.kill()
        raise Exception("Video ဖွင့်မရပါ")

    i = 0; j = 0; k = 0; p = 0; broken = False
    try:
        while True:
            if not cap.grab(): break
            t_in = i / fps
            i += 1
            if segments:
                while p < len(segments) and segments[p][1] < t_in: p += 1
                if not (p < len(segments) and segments[p][0] <= t_in): continue
            ok, fr = cap.retrieve()
            if not ok or fr is None: break
            t = j / fps
            if fr.ndim == 2: fr = cv2.cvtColor(fr, cv2.COLOR_GRAY2BGR)
            if fr.shape[1] != W0 or fr.shape[0] != H0:
                fr = cv2.resize(fr, (W0, H0))
            fr = fr[cy:cy + ch, cx:cx + cw]
            if mirror: fr = fr[:, ::-1]
            fr = np.ascontiguousarray(fr)

            if use_neon:
                head = (t * speed * P / 4.0) % P
                fr[ys, xs] = make_strip(head)[Si]

            while k < len(subs) and subs[k]["b"] < t: k += 1
            if k < len(subs) and subs[k]["a"] <= t:
                sb = subs[k]
                reg = fr[sb["y0"]:sb["y1"]].astype(np.float32) * sb["inv"] + sb["pre"]
                fr[sb["y0"]:sb["y1"]] = np.clip(reg, 0, 255).astype(np.uint8)

            try:
                proc.stdin.write(fr.tobytes())
            except (BrokenPipeError, OSError):
                broken = True
                break
            j += 1
    finally:
        cap.release()
        try: proc.stdin.close()
        except Exception: pass
        proc.wait()
        logf.close()

    if proc.returncode != 0 or broken or j == 0:
        tail_txt = ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                tail_txt = f.read()[-300:]
        except Exception:
            pass
        raise Exception(f"Final render: ffmpeg fail ({j} frames) {tail_txt}")
    if not os.path.exists(output_video) or os.path.getsize(output_video) == 0:
        raise Exception("Final render: output မထွက်ပါ")
    return output_video


# ==================== Speech Detection ====================

@st.cache_resource(show_spinner=False)
def get_fw_model():
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                        cpu_threads=os.cpu_count() or 4)


def whisper_fast(video_path, model=None):
    """Speech segment တွေ ရှာ — အသံမပါ/ရှာမရရင် [] ပြန် (error မတက်)"""
    wav = wp("whisper_audio.wav")
    safe_remove(wav)
    r = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-vn",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav
    ], capture_output=True)
    if r.returncode != 0 or not os.path.exists(wav):
        return []   # Video မှာ အသံ track မရှိ

    if USE_FAST_VAD:
        try:
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import get_speech_timestamps, VadOptions
            audio = decode_audio(wav, sampling_rate=16000)
            ts = get_speech_timestamps(
                audio, VadOptions(min_silence_duration_ms=700, speech_pad_ms=200))
            segs_vad = [(t["start"] / 16000.0, t["end"] / 16000.0) for t in ts]
            if segs_vad:
                return segs_vad
        except Exception:
            pass   # VAD မရရင် Whisper နဲ့ ဆက်သွား

    speech_segments = []
    try:
        model = model or get_fw_model()
        segments, _ = model.transcribe(
            wav, language=WHISPER_LANG,
            vad_filter=False, beam_size=1,
            condition_on_previous_text=False, temperature=0
        )
        for seg in segments:
            speech_segments.append((seg.start, seg.end))
    except Exception:
        try:
            import whisper
            model = whisper.load_model(WHISPER_MODEL)
            result = model.transcribe(
                wav, language=WHISPER_LANG,
                condition_on_previous_text=False, beam_size=1, temperature=0
            )
            for seg in result["segments"]:
                speech_segments.append((seg["start"], seg["end"]))
        except Exception:
            return []
    return speech_segments


def prepare_video_job(video_in, script_text, use_sub, fw_model,
                      pos_y, use_neon, thickness, speed):
    """Background: Speech ရှာ → Subtitle → အသံမပါ Video render (TTS နဲ့ တပြိုင်တည်း)
    Speech မတွေ့/တိုလွန်းရင် Video အပြည့်ကို သုံး (error မတက်)"""
    _, _, total = vid_info(video_in)
    try:
        segs = whisper_fast(video_in, fw_model)
    except Exception:
        segs = []
    segs = merge_segments(segs, total)

    no_speech = False
    kept, vfps = 0, get_fps(video_in)
    if segs:
        try:
            kept, vfps = keep_count(video_in, segs)
        except Exception:
            kept = 0
    if not segs or kept / vfps < MIN_KEEP_SECONDS:
        no_speech = True
        segs = None                      # Video အပြည့်
        kept = max(1, int(round(total * vfps)))
    vdur = kept / vfps

    render_err = None
    out_path = wp("video_only.mp4")
    safe_remove(out_path)
    try:
        sp = scr_to_srt(script_text, vdur, wp("sub.srt")) if use_sub else None
        final_render(video_in, None, out_path, 1.0,
                     srt_path=sp, fp=FONT_FILE, fs=FS, pos_y=pos_y,
                     bh=BH, ba=BA, use_neon=use_neon,
                     thickness=thickness, speed=speed, segments=segs)
    except Exception as e:
        render_err = str(e)
    return {"segments": segs, "vdur": vdur, "render_err": render_err,
            "no_speech": no_speech}


# ==================== Preview ====================

def draw_tiktok_border_preview(img, thickness=30, animated_phase=0.0):
    """Preview frame ပေါ်မှာ TikTok 3-layer border ဆွဲ"""
    d = ImageDraw.Draw(img, "RGBA")
    W, H = img.size
    inner_c = max(3, thickness // 3)

    d.rectangle([0, 0, W - 1, H - 1],
                outline=(0, 0, 0, 255), width=thickness)

    if animated_phase > 0:
        import math
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

def _retry(fn, tries=2, wait=2):
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(wait * (k + 1))
    raise last


def tts_demo(chunks, ref, space, cb=None):
    cl = Client(space); files = []; rf = handle_file(ref) if ref else None
    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks), c)
        res = _retry(lambda: cl.predict(
            text_input=c,
            control_instruction="A warm young woman, calm and expressive",
            reference_wav_path_input=rf,
            use_prompt_text=False, prompt_text_input="",
            cfg_value_input=2.0, do_normalize=True, denoise=False,
            api_name="/generate"
        ))
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = wp(f"chunk_{i}.wav"); shutil.copy(p, dst); files.append(dst)
    return files


def tts_burmese(chunks, ref, space, cb=None):
    if not ref: raise Exception("Reference Audio needed")
    cl = Client(space); files = []
    rf = handle_file(ref)
    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks), c)
        res = _retry(lambda: cl.predict(
            target_text=c, ref_audio=rf,
            ref_text="မြန်မာ အသံနမူနာ", cfg_value=2.0,
            inference_timesteps=10, api_name="/tts"
        ))
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = wp(f"chunk_b_{i}.wav"); shutil.copy(p, dst); files.append(dst)
    return files


async def _edge_tts_async(text, out_file, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_file)


def concat_audio(files, out_path):
    """Audio ဖိုင်တွေ ဆက် (absolute path သုံး — relative path ပြဿနာမရှိ)"""
    lst = wp("concat_list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for a in files:
            ap = os.path.abspath(a).replace("'", "'\\''")
            f.write(f"file '{ap}'\n")
    safe_remove(out_path)
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c:a", "libmp3lame", "-b:a", AUDIO_BITRATE, "-ar", "48000", out_path],
        capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0 or not audio_ok(out_path):
        raise Exception(f"Audio concat: {(r.stderr or '')[-300:]}")
    return out_path


def edge_tts_run(chunks, out_path, cb=None, workers=TTS_WORKERS):
    voice_id = EDGE_VOICES[EDGE_VOICE_FIXED]
    os.makedirs(TTS_CACHE, exist_ok=True)

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
            try: loop.run_until_complete(coro_fn())
            finally: loop.close()

    def synth(text):
        key = hashlib.md5((voice_id + text).encode("utf-8")).hexdigest()
        dst = os.path.join(TTS_CACHE, f"{key}.mp3")
        if audio_ok(dst):
            return dst   # ယခင်ထုတ်ပြီးသား — request ပြန်မခေါ်
        for attempt in range(4):
            try:
                safe_remove(dst)
                run_async(lambda: _edge_tts_async(text, dst, voice_id))
                if audio_ok(dst):
                    time.sleep(0.7)
                    return dst
            except Exception:
                pass
            safe_remove(dst)
            time.sleep(3 * (attempt + 1))
        return None

    def tts_one(args):
        i, c = args
        p = synth(c)
        if p: return (i, [p])
        # အပိုင်းကြီး fail ရင် ပိုသေးသေးခွဲပြီး ထပ်စမ်း
        subs = [synth(x) for x in split_scr(c, 120) if has_speech(x)]
        return (i, [s for s in subs if s])

    results = [[] for _ in chunks]; done = 0; skipped = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for idx, paths in ex.map(tts_one, enumerate(chunks)):
            results[idx] = paths; done += 1
            if not paths: skipped.append(idx)
            if cb: cb(done - 1, len(chunks), chunks[idx])

    good = [a for lst in results for a in lst]
    if not good:
        raise Exception("Edge TTS အသံမရပါ — edge-tts ကို update လုပ်ပါ (edge-tts>=7.0.0) / ခဏနေ ပြန်စမ်းပါ")
    if skipped:
        st.warning("⚠️ ကျော်လိုက်တဲ့ အပိုင်း: " + " | ".join(chunks[i][:30] for i in skipped))

    return concat_audio(good, out_path)


def tts_free(chunks, out, cb=None):
    """Edge TTS သီဟ"""
    edge_tts_run(chunks, out, cb=cb)
    st.success("✅ Edge TTS — 👨 သီဟ (Thiha)")
    return out


def tts_all(text, out, ref=None, cb=None, use_voxcpm=True):
    chunks = [c for c in split_scr(text, TTS_CHUNK) if has_speech(c)]
    if not chunks: raise Exception("TTS လုပ်စရာ စာမတွေ့ပါ")

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
            if not files or not all(audio_ok(f) for f in files):
                raise Exception("အသံ chunk ပျက်နေ")
            st.success("✅ VoxCPM2 — အောင်မြင်")
            break
        except Exception as e:
            st.warning(f"⚠️ VoxCPM2 — Fail: {str(e)[:80]}")
            files = None
            continue

    if files is None:
        st.warning("⚠️ VoxCPM2 — Busy/Fail — Edge TTS သီဟ Auto")
        return tts_free(chunks, out, cb=cb)

    try:
        return concat_audio(files, out)
    except Exception as e:
        st.warning(f"⚠️ VoxCPM2 audio ပေါင်းမရ ({str(e)[:60]}) — Edge TTS Auto")
        return tts_free(chunks, out, cb=cb)


# ==================== UI ====================

st.markdown("<div class='main-title'>🎬 Myanmar TTS Recap</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>Video → Script → VoxCPM2 / Edge TTS သီဟ → Recap</div>", unsafe_allow_html=True)
if not FONT_FOUND:
    st.warning(f"⚠️ Font မတွေ့ပါ ({FONT_FILE}) — Subtitle မြန်မာစာ မှန်မှန် မပြနိုင်ပါ")
st.divider()

if "script" not in st.session_state: st.session_state.script = ""
script = st.text_area("Script", value=st.session_state.script, height=180,
                       label_visibility="collapsed", placeholder="မြန်မာ Script paste...")
st.session_state.script = script

c1, c2 = st.columns([3, 1])
with c1: st.caption(f"📝 {len(script):,}")
with c2:
    if st.button("🗑️ Clear", use_container_width=True):
        st.session_state.script = ""; st.rerun()


def _canvas_body():
    st.link_button("↗️ Canvas ကို Tab အသစ်မှာ ဖွင့်", CANVAS_URL, use_container_width=True)
    pasted = st.text_area("Canvas ကနေ Copy → ဒီမှာ Paste", height=240, key="canvas_paste")
    if st.button("➡️ Script ထဲ ထည့်မယ်", key="canvas_apply", use_container_width=True):
        lines = [l.strip().strip('"\u201c\u201d').strip() for l in pasted.splitlines()]
        st.session_state.script = "\n\n".join(l for l in lines if l)
        st.rerun()


if hasattr(st, "dialog"):
    @st.dialog("📄 Gemini Canvas")
    def canvas_dialog():
        _canvas_body()

    if st.button("📄 Transcript ထုတ်ယူမယ် (Gemini Canvas)", use_container_width=True):
        canvas_dialog()
else:
    with st.expander("📄 Transcript ထုတ်ယူမယ် (Gemini Canvas)"):
        _canvas_body()
st.divider()

st.subheader("📁 Step 2 — Video")
vid = st.file_uploader("📹", type=["mp4","mov","avi","mkv"], label_visibility="collapsed")
if vid: st.success(f"✅ {vid.size/(1024*1024):.1f} MB")
st.divider()

# TikTok Neon Border — UI မပြဘဲ နောက်ကွယ်မှာ Auto (ပုံသေ)
use_neon = True
neon_animated = True
neon_thickness = 15
neon_speed = 0.4

# Step 4 — Subtitle
st.subheader("📝 Step 4 — Subtitle")
use_sub = st.toggle("Burn-in", value=True)
pos_y = 100
if use_sub:
    pos_y = st.slider("📍 Position", 0, 100, 100, 1)
st.divider()

# Step 5 — Preview
if vid:
    st.subheader("🖼️ Step 5 — Preview")
    try:
        with st.spinner("Preview..."):
            prev_video = wp("preview.mp4")
            pkey = (vid.name, vid.size)
            if st.session_state.get("pkey") != pkey or not os.path.exists(prev_video):
                vid.seek(0)
                with open(prev_video, "wb") as f: shutil.copyfileobj(vid, f)
                st.session_state.pkey = pkey

            cap = cv2.VideoCapture(prev_video)
            ok, fr = cap.read()
            cap.release()

            if not ok or fr is None:
                st.warning("⚠️ Preview frame ဖတ်မရ — Video ဖိုင်ကို စစ်ပါ")
            else:
                H, W = fr.shape[:2]       # တကယ်ဖတ်ရတဲ့ အရွယ်အစားနဲ့ တွက်
                prev_png = wp("prev.png")
                render_png("စာတန်းထိုး Preview", prev_png, FONT_FILE, W, H, FS, pos_y, BH, BA,
                           box_width_ratio=BOX_WIDTH_RATIO)

                bg = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).convert("RGBA")
                comp = bg
                if use_sub and os.path.exists(prev_png):
                    fg = Image.open(prev_png).convert("RGBA")
                    if fg.size != bg.size: fg = fg.resize(bg.size)
                    comp = Image.alpha_composite(bg, fg)

                if use_neon:
                    phase = 0.5 if neon_animated else 0.0
                    comp = draw_tiktok_border_preview(comp, thickness=neon_thickness,
                                                       animated_phase=phase)

                pw = 720
                out_png = wp("prev_out.png")
                comp.resize((pw, max(1, int(H * (pw / W)))), Image.LANCZOS).convert("RGB").save(out_png)
                st.image(out_png, use_container_width=True)
                if use_neon and neon_animated:
                    st.caption("🎬 Animated — Output Video မှာ အလင်းတန်း ပတ်ပြေးနေမည်")
    except Exception as e:
        st.warning(f"⚠️ Preview ပြမရ: {str(e)[:120]}")
st.divider()

# Step 6 — Generate
st.subheader("🚀 Step 6 — Generate")

use_voxcpm = st.toggle("🎙️ VoxCPM2 သုံးမလား?", value=True)

if use_voxcpm:
    st.info("✅ VoxCPM2 သုံးမယ် — Fail/Busy ရင် — Edge TTS သီဟ Auto")
    ref = st.file_uploader("🎤 Ref Audio (VoxCPM2) — Optional",
                            type=["wav","mp3","m4a"], key="ref_up")
    if ref:
        ref_path = wp("ref" + (os.path.splitext(ref.name)[1] or ".wav"))
        with open(ref_path, "wb") as f: f.write(ref.read())
        st.session_state.ref = ref_path
        st.success("✅ Ref Audio")
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
    if not has_speech(script_n): st.error("Script မှာ ပြောလို့ရတဲ့ စာမပါပါ"); st.stop()

    # ယခင် run ရဲ့ ကျန်ခဲ့တဲ့ ဖိုင်တွေ ရှင်း (stale output မပြအောင်)
    for fn in ("video_only.mp4", "voice.mp3", "final.mp4", "sub.srt", "whisper_audio.wav"):
        safe_remove(wp(fn))

    in_path = wp("input.mp4")
    vid.seek(0)
    with open(in_path, "wb") as f: shutil.copyfileobj(vid, f)
    try:
        vid_info(in_path)
        frame_size(in_path)
    except Exception as e:
        st.error(f"❌ Video ဖတ်မရ: {str(e)[:150]}"); st.stop()

    # ၁။ Cut + Render ကို Background မှာ — TTS နဲ့ တပြိုင်တည်း (အသံမပါ Video အရင်ထုတ်)
    fw_model = None
    if not USE_FAST_VAD:
        try: fw_model = get_fw_model()
        except Exception: pass
    job_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job = job_pool.submit(prepare_video_job, in_path, script_n, use_sub, fw_model,
                          pos_y, use_neon, neon_thickness, neon_speed)

    # ၂။ TTS
    t0 = time.time()
    pb = st.progress(0); txt = st.empty()
    def cb(i, tot, c):
        pb.progress(min(1.0, (i + 1) / max(tot, 1)))
        txt.caption(f"[{min(i + 1, tot)}/{tot}]")
    voice_path = wp("voice.mp3")
    try:
        tts_all(script_n, voice_path, ref=st.session_state.get("ref"),
                cb=cb, use_voxcpm=use_voxcpm)
        if not audio_ok(voice_path): raise Exception("အသံဖိုင် မထွက်ပါ")
    except Exception as e:
        job_pool.shutdown(wait=False)
        st.error(f"TTS: {e}"); st.stop()
    step_times["🎙️ TTS"] = time.time() - t0

    # ၃။ Background job စောင့် + အသံပေါင်း
    t0 = time.time()
    try:
        info = job.result()
    except Exception as e:
        job_pool.shutdown(wait=False)
        st.error(f"❌ Cut: {e}"); st.stop()
    job_pool.shutdown(wait=False)

    segments = info["segments"]          # None = Video အပြည့်
    if info.get("no_speech"):
        st.warning("⚠️ Video ထဲမှာ Speech မတွေ့/တိုလွန်း — Video အပြည့်ကို သုံးထားသည်")

    video_only = wp("video_only.mp4")
    render_ok = (not info["render_err"]) and os.path.exists(video_only) \
        and os.path.getsize(video_only) > 0

    adur = media_dur(voice_path)
    vdur = media_dur(video_only) if render_ok else 0.0
    if vdur <= 0: vdur = info["vdur"]     # Render ပြီးသား Video အရှည်ကို ဦးစားပေး
    tempo, out_of_range = calc_tempo(adur, vdur)
    if out_of_range:
        st.warning(f"⚠️ အသံ ({adur:.0f}s) နဲ့ Video ({vdur:.0f}s) အရှည် ကွာလွန်း — "
                   "Script ကို တိုစေ/ရှည်စေ ညှိရင် ပိုကောင်းမယ်")

    final_path = wp("final.mp4")
    with st.spinner("🎬 Finalizing..."):
        try:
            if not render_ok:
                raise Exception(info["render_err"] or "video_only မထွက်")
            mux_audio(video_only, voice_path, final_path, tempo)
        except Exception as e:
            st.warning(f"⚠️ Fail: {str(e)[:150]} — Simple mode နဲ့ ပြန်လုပ်နေ")
            try:
                simple_merge(in_path, voice_path, final_path, tempo, segments)
            except Exception as e2:
                st.error(f"❌ Render မအောင်မြင်: {str(e2)[:200]}"); st.stop()
    step_times["🎬 Render (စောင့်ချိန်)"] = time.time() - t0

    if not os.path.exists(final_path) or os.path.getsize(final_path) == 0:
        st.error("❌ Final video မထွက်ပါ"); st.stop()

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
    st.video(final_path)
    with open(final_path, "rb") as f:
        st.download_button("📥 Download", f, file_name="recap.mp4")
