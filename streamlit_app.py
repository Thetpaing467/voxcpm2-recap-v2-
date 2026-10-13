import streamlit as st
import os, re, subprocess, time, math, threading, queue, bisect
import concurrent.futures
from functools import lru_cache
import ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2

os.environ["HF_HOME"] = "/tmp/hf_cache"

PASSWORD = "voxcpm2026"
FONT_FILE = "MyanmarPadaung.ttf"

FS, BH, BA = 30, 100, 200
AUDIO_BITRATE = "128k"
PNG_WORKERS = 4
USE_FAST_VAD = True

CANVAS_URL = "https://gemini.google.com/share/a96d9ba3e76e"
WHISPER_MODEL = "tiny"
WHISPER_LANG = "my"

BOX_WIDTH_RATIO = 1.0
PADDING_Y = 15
CORNER_RADIUS = 20
FINAL_PRESET = "ultrafast"
FINAL_CRF = 20

SUB_MAX_CHARS = 30      # စာတန်းတစ်ကြောင်း အများဆုံး စာလုံး
SUB_HOLD = 0.25         # စာတန်း အသံပြီးနောက် ဆက်ပြချိန် (sec)
AUDIO_VAD_SILENCE_MS = 200

st.set_page_config(page_title="Myanmar TTS Recap", page_icon="🎬", layout="centered")


def _on_download_clear():
    st.session_state.script = ""
    st.session_state.last_paste = ""
    st.session_state.paste_big = ""
    st.session_state.pkey = None
    st.session_state.video_up_key = st.session_state.get("video_up_key", 0) + 1
    st.session_state.audio_up_key = st.session_state.get("audio_up_key", 0) + 1


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
        pwd = st.text_input("Password", type="password",
                            label_visibility="collapsed", placeholder="Password")
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
if "audio_up_key" not in st.session_state: st.session_state.audio_up_key = 0


# ==================== Utility Functions ====================

def vid_info(p):
    pr = ffmpeg.probe(p)
    v = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    return int(v['width']), int(v['height']), float(pr['format']['duration'])


def vid_meta(p):
    """W, H, fps(float), fps(exact 'n/d' string), frame count"""
    pr = ffmpeg.probe(p)
    v = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    n, d = v['r_frame_rate'].split('/')
    fps = float(n) / float(d)
    dur = float(pr['format']['duration'])
    nbf = str(v.get('nb_frames', ''))
    nb = int(nbf) if nbf.isdigit() else int(round(dur * fps))
    return int(v['width']), int(v['height']), fps, f"{n}/{d}", nb


def audio_dur(p):
    return float(ffmpeg.probe(p)['format']['duration'])


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


# ---------- Subtitle image (strip only, no full-frame PNG / disk I/O) ----------

@lru_cache(maxsize=8)
def get_font(fp, fs):
    try: return ImageFont.truetype(fp, fs)
    except Exception: return ImageFont.load_default()


def wrap_px(text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}" if cur else w
        if not cur or font.getlength(t) <= max_w: cur = t
        else:
            lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines


