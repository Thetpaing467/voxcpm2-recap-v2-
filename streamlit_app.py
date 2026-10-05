import streamlit as st
import os, re, ffmpeg, shutil, subprocess, asyncio, time
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
EDGE_CHUNK = 400   # chunk ကြီးလေ request နည်းလေ (rate limit လျော့)
TTS_WORKERS = 2
PNG_WORKERS = 4

CANVAS_URL = "https://gemini.google.com/share/a96d9ba3e76e"   # Gemini Canvas
GEMINI_MODEL = "gemini-3-flash-preview"   # Google docs ရဲ့ လက်ရှိ model
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

st.set_page_config(page_title="Myanmar TTS Recap", page_icon="🎬", layout="centered")

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

def vid_info(p):
    pr = ffmpeg.probe(p)
    v = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    return int(v['width']), int(v['height']), float(pr['format']['duration'])


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


def scr_to_srt(scr, dur, path, mc=30):
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


def overlay(vp, sp, op, fp, fs=30, pos_y=100, bh=100, ba=100,
            box_width_ratio=BOX_WIDTH_RATIO):
    W, H, _ = vid_info(vp); segs = parse_srt(sp)
    if not segs: raise Exception("SRT empty")
    os.makedirs("subtitle_pngs", exist_ok=True)

    def render_one(args):
        i, s = args
        p = f"subtitle_pngs/s_{i:04d}.png"
        render_png(s["text"], p, fp, W, H, fs, pos_y, bh, ba,
                   box_width_ratio=box_width_ratio)
        return i, {"p": p, "a": s["start"], "b": s["end"]}

    pngs = [None] * len(segs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=PNG_WORKERS) as ex:
        for idx, item in ex.map(render_one, enumerate(segs)):
            pngs[idx] = item

    cmd = ["ffmpeg","-y","-i",vp] + sum([["-i",x["p"]] for x in pngs], [])
    flt, cur = [], "[0:v]"
    for i, x in enumerate(pngs):
        lbl = f"[v{i}]"
        flt.append(f"{cur}[{i+1}:v]overlay=0:0:enable='between(t,{x['a']:.3f},{x['b']:.3f})'{lbl}")
        cur = lbl
    cmd += ["-filter_complex",";".join(flt),"-map",cur,"-map","0:a?",
            "-c:v","libx264","-crf",str(ENC_CRF),"-preset",ENC_PRESET,
            "-tune","fastdecode","-c:a","copy",op]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0: raise Exception(f"FFmpeg: {(r.stderr or '')[-500:]}")
    for x in pngs:
        try: os.remove(x["p"])
        except: pass
    return op


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


