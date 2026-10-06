import streamlit as st
import os, re, ffmpeg, shutil, subprocess, asyncio, time, threading, queue
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
FINAL_CRF = 18
USE_FAST_VAD = True   # Whisper မသုံးဘဲ VAD နဲ့ speech ရှာ (အမြန်ဆုံး)
AUDIO_BITRATE = "128k"
TTS_CHUNK = 600
TTS_WORKERS = 5
PNG_WORKERS = 4

CANVAS_URL = "https://gemini.google.com/share/a96d9ba3e76e"   # Gemini Canvas
WHISPER_MODEL = "tiny"
WHISPER_LANG = "my"

BOX_WIDTH_RATIO = 1.0
PADDING_Y = 15
CORNER_RADIUS = 20

# ---- Video / Neon settings ----
CROP_RATIO = 0.95
MAX_LONG_SIDE = 1920      # ဒီထက်ကြီးတဲ့ video (4K) ကို အလိုအလျောက်ချုံ့ → ပိုမြန်
NEON_THICKNESS = 20       # border အထူ (px)
NEON_SPEED = 0.6          # ပတ်ပြေးနှုန်း (1.0 = ၄ စက္ကန့်/အပတ်)
NEON_TAIL = 0.45          # အလင်းတန်း အမြီးအရှည် (border ရဲ့ ၄၅%)

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


def vid_fps(p):
    try:
        pr = ffmpeg.probe(p)
        v = next(s for s in pr['streams'] if s['codec_type'] == 'video')
        n, d = v['r_frame_rate'].split('/')
        f = float(n) / float(d)
        if f > 0:
            return f
    except Exception:
        pass
    return 30.0


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