def build_sub_layer(text, fp, W, H, fs=FS, pos_y=100, ba=BA,
                    box_width_ratio=BOX_WIDTH_RATIO,
                    padding_y=PADDING_Y, corner_radius=CORNER_RADIUS):
    f = get_font(fp, fs)
    box_w = int(W * box_width_ratio)
    lines = wrap_px(text, f, box_w * 0.92)
    if not lines: return None
    lh = int(fs * 1.3)
    box_h = min(len(lines) * lh + padding_y * 2, H)
    max_y = H - box_h
    y0 = max(0, min(int((pos_y / 100) * max_y), max_y))
    box_x = (W - box_w) // 2

    img = Image.new("RGBA", (W, box_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([box_x, 0, box_x + box_w - 1, box_h - 1],
                        radius=corner_radius, fill=(0, 0, 0, ba))
    ty = padding_y
    for ln in lines:
        lx = box_x + (box_w - d.textlength(ln, font=f)) / 2
        d.text((lx, ty), ln, font=f, fill=(255, 255, 255, 255),
               stroke_width=2, stroke_fill=(0, 0, 0, 255))
        ty += lh
    return y0, img


def render_png(text, out, fp, W, H, fs=30, pos_y=100, bh=100, ba=100,
               box_width_ratio=BOX_WIDTH_RATIO,
               padding_y=PADDING_Y, corner_radius=CORNER_RADIUS):
    full = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    r = build_sub_layer(text, fp, W, H, fs, pos_y, ba, box_width_ratio,
                        padding_y, corner_radius)
    if r:
        y0, img = r
        full.paste(img, (0, y0))
    full.save(out, "PNG")
    return out


# ---------- Subtitle ↔ Audio alignment ----------

def split_sentences(t):
    return [s.strip() for s in re.split(r'(?<=[။!?])\s+', t) if s.strip()]


def split_parts(sentence, mc=SUB_MAX_CHARS):
    parts, cur = [], ""
    for w in sentence.split():
        if cur and len(cur) + len(w) + 1 > mc:
            parts.append(cur); cur = w
        else:
            cur = f"{cur} {w}" if cur else w
        if w.endswith("၊"):
            parts.append(cur); cur = ""
    if cur: parts.append(cur)
    return parts


def weight(s):
    # consonant/letter count ≈ syllable count (ပြောနှုန်းနဲ့ ပိုကိုက်)
    return max(1, len(re.findall(r'\w', s)))


def make_speech_map(segs):
    cum = [0.0]
    for a, b in segs: cum.append(cum[-1] + (b - a))

    def f(u, end=False):
        u = max(0.0, min(u, cum[-1]))
        i = (bisect.bisect_left(cum, u) - 1) if end else (bisect.bisect_right(cum, u) - 1)
        i = max(0, min(i, len(segs) - 1))
        a, b = segs[i]
        return a + min(max(u - cum[i], 0.0), b - a)
    return f, cum[-1]


def distribute(parts, segs):
    """parts ကို အသံပါတဲ့ အပိုင်းတွေပေါ်မှာပဲ (silence ကျော်) အချိုးကျ ခွဲ"""
    f, U = make_speech_map(segs)
    ws = [weight(p) for p in parts]
    tot = float(sum(ws))
    out, c = [], 0
    for p, w in zip(parts, ws):
        out.append((f(U * c / tot), f(U * (c + w) / tot, True), p))
        c += w
    return out


def align_subs(sents, segs, total):
    """sents: [[part,...],...]  segs: output-time speech segments"""
    sents = [s for s in sents if s]
    if not sents: return []
    segs = sorted((max(0.0, a), min(total, b)) for a, b in segs if b > a)
    if not segs: segs = [(0.0, total)]
    S = len(sents)
    res = []
    if S > 1 and len(segs) >= S:
        # အကြီးဆုံး ရပ်ကွက် (S-1) ခုကို ဝါကျအဆုံးအဖြစ် anchor လုပ်
        gaps = sorted(((segs[i + 1][0] - segs[i][1], i)
                       for i in range(len(segs) - 1)), reverse=True)[:S - 1]
        cuts = sorted(i for _, i in gaps)
        start = 0
        for k, end in enumerate(cuts + [len(segs) - 1]):
            res += distribute(sents[k], segs[start:end + 1])
            start = end + 1
    else:
        res = distribute([p for s in sents for p in s], segs)

    subs = []
    for i, (a, b, t) in enumerate(res):
        nxt = res[i + 1][0] if i + 1 < len(res) else total
        b2 = min(b + SUB_HOLD, nxt) if nxt >= b else b
        subs.append({"start": a, "end": max(b2, a + 0.05), "text": t})
    return subs


# ---------- Speech detect ----------

def load_audio_16k(path):
    # PyAV version မကိုက်ညီမှု (metadata_errors) ကို ရှောင်ရန် ffmpeg pipe နဲ့ တိုက်ရိုက် decode
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
         "-f", "f32le", "-"], capture_output=True, check=True)
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    if audio.size == 0:
        raise Exception("Audio မတွေ့")
    return audio