def video_bypass(input_video, output_video="bypass.mp4",
                 crop_ratio=0.95, mirror=True):
    W, H, dur = vid_info(input_video)
    filters = []

    if crop_ratio != 1.0:
        cw = int(W * crop_ratio); ch = int(H * crop_ratio)
        if cw % 2 != 0: cw -= 1
        if ch % 2 != 0: ch -= 1
        cx = (W - cw) // 2; cy = (H - ch) // 2
        filters.append(f"crop={cw}:{ch}:{cx}:{cy}")

    if mirror:
        filters.append("hflip")

    vf = ",".join(filters) if filters else "null"

    cmd = [
        "ffmpeg", "-y", "-i", input_video,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy", output_video
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")
    return output_video


# ==================== TikTok Neon Border (Static + Chase) ====================

def bypass_and_neon(input_video, output_video,
                    crop_ratio=0.95, mirror=True, thickness=30):
    """Bypass + Static Neon Border — ၃ လွှာ (Video မဖုံး)"""
    W, H, dur = vid_info(input_video)
    pad = thickness
    filters = []

    if crop_ratio != 1.0:
        cw = int(W * crop_ratio); ch = int(H * crop_ratio)
        if cw % 2 != 0: cw -= 1
        if ch % 2 != 0: ch -= 1
        cx = (W - cw) // 2; cy = (H - ch) // 2
        filters.append(f"crop={cw}:{ch}:{cx}:{cy}")

    if mirror:
        filters.append("hflip")

    filters.append(f"pad={W}:{H}:{pad}:{pad}:color=black@0")
    filters.append(f"drawbox=x=0:y=0:w={W}:h={H}:color={TIKTOK_BLACK}@1.0:t={pad}:replace=0")
    filters.append(f"drawbox=x=0:y=0:w={W}:h={H}:color={TIKTOK_CYAN}@1.0:t={max(3, pad//3)}:replace=0")
    filters.append(f"drawbox=x={pad}:y={pad}:w={W-2*pad}:h={H-2*pad}:color={TIKTOK_MAGENTA}@1.0:t={max(3, pad//3)}:replace=0")

    vf = ",".join(filters)

    cmd = [
        "ffmpeg", "-y", "-i", input_video,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy", output_video
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")
    return output_video


def bypass_and_neon_chase(input_video, output_video,
                          crop_ratio=0.95, mirror=True,
                          thickness=30, speed=1.0, tail=0.35):
    """Bypass + ပတ်ပြေးနေတဲ့ Neon အလင်းတန်း (comet / မြားဦး style)"""
    base = "bypass_base.mp4"
    video_bypass(input_video, base, crop_ratio, mirror)

    W, H, _ = vid_info(base)
    pr = ffmpeg.probe(base)
    vs = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    n, d = vs['r_frame_rate'].split('/')
    fps = float(n) / float(d)

    th = thickness
    P = 2 * (W + H)

    # Border ring pixel တွေရဲ့ ပတ်လမ်းအတိုင်း နေရာ (S) ကို တစ်ခါတည်း တွက်
    yy, xx = np.mgrid[0:H, 0:W]
    ring = (xx < th) | (xx >= W - th) | (yy < th) | (yy >= H - th)
    ys, xs = np.nonzero(ring)
    dt, db, dl, dr = ys, H - 1 - ys, xs, W - 1 - xs
    m = np.minimum.reduce([dt, db, dl, dr])
    S = np.where(m == dt, xs,
        np.where(m == dr, W + ys,
        np.where(m == db, 2 * W + H + (W - xs),
                 2 * W + 2 * H - ys))).astype(np.float32)

    BASE = np.array([15, 15, 15], np.float32)       # အနက်ရောင် အောက်ခံ
    CYAN = np.array([238, 244, 37], np.float32)     # BGR
    MAGENTA = np.array([85, 44, 254], np.float32)   # BGR

    def comet(head):
        dist = (head - S) % P                       # head ရဲ့ နောက်ဘက် ဝေးမှု
        a = np.clip(1.0 - dist / (tail * P), 0, 1)  # tail မှိန်သွား
        return (a ** 1.5)[:, None]

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-i", base,
        "-map", "0:v", "-map", "1:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest",
        output_video
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)

    cap = cv2.VideoCapture(base)
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok: break
        t = i / fps
        head = (t * speed * P / 4.0) % P            # speed=1 → ၄ စက္ကန့်/အပတ်
        c = BASE + CYAN * comet(head) + MAGENTA * comet(head + P / 2)
        fr[ys, xs] = np.clip(c, 0, 255).astype(np.uint8)
        proc.stdin.write(fr.tobytes())
        i += 1
    cap.release()
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise Exception("Chase Neon: ffmpeg fail")
    return output_video


def keep_count(video_in, segments):
    """Speech အပိုင်းထဲက frame အရေအတွက် (encode မလုပ်ဘဲ တွက်)"""
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
                 use_neon=True, thickness=15, speed=0.4, tail=0.35,
                 crop_ratio=0.95, mirror=True, segments=None):
    """Crop + Mirror + Chase Neon + Subtitle + Audio — encode တစ်ခါတည်း"""
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

    # ---- Subtitle PNG များကို အကြိုပြင် (parallel) ----
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

    # ---- Neon ring ကြိုတွက် ----
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
        BASE = np.array([15, 15, 15], np.float32)
        CYAN = np.array([238, 244, 37], np.float32)
        MAGENTA = np.array([85, 44, 254], np.float32)

        Si = (S.astype(np.int64)) % P
        P_arr = np.arange(P, dtype=np.float32)

        def comet_a(h):
            dist = (h - P_arr) % P
            return (np.clip(1.0 - dist / (tail * P), 0, 1) ** 1.5)[:, None]

        def make_strip(head):
            c_ = BASE + CYAN * comet_a(head) + MAGENTA * comet_a(head + P / 2)
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

    cap = cv2.VideoCapture(video_in)
    i = 0; j = 0; k = 0; p = 0
    try:
        while True:
            if not cap.grab(): break
            t_in = i / fps
            i += 1
            if segments:
                while p < len(segments) and segment