def render_rgba(text, fp, W, H, fs=30, pos_y=100, bh=100, ba=100,
                box_width_ratio=BOX_WIDTH_RATIO,
                padding_y=PADDING_Y, corner_radius=CORNER_RADIUS):
    """Subtitle ကို RGBA image အဖြစ် memory ထဲမှာပဲ ဆွဲ (disk မသုံး)"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
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
    if not lines: return None

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
        try:
            # stroke တစ်ခါတည်းနဲ့ outline ဆွဲ (နည်းဟောင်း ၂၅ ခါဆွဲတာထက် အများကြီးမြန်)
            d.text((lx, ty), ln, font=f, fill=(255, 255, 255, 255),
                   stroke_width=2, stroke_fill=(0, 0, 0, 255))
        except Exception:
            d.text((lx, ty), ln, font=f, fill=(255, 255, 255, 255))
        ty += lh
    return img


def render_png(text, out, fp, W, H, fs=30, pos_y=100, bh=100, ba=100,
               box_width_ratio=BOX_WIDTH_RATIO,
               padding_y=PADDING_Y, corner_radius=CORNER_RADIUS):
    img = render_rgba(text, fp, W, H, fs, pos_y, bh, ba,
                      box_width_ratio, padding_y, corner_radius)
    if img is None: return out
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


def split_scr(t, mc=TTS_CHUNK):
    sents = [s.strip()+"။" for s in t.replace("။","။|").split("|") if s.strip()]
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


# ==================== TikTok Neon (ပိုလင်း + ပိုမြန်) ====================

class NeonRing:
    """
    Border ring ပေါ်မှာ ပတ်ပြေးတဲ့ Cyan + Magenta အလင်းတန်း။
    - အရောင်ပုံစံ (comet pattern) ကို တစ်ခါတည်း ကြိုတွက်ထား
    - frame တစ်ခုချင်းမှာ index ကို shift လုပ်ပြီး ယူရုံပဲ → အရမ်းမြန်
    - ခေါင်း (head) မှာ အဖြူရောင် အလင်းပွင့် + အပြင်ဘက်ဆုံး အလွှာ ပိုလင်း
    """
    def __init__(self, W, H, thickness=NEON_THICKNESS, tail=NEON_TAIL):
        th = int(max(2, min(thickness, W // 2 - 1, H // 2 - 1)))
        P = 2 * (W + H)
        self.P = P

        mask = np.zeros((H, W), dtype=bool)
        mask[:th] = True; mask[-th:] = True
        mask[:, :th] = True; mask[:, -th:] = True
        ys, xs = np.nonzero(mask)

        dt, db, dl, dr = ys, H - 1 - ys, xs, W - 1 - xs
        m = np.minimum.reduce([dt, db, dl, dr])
        # ပတ်လမ်းတစ်လျှောက် ဆက်တိုက်နေရာ (clockwise): top → right → bottom → left
        S = np.where(m == dt, xs,
            np.where(m == dr, W + ys,
            np.where(m == db, 2 * W + H - 1 - xs,
                     2 * W + 2 * H - 1 - ys))).astype(np.int64) % P

        BASE = np.array([22, 22, 22], np.float32)
        CYAN = np.array([238, 244, 37], np.float32)      # BGR
        MAGENTA = np.array([85, 44, 254], np.float32)    # BGR

        pos = np.arange(P, dtype=np.float32)
        d1 = (-pos) % P                  # Cyan head = 0
        d2 = (P / 2.0 - pos) % P         # Magenta head = P/2

        def prof(dist, length, power):
            return np.clip(1.0 - dist / length, 0.0, 1.0) ** power

        a1 = prof(d1, tail * P, 0.7)[:, None]    # power နည်း = ပိုလင်း/ပိုကျယ်
        a2 = prof(d2, tail * P, 0.7)[:, None]
        w1 = prof(d1, 0.05 * P, 2.0)[:, None]    # အဖြူ အလင်းပွင့် (head)
        w2 = prof(d2, 0.05 * P, 2.0)[:, None]

        col = BASE[None, :] + CYAN[None, :] * a1 + MAGENTA[None, :] * a2 \
              + 255.0 * 0.85 * (w1 + w2)

        G = np.linspace(1.0, 0.6, th, dtype=np.float32)       # အပြင်ဘက် ပိုလင်း၊ အတွင်း မှိန်သွား
        pat = np.clip(col[None, :, :] * G[:, None, None], 0, 255).astype(np.uint8)  # (th, P, 3)
        self.pat = np.concatenate([pat, pat], axis=1).reshape(-1, 3)   # modulo မလိုအောင် ၂ ထပ်

        self.flat = (ys * W + xs).astype(np.int64)
        self.base_idx = (m * 2 * P + S + P).astype(np.int32)

    def apply(self, fr, head):
        """fr = BGR uint8, C-contiguous (in-place)"""
        s = int(head) % self.P
        fr.reshape(-1, 3)[self.flat] = self.pat.take(self.base_idx - s, axis=0)


def draw_neon_preview(img, thickness=NEON_THICKNESS):
    """Preview frame ပေါ်မှာ Output နဲ့ တူတဲ့ Neon ဆွဲ"""
    rgb = np.array(img.convert("RGB"))
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    H, W = bgr.shape[:2]
    ring = NeonRing(W, H, thickness)
    ring.apply(bgr, ring.P * 0.15)
    return Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1])).convert("RGBA")


def keep_count(video_in, segments):
    """Speech အပိုင်းထဲက frame အရေအတွက် (encode မလုပ်ဘဲ တွက်)"""
    fps = vid_fps(video_in)
    pr = ffmpeg.probe(video_in)
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
                 use_neon=True, thickness=NEON_THICKNESS, speed=NEON_SPEED,
                 tail=NEON_TAIL, crop_ratio=CROP_RATIO, mirror=True, segments=None):
    """
    Crop + Mirror + Neon + Subtitle + Audio — encode တစ်ခါတည်း
    Pipeline (၃ thread): [Decode+Crop+Flip] → [Neon+Subtitle] → [ffmpeg ထဲ ရေး]
    """
    fps = vid_fps(video_in)

    # cv2 က တကယ်ထုတ်ပေးမယ့် frame အရွယ်ကို အတိအကျသိဖို့ (rotation ပြဿနာ မဖြစ်စေ)
    pc = cv2.VideoCapture(video_in)
    ok0, f0 = pc.read()
    pc.release()
    if not ok0 or f0 is None:
        raise Exception("Video ဖတ်မရ")
    H0, W0 = f0.shape[:2]
    del f0

    if crop_ratio != 1.0:
        cw = int(W0 * crop_ratio); ch = int(H0 * crop_ratio)
        cw -= cw % 2; ch -= ch % 2
        cx = (W0 - cw) // 2; cy = (H0 - ch) // 2
    else:
        cw, ch = W0 - W0 % 2, H0 - H0 % 2
        cx = cy = 0

    scale = 1.0
    if MAX_LONG_SIDE and max(cw, ch) > MAX_LONG_SIDE:
        scale = MAX_LONG_SIDE / float(max(cw, ch))
    W = max(2, int(cw * scale) // 2 * 2)
    H = max(2, int(ch * scale) // 2 * 2)
    do_resize = (W != cw or H != ch)

    # ---- Subtitle ကြိုပြင် (parallel, memory ထဲမှာပဲ) ----
    subs = []
    if srt_path:
        segs = parse_srt(srt_path)

        def prep(args):
            i, sg = args
            img = render_rgba(sg["text"], fp, W, H, fs, pos_y, bh, ba,
                              box_width_ratio=BOX_WIDTH_RATIO)
            if img is None: return None
            arr = np.asarray(img)                      # RGBA
            rows = np.where(arr[:, :, 3].any(axis=1))[0]
            if len(rows) == 0: return None
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
            crop = arr[y0:y1]
            bgra = np.ascontiguousarray(crop[:, :, [2, 1, 0, 3]])
            return {"a": sg["start"], "b": sg["end"], "y0": y0, "y1": y1, "bgra": bgra}

        with concurrent.futures.ThreadPoolExecutor(max_workers=PNG_WORKERS) as ex:
            subs = [x for x in ex.map(prep, enumerate(segs)) if x]

    ring = NeonRing(W, H, thickness, tail) if use_neon else None

    # ---- ffmpeg (raw frame လက်ခံ) ----
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", f"{fps:.5f}", "-i", "-",
    ]
    if audio_in:
        cmd += ["-i", audio_in, "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", str(FINAL_CRF), "-preset", FINAL_PRESET,
            "-pix_fmt", "yuv420p", "-threads", "0"]
    if audio_in:
        cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest"]
    cmd += ["-movflags", "+faststart", output_video]

    logf = open("final_ffmpeg.log", "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=logf,
                            bufsize=W * H * 3 * 2)

    q_in = queue.Queue(maxsize=6)
    q_out = queue.Queue(maxsize=6)
    stop = threading.Event()
    err = {}

    def put(q, item):
        while not stop.is_set():
            try:
                q.put(item, timeout=0.5)
                return True
            except queue.Full:
                pass
        return False

    def reader():
        cap = cv2.VideoCapture(video_in)
        i = 0; p = 0
        try:
            while not stop.is_set():
                if not cap.grab(): break
                t_in = i / fps
                i += 1
                if segments:
                    while p < len(segments) and segments[p][1] < t_in: p += 1
                    if p >= len(segments): break          # Speech ကုန်ပြီ → ကျန်တာ မဖတ်တော့
                    if segments[p][0] > t_in: continue    # Speech မဟုတ်တဲ့ frame ကျော်
                ok, fr = cap.retrieve()
                if not ok or fr is None: break
                fr = fr[cy:cy + ch, cx:cx + cw]
                fr = cv2.flip(fr, 1) if mirror else np.ascontiguousarray(fr)
                if do_resize:
                    fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA)
                fr = np.ascontiguousarray(fr)
                if not put(q_in, fr): break
        except Exception as e:
            err["r"] = e
        finally:
            cap.release()
            put(q_in, None)

    def writer():
        try:
            while True:
                try:
                    fr = q_out.get(timeout=0.5)
                except queue.Empty:
                    if stop.is_set(): return
                    continue
                if fr is None: break
                proc.stdin.write(fr.tobytes())
        except Exception as e:
            err["w"] = e
            stop.set()

    rt = threading.Thread(target=reader, daemon=True)
    wt = threading.Thread(target=writer, daemon=True)
    rt.start(); wt.start()

    j = 0; k = 0; cur_k = -1; inv = pre = None
    try:
        while True:
            try:
                fr = q_in.get(timeout=0.5)
            except queue.Empty:
                if stop.is_set(): break
                continue
            if fr is None: break

            t = j / fps

            # Subtitle
            while k < len(subs) and subs[k]["b"] < t: k += 1
            if k < len(subs) and subs[k]["a"] <= t:
                sb = subs[k]
                if cur_k != k:
                    al = sb["bgra"][:, :, 3:4].astype(np.float32) / 255.0
                    pre = sb["bgra"][:, :, :3].astype(np.float32) * al
                    inv = 1.0 - al
                    cur_k = k
                reg = fr[sb["y0"]:sb["y1"]].astype(np.float32)
                reg *= inv
                reg += pre
                fr[sb["y0"]:sb["y1"]] = reg.astype(np.uint8)

            # Neon (Subtitle ပေါ်မှာ ထပ်ဆွဲ → border အမြဲ ပေါ်နေမယ်)
            if ring is not None:
                ring.apply(fr, t * speed * ring.P / 4.0)

            if not put(q_out, fr): break
            j += 1
        put(q_out, None)
        wt.join()
    finally:
        stop.set()
        rt.join(timeout=10)
        wt.join(timeout=10)
        try: proc.stdin.close()
        except Exception: pass
        proc.wait()
        logf.close()

    if "r" in err: raise Exception(f"Read: {err['r']}")
    if "w" in err and proc.returncode != 0:
        raise Exception(f"Write: {err['w']}")
    if j == 0: raise Exception("Frame မရ")
    if proc.returncode != 0:
        try:
            with open("final_ffmpeg.log", "rb") as lf:
                tail_txt = lf.read()[-300:].decode("utf-8", "ignore")
        except Exception:
            tail_txt = ""
        raise Exception(f"Final render: ffmpeg fail {tail_txt}")
    return output_video


def mux_audio(video_in, audio_in, output_video, tempo):
    """Video ကို ပြန် encode မလုပ်ဘဲ အသံပေါင်းပေး (copy)"""
    cmd = ["ffmpeg", "-y", "-i", video_in, "-i", audio_in,
           "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
           "-shortest", "-movflags", "+faststart", output_video]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"Mux: {(r.stderr or '')[-300:]}")
    return output_video


def prepare_video_job(video_in, script_text, use_sub, fw_model,
                      pos_y, use_neon, thickness, speed):
    """Background: Speech ရှာ → Subtitle → အသံမပါ Video render (TTS နဲ့ တပြိုင်တည်း)"""
    segments = whisper_fast(video_in, fw_model)
    if not segments: raise Exception("Speech မတွေ့")
    kept, vfps = keep_count(video_in, segments)
    if kept == 0: raise Exception("Speech မတွေ့")
    vdur = kept / vfps
    render_err = None
    try:
        sp = scr_to_srt(script_text, vdur, "sub.srt") if use_sub else None
        final_render(video_in, None, "video_only.mp4", 1.0,
                     srt_path=sp, fp=FONT_FILE, fs=FS, pos_y=pos_y,
                     bh=BH, ba=BA, use_neon=use_neon,
                     thickness=thickness, speed=speed, segments=segments)
    except Exception as e:
        render_err = str(e)
    return {"segments": segments, "vdur": vdur, "render_err": render_err}


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

    def tts_one(args):
        i, c = args
        dst = f"edge_chunk_{i}.mp3"
        last = None
        for _ in range(3):                       # network error ရှိရင် ၃ ကြိမ်အထိ ထပ်စမ်း
            try:
                asyncio.run(_edge_tts_async(c, dst, voice_id))
                return (i, dst)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(_edge_tts_async(c, dst, voice_id))
                    return (i, dst)
                except Exception as e:
                    last = e
                finally:
                    loop.close()
            except Exception as e:
                last = e
            time.sleep(0.7)
        raise last if last else Exception("Edge TTS fail")

    results = [None] * len(chunks); done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for idx, dst in ex.map(tts_one, enumerate(chunks)):
            results[idx] = dst; done += 1
            if cb: cb(done - 1, len(chunks), chunks[idx])

    with open("edge_concat.txt", "w", encoding="utf-8") as f:
        for a in results: f.write(f"file '{a}'\n")

    ffmpeg.input("edge_concat.txt", format="concat", safe=0).output(
        out_path, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
    ).run(overwrite_output=True, quiet=True)
    return out_path


def tts_all(text, out, ref=None, cb=None, use_voxcpm=True):
    chunks = split_scr(text, TTS_CHUNK)

    if not use_voxcpm:
        st.info("⚡ Edge TTS သီဟ — VoxCPM2 Off")
        edge_tts_run(chunks, out, cb=cb)
        st.success("✅ Edge TTS — 👨 သီဟ (Thiha)")
        return out

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
        edge_tts_run(chunks, out, cb=cb)
        st.success("✅ Edge TTS — 👨 သီဟ (Thiha)")
        return out

    with open("concat.txt", "w", encoding="utf-8") as f:
        for a in files: f.write(f"file '{a}'\n")
    ffmpeg.input("concat.txt", format="concat", safe=0).output(
        out, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
    ).run(overwrite_output=True, quiet=True)
    return out


@st.cache_resource(show_spinner=False)
def get_fw_model():
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                        cpu_threads=os.cpu_count() or 4)


def whisper_fast(video_path, model=None):
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path, "-vn",
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
            pass   # VAD မရရင် Whisper နဲ့ ဆက်သွား

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
st.markdown("<div class='main-sub'>Video → Script → VoxCPM2 / Edge TTS သီဟ → Recap</div>", unsafe_allow_html=True)
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
st.link_button("📄 Transcript ထုတ်ယူမယ် (Gemini Canvas)", CANVAS_URL,
               use_container_width=True)
st.divider()

st.subheader("📁 Step 2 — Video")
vid = st.file_uploader("📹", type=["mp4","mov","avi","mkv"], label_visibility="collapsed")
if vid: st.success(f"✅ {vid.size/(1024*1024):.1f} MB")
st.divider()

# TikTok Neon Border — UI မပြဘဲ နောက်ကွယ်မှာ Auto (ပုံသေ)
use_neon = True
neon_animated = True
neon_thickness = NEON_THICKNESS
neon_speed = NEON_SPEED

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
        ok, fr = cap.read()
        cap.release()

        if ok:
            bg = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).convert("RGBA")
            fg = Image.open("prev.png").convert("RGBA")
            comp = Image.alpha_composite(bg, fg)

            if use_neon:
                comp = draw_neon_preview(comp, thickness=neon_thickness)

            pw = 720
            comp.resize((pw, int(comp.size[1] * (pw / comp.size[0]))), Image.LANCZOS).convert("RGB").save("prev_out.png")
            st.image("prev_out.png", use_container_width=True)
            if use_neon and neon_animated:
                st.caption("🎬 Animated — Output Video မှာ အလင်းတန်း ပတ်ပြေးနေမည်")
st.divider()

# Step 6 — Generate
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
        st.session_state.ref = None
else:
    st.info("⚡ Edge TTS သီဟ (Thiha) — ပဲ သုံးမယ်")
    st.session_state.ref = None

if st.button("✨ Generate Recap Video", type="primary", use_container_width=True):
    if not script.strip(): st.error("Script paste"); st.stop()
    if vid is None: st.error("Video Upload"); st.stop()

    total_start = time.time(); step_times = {}
    vid.seek(0)
    with open("input.mp4", "wb") as f: f.write(vid.read())
    _, _, vdur = vid_info("input.mp4")

    # ၁။ Cut + Render ကို Background မှာ — TTS နဲ့ တပြိုင်တည်း (အသံမပါ Video အရင်ထုတ်)
    fw_model = None
    if not USE_FAST_VAD:
        try: fw_model = get_fw_model()
        except Exception: pass
    job_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job = job_pool.submit(prepare_video_job, "input.mp4", script, use_sub, fw_model,
                          pos_y, use_neon, neon_thickness, neon_speed)

    # ၂။ TTS
    t0 = time.time()
    pb = st.progress(0); txt = st.empty()
    def cb(i, tot, c): pb.progress(min(1.0, (i+1)/tot)); txt.caption(f"[{i+1}/{tot}]")
    try:
        tts_all(script, "voice.mp3", ref=st.session_state.get("ref"),
                cb=cb, use_voxcpm=use_voxcpm)
    except Exception as e:
        st.error(f"TTS: {e}"); st.stop()
    step_times["🎙️ TTS"] = time.time() - t0

    # ၃။ Background job စောင့် + အသံပေါင်း
    t0 = time.time()
    try:
        info = job.result()
    except Exception as e:
        st.error(f"❌ Cut: {e}"); st.stop()
    job_pool.shutdown(wait=False)
    segments, vdur = info["segments"], info["vdur"]

    adur = float(ffmpeg.probe("voice.mp3")['format']['duration'])
    tempo = max(0.5, min(2.0, adur/vdur))

    with st.spinner("🎬 Finalizing..."):
        try:
            if info["render_err"]: raise Exception(info["render_err"])
            mux_audio("video_only.mp4", "voice.mp3", "final.mp4", tempo)
        except Exception as e:
            st.warning(f"⚠️ Fail: {e}")
            simple_merge("input.mp4", "voice.mp3", "final.mp4", tempo, segments)
    step_times["🎬 Render (စောင့်ချိန်)"] = time.time() - t0

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
        st.download_button("📥 Download", f, file_name="recap.mp4")