def vad_segments(audio, min_silence_ms, pad_ms):
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    ts = get_speech_timestamps(
        audio, VadOptions(min_silence_duration_ms=min_silence_ms, speech_pad_ms=pad_ms))
    return [(t["start"] / 16000.0, t["end"] / 16000.0) for t in ts]


@st.cache_resource(show_spinner=False)
def get_fw_model():
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                        cpu_threads=os.cpu_count() or 4)


def whisper_fast(video_path, model=None, audio=None):
    if USE_FAST_VAD:
        try:
            if audio is None: audio = load_audio_16k(video_path)
            segs = vad_segments(audio, 700, 200)
            if segs: return segs
        except Exception:
            pass

    subprocess.run([
        "ffmpeg", "-y", "-i", video_path, "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", "whisper_audio.wav"
    ], capture_output=True, check=True)

    speech_segments = []
    try:
        model = model or get_fw_model()
        segments, _ = model.transcribe(
            "whisper_audio.wav", language=WHISPER_LANG,
            vad_filter=False, beam_size=1,
            condition_on_previous_text=False, temperature=0)
        for seg in segments:
            speech_segments.append((seg.start, seg.end))
    except Exception:
        import whisper
        model = whisper.load_model(WHISPER_MODEL)
        result = model.transcribe(
            "whisper_audio.wav", language=WHISPER_LANG,
            condition_on_previous_text=False, beam_size=1, temperature=0)
        for seg in result["segments"]:
            speech_segments.append((seg["start"], seg["end"]))
    return speech_segments


def keep_mask(segments, fps, total):
    """render နဲ့ duration တွက်ရာမှာ တူညီတဲ့ frame mask (တိကျစေရန်)"""
    t = np.arange(total) / fps
    m = np.zeros(total, dtype=bool)
    if not segments: return ~m
    segs = np.array(sorted(segments))
    idx = np.searchsorted(segs[:, 0], t, side='right') - 1
    ok = idx >= 0
    m[ok] = t[ok] <= segs[idx[ok], 1]
    return m


def prepare_video_job(video_in, fw_model, audio=None):
    segments = whisper_fast(video_in, fw_model, audio)
    if not segments: raise Exception("Speech မတွေ့")
    _, _, fps, _, nb = vid_meta(video_in)
    kept = int(keep_mask(segments, fps, nb).sum())
    if kept == 0: raise Exception("Speech မတွေ့")
    return {"segments": segments, "vdur": kept / fps}


# ---------- Render ----------

def simple_merge(video_in, audio_in, output_video, tempo, segments=None):
    vf = []
    if segments:
        sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segments)
        vf = ["-vf", f"select='{sel}',setpts=N/FRAME_RATE/TB"]
    cmd = ["ffmpeg", "-y", "-i", video_in, "-i", audio_in,
           "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a"] + vf + [
           "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
           "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest", output_video]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")
    return output_video


def final_render(video_in, audio_in, output_video, tempo,
                 subs=None, fp=FONT_FILE, fs=FS, pos_y=100, bh=BH, ba=BA,
                 use_neon=True, thickness=15, speed=0.4, tail=0.5,
                 crop_ratio=1.0, mirror=True, segments=None, target_dur=None):
    W0, H0, fps, fps_str, nb = vid_meta(video_in)

    if crop_ratio != 1.0:
        cw = int(W0 * crop_ratio); ch = int(H0 * crop_ratio)
        if cw % 2: cw -= 1
        if ch % 2: ch -= 1
        cx = (W0 - cw) // 2; cy = (H0 - ch) // 2
    else:
        cw, ch, cx, cy = W0, H0, 0, 0
    W, H = cw, ch

    mask = keep_mask(segments, fps, int(nb + fps * 2)) if segments else None
    target_frames = int(math.ceil(target_dur * fps)) if (target_dur and audio_in) else None

    # ---- Subtitle layers (strip only, parallel) ----
    layers = []
    if subs:
        def prep(sg):
            r = build_sub_layer(sg["text"], fp, W, H, fs, pos_y, ba,
                                box_width_ratio=BOX_WIDTH_RATIO)
            if r is None: return None
            y0, img = r
            arr = np.asarray(img)
            alpha = arr[:, :, 3:4].astype(np.uint16)
            bgr = arr[:, :, 2::-1].astype(np.uint16)
            return {"a": sg["start"], "b": sg["end"], "y0": y0,
                    "y1": y0 + arr.shape[0],
                    "inv": 255 - alpha, "pre": bgr * alpha}

        with concurrent.futures.ThreadPoolExecutor(max_workers=PNG_WORKERS) as ex:
            layers = [x for x in ex.map(prep, subs) if x]
        layers.sort(key=lambda s: s["a"])

    # ---- Neon ----
    if use_neon:
        th = thickness
        P = 2 * (W + H)
        yy, xx = np.mgrid[0:H, 0:W]
        ring = (xx < th) | (xx >= W - th) | (yy < th) | (yy >= H - th)
        ys, xs = np.nonzero(ring)
        flat = ys * W + xs
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

    # ---- ffmpeg encoder (video + audio တစ်ကြိမ်တည်း) ----
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", fps_str, "-i", "-"]
    if audio_in:
        cmd += ["-i", audio_in, "-af", f"atempo={tempo:.6f}",
                "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", str(FINAL_CRF), "-preset", FINAL_PRESET,
            "-pix_fmt", "yuv420p", "-threads", "0"]
    if audio_in:
        cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest"]
    cmd += ["-movflags", "+faststart", output_video]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    q_in = queue.Queue(maxsize=16)
    q_out = queue.Queue(maxsize=16)
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

    def reader():
        cap = cv2.VideoCapture(video_in)
        i = 0
        try:
            while not stop.is_set():
                if not cap.grab(): break
                idx = i; i += 1
                if mask is not None and (idx >= len(mask) or not mask[idx]):
                    continue
                ok, fr = cap.retrieve()
                if not ok: break
                if not qput(q_in, fr): break
        except Exception as e:
            err.append(e); stop.set()
        finally:
            cap.release()
            qput(q_in, None)

    def writer():
        try:
            while True:
                fr = qget(q_out)
                if fr is None: break
                proc.stdin.write(fr.data)
        except Exception as e:
            err.append(e); stop.set()

    th_r = threading.Thread(target=reader, daemon=True)
    th_w = threading.Thread(target=writer, daemon=True)
    th_r.start(); th_w.start()

    k = 0

    def process(raw, j):
        nonlocal k
        t = j / fps
        crop = raw[cy:cy + ch, cx:cx + cw]
        fr = cv2.flip(crop, 1) if mirror else crop.copy()
        if use_neon:
            head = (t * speed * P / 4.0) % P
            fr.reshape(-1, 3)[flat] = make_strip(head)[Si]
        while k < len(layers) and layers[k]["b"] < t: k += 1
        if k < len(layers) and layers[k]["a"] <= t:
            sb = layers[k]
            roi = fr[sb["y0"]:sb["y1"]].astype(np.uint16)
            fr[sb["y0"]:sb["y1"]] = ((roi * sb["inv"] + sb["pre"]) // 255).astype(np.uint8)
        return fr

    j = 0
    last_raw = None
    try:
        while True:
            raw = qget(q_in)
            if raw is None: break
            last_raw = raw
            if not qput(q_out, process(raw, j)): break
            j += 1
        # အသံက ဗီဒီယိုထက်ရှည်ရင် နောက်ဆုံး frame ကို ဆက်ထား (အသံမဖြတ်မိစေရန်)
        if target_frames and last_raw is not None and not stop.is_set():
            while j < target_frames:
                if not qput(q_out, process(last_raw, j)): break
                j += 1
    except Exception as e:
        err.append(e); stop.set()
    finally:
        if not stop.is_set(): qput(q_out, None)
        th_w.join()
        stop.set()
        th_r.join()
        try: proc.stdin.close()
        except Exception: pass
        proc.wait()
    if err: raise err[0]
    if proc.returncode != 0:
        raise Exception("Final render: ffmpeg fail")
    return output_video


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


# ==================== UI ====================

st.markdown("<div class='main-title'>🎬 Myanmar TTS Recap</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>Video + Script + Voice Audio (MP3) → Recap</div>",
            unsafe_allow_html=True)
st.divider()

# ==================== Step 1 — Transcript ====================
with st.expander("📄 Transcript ထုတ်ယူမယ် (Gemini Canvas)", expanded=False):
    st.link_button("↗️ Canvas ကို Tab အသစ်မှာ ဖွင့်", CANVAS_URL,
                   use_container_width=True)
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

pasted_now = st.text_area(
    "paste",
    value="",
    height=80,
    key="paste_big",
    label_visibility="collapsed",
    placeholder="📋  ဒီနေရာကို Long-press → Paste  (သို့)  Ctrl+V",
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

if pasted_now and pasted_now.strip():
    clean = pasted_now.strip()
    if clean != st.session_state.get("last_paste", ""):
        st.session_state.last_paste = clean
        lines = [l.strip().strip('"\u201c\u201d').strip() for l in clean.splitlines()]
        result = "\n\n".join(l for l in lines if l)
        st.session_state.script = result
        st.session_state.paste_big = ""
        st.success(f"✅ Script ထဲ Auto ရောက်သွားပြီ — {len(result)} လုံး")
        st.rerun()

script = st.text_area("Script", value=st.session_state.script, height=180,
                      label_visibility="collapsed",
                      placeholder="မြန်မာ Script paste...")
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
    type=["mp4", "mov", "avi", "mkv"],
    label_visibility="collapsed",
    key=f"video_up_{st.session_state.video_up_key}"
)
if vid: st.success(f"✅ {vid.size/(1024*1024):.1f} MB")
st.divider()

use_neon = True
neon_animated = True
neon_thickness = 15
neon_speed = 0.4

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
                phase = 0.5 if neon_animated else 0.0
                comp = draw_tiktok_border_preview(comp, thickness=neon_thickness,
                                                  animated_phase=phase)

            pw = 720
            comp.resize((pw, int(H * (pw / W))), Image.LANCZOS).convert("RGB").save("prev_out.png")
            st.image("prev_out.png", use_container_width=True)
            if use_neon and neon_animated:
                st.caption("🎬 Animated — Output Video မှာ အလင်းတန်း ပတ်ပြေးနေမည်")
st.divider()

# ==================== Step 6 — Upload Voice Audio ====================
st.subheader("🎵 Step 6 — Upload Voice Audio")
st.caption("MS-Voice-TTS / SpeechGen / Azure / Edge TTS ကနေ ထုတ်ထားတဲ့ "
           "MP3 ဖိုင် upload လုပ်ပါ")

audio_up = st.file_uploader(
    "🔊 Voice Audio",
    type=["mp3", "wav", "m4a", "aac"],
    key=f"audio_up_{st.session_state.audio_up_key}",
    label_visibility="collapsed",
)

if audio_up:
    with open("voice.mp3", "wb") as f:
        f.write(audio_up.read())
    size_mb = audio_up.size / (1024 * 1024)
    st.success(f"✅ Voice Audio: {audio_up.name} ({size_mb:.2f} MB)")
    try:
        dur = audio_dur("voice.mp3")
        st.caption(f"⏱️ Audio Duration: {dur:.1f} sec")
    except Exception:
        pass
    st.audio("voice.mp3")
else:
    if os.path.exists("voice.mp3"):
        st.info("📎 Voice Audio — အရင် upload ထားတာ ဆက်ရှိနေတယ်")
        st.audio("voice.mp3")
    else:
        st.warning("📌 MP3 ဖိုင် upload လုပ်ပါ — မရှိသေးရင် Generate နှိပ်လို့ မရပါ")
st.divider()

# ==================== Step 7 — Generate ====================
st.subheader("🚀 Step 7 — Generate Recap Video")

if st.button("✨ Generate Recap Video", type="primary", use_container_width=True):
    if not script.strip():
        st.error("Script paste လုပ်ပါ"); st.stop()
    if vid is None:
        st.error("Video Upload လုပ်ပါ"); st.stop()
    if not os.path.exists("voice.mp3"):
        st.error("MP3 ဖိုင် upload လုပ်ပါ"); st.stop()

    total_start = time.time()
    step_times = {}

    script_n = normalize_script(script)
    if not script_n.strip():
        st.error("Script မှာ စာမပါပါ"); st.stop()

    vid.seek(0)
    with open("input.mp4", "wb") as f:
        f.write(vid.read())

    fw_model = None
    if not USE_FAST_VAD:
        try: fw_model = get_fw_model()
        except Exception: pass

    # ==================== Speech Detect (video + audio decode အပြိုင်) ====================
    t0 = time.time()
    with st.spinner("🔍 Speech segments ရှာနေသည်..."):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                f_vid = ex.submit(load_audio_16k, "input.mp4")
                f_aud = ex.submit(load_audio_16k, "voice.mp3")
                try: vid_audio = f_vid.result()
                except Exception: vid_audio = None
                aud = f_aud.result()
            info = prepare_video_job("input.mp4", fw_model, vid_audio)
        except Exception as e:
            st.error(f"❌ Speech Detect: {e}"); st.stop()
    segments, vdur = info["segments"], info["vdur"]
    step_times["🔍 Speech Detect"] = time.time() - t0

    # ==================== Sync (အသံအမှန်အတိုင်း စာတန်းချိန်) ====================
    t0 = time.time()
    adur = len(aud) / 16000.0                      # decode လုပ်ထားတဲ့ အမှန် duration
    tempo = max(0.5, min(2.0, adur / vdur))
    eff = adur / tempo                              # tempo ပြီးနောက် အသံ duration

    subs = []
    if use_sub:
        try:
            a_segs = vad_segments(aud, AUDIO_VAD_SILENCE_MS, 60)
        except Exception:
            a_segs = [(0.0, adur)]
        a_segs = [(a / tempo, b / tempo) for a, b in a_segs]
        sents = [split_parts(s) for s in split_sentences(script_n)]
        subs = align_subs(sents, a_segs, eff)
        if not subs:
            st.error("Script မှာ စာမပါပါ"); st.stop()
    step_times["📝 Sub Align"] = time.time() - t0

    # ==================== Render (video + audio တစ်ကြိမ်တည်း) ====================
    t0 = time.time()
    with st.spinner("🎬 Finalizing..."):
        try:
            final_render("input.mp4", "voice.mp3", "final.mp4", tempo,
                         subs=subs, fp=FONT_FILE, fs=FS, pos_y=pos_y,
                         bh=BH, ba=BA, use_neon=use_neon,
                         thickness=neon_thickness, speed=neon_speed,
                         segments=segments, target_dur=eff)
        except Exception as e:
            st.warning(f"⚠️ Final render fail: {e} — simple merge ပြောင်း")
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
        st.markdown(f"<div class='step-timer'>{name} — <b>{t:.1f}s</b></div>",
                    unsafe_allow_html=True)

    if abs(adur / tempo - vdur) > 0.3:
        st.info(f"ℹ️ Tempo ကို {tempo:.2f}x ထိ ကန့်သတ်ထားလို့ အသံ/ဗီဒီယို အရှည် "
                f"({adur/tempo:.1f}s / {vdur:.1f}s) မတူပါ — အသံမဖြတ်အောင် ညှိထားပါတယ်")

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
