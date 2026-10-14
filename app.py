# =====================================================================
#  Myanmar TTS Recap  —  Google Colab + Gradio
# =====================================================================
import subprocess, sys, os, importlib

def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

# ---------- Setup ----------
def _need(mod):
    try:
        importlib.import_module(mod); return False
    except Exception:
        return True

pk = []
if _need("gradio"): pk.append("gradio")
if _need("edge_tts"): pk.append("edge-tts")
if _need("faster_whisper"): pk.append("faster-whisper")
if _need("gradio_client"): pk.append("gradio_client")
if pk:
    print("📦 Installing:", pk)
    sh("pip install -q -U " + " ".join(pk))
sh("pip install -q -U 'edge-tts>=7.0.0'")
sh("apt-get -qq update && apt-get -qq install -y ffmpeg libraqm0 libfribidi0 "
   "libharfbuzz0b fonts-noto-core > /dev/null 2>&1")

import re, json, glob, hashlib, shutil, asyncio, time, tempfile, unicodedata
import concurrent.futures, threading, queue
import numpy as np
import cv2
import edge_tts
import gradio as gr
from PIL import Image, ImageDraw, ImageFont, features
from gradio_client import Client, handle_file

os.environ["HF_HOME"] = "/tmp/hf_cache"

# ============================ CONFIG ============================
SPACES = [
    {"space": "openbmb/VoxCPM-Demo", "type": "demo"},
    {"space": "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts", "type": "burmese"},
]
PASSWORD = "voxcpm2026"        # user: admin
USE_AUTH = True

FONT_CANDIDATES = [
    "/content/MyanmarPadaung.ttf", "MyanmarPadaung.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf",
]
BA = 200
FINAL_PRESET, FINAL_CRF = "ultrafast", 20
AUDIO_BITRATE = "128k"
TTS_CHUNK, EDGE_CHUNK, TTS_WORKERS = 300, 200, 3
WHISPER_MODEL, WHISPER_LANG = "tiny", "my"
PADDING_Y, CORNER_RADIUS = 15, 20
EDGE_VOICE = "my-MM-ThihaNeural"

BASE_DIR = os.getcwd()
OUT_DIR = os.path.join(BASE_DIR, "outputs")
CACHE_DIR = os.path.join(BASE_DIR, "tts_cache")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

RAQM = features.check("raqm")


# ============================ UTILS ============================
def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_streams", "-show_format", p],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def audio_dur(p):
    return float(probe(p)["format"]["duration"])


def video_meta(p):
    cap = cv2.VideoCapture(p)
    ok, f0 = cap.read()
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if not ok:
        raise Exception("Video ဖတ်မရပါ")
    if not fps or fps != fps or fps < 1:
        fps = 30.0
    H, W = f0.shape[:2]
    dur = float(probe(p)["format"]["duration"])
    return W, H, fps, dur


def find_font(uploaded=None):
    for p in [uploaded] + FONT_CANDIDATES + glob.glob("/usr/share/fonts/**/NotoSansMyanmar*.ttf", recursive=True):
        if p and os.path.exists(p):
            return p
    return None


def load_font(fp, fs):
    try:
        eng = ImageFont.Layout.RAQM if RAQM else ImageFont.Layout.BASIC
        return ImageFont.truetype(fp, fs, layout_engine=eng)
    except Exception:
        try: return ImageFont.truetype(fp, fs)
        except Exception: return ImageFont.load_default()


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
    return re.search(r"[\u1000-\u109F\w]", t) is not None


def split_scr(t, mc=TTS_CHUNK):
    sents = []
    for p in t.replace("။", "။|").split("|"):
        p = p.strip()
        if not p: continue
        sents.append(p if p.endswith("။") else p + "။")
    out, cur = [], ""
    for s in sents:
        if len(cur) + len(s) <= mc: cur += s
        else:
            if cur: out.append(cur)
            if len(s) > mc:
                for i in range(0, len(s), mc): out.append(s[i:i + mc])
                cur = ""
            else: cur = s
    if cur: out.append(cur)
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


def build_sub_segments(pieces, total, mc=30):
    raw = sum(d for _, d in pieces)
    if raw <= 0: return []
    k = total / raw
    cur, segs = 0.0, []
    for text, d in pieces:
        parts = split_for_sub(text, mc)
        d *= k
        if not parts:
            cur += d; continue
        tot = sum(len(p) for p in parts)
        for p in parts:
            dd = len(p) / tot * d
            segs.append({"start": cur, "end": cur + dd, "text": p})
            cur += dd
    return segs


# ============================ SUBTITLE RENDER ============================
def wrap_text(text, font, max_w, d):
    def w(s): return d.textlength(s, font=font)
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word) if cur else word
        if w(t) <= max_w:
            cur = t; continue
        if cur: lines.append(cur); cur = ""
        if w(word) <= max_w:
            cur = word; continue
        piece = ""
        for ch in word:
            can_break = (piece and not unicodedata.category(ch).startswith("M")
                         and piece[-1] != "\u1039")
            if can_break and w(piece + ch) > max_w:
                lines.append(piece); piece = ch
            else:
                piece += ch
        cur = piece
    if cur: lines.append(cur)
    return lines


def make_sub_overlay(text, fp, W, H, fs, pos_y, ba):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = load_font(fp, fs)
    lines = wrap_text(text, f, W * 0.92, d)
    if not lines: return None

    lh = int(fs * 1.5)
    box_h = len(lines) * lh + PADDING_Y * 2
    max_y = max(0, H - box_h)
    box_y = max(0, min(int(pos_y / 100 * max_y), max_y))
    d.rounded_rectangle([0, box_y, W, box_y + box_h], radius=CORNER_RADIUS, fill=(0, 0, 0, ba))

    ty = box_y + PADDING_Y
    for ln in lines:
        lw = d.textlength(ln, font=f)
        lx = int((W - lw) // 2)
        d.text((lx, ty), ln, font=f, fill=(255, 255, 255, 255),
               stroke_width=2, stroke_fill=(0, 0, 0, 255))
        ty += lh

    rgba = np.array(img)
    rows = np.where(rgba[:, :, 3].any(axis=1))[0]
    if len(rows) == 0: return None
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    crop = rgba[y0:y1]
    alpha = crop[:, :, 3:4].astype(np.float32) / 255.0
    bgr = crop[:, :, [2, 1, 0]].astype(np.float32)
    return {"y0": y0, "y1": y1, "inv": 1.0 - alpha, "pre": bgr * alpha}


# ============================ FFMPEG ============================
def keep_count(video_in, segments):
    W, H, fps, dur = video_meta(video_in)
    total = int(round(dur * fps))
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
    r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")
    return output_video


def mux_audio(video_in, audio_in, output_video, tempo):
    cmd = ["ffmpeg", "-y", "-i", video_in, "-i", audio_in,
           "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
           "-shortest", output_video]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if r.returncode != 0:
        raise Exception(f"Mux: {(r.stderr or '')[-300:]}")
    return output_video


def final_render(video_in, output_video, segments, sub_segs, fp, fs, pos_y, ba,
                 use_neon=True, mirror=True, crop_ratio=0.95,
                 thickness=15, speed=0.4, tail=0.5):
    W0, H0, fps, _ = video_meta(video_in)
    if crop_ratio != 1.0:
        cw, ch = int(W0 * crop_ratio), int(H0 * crop_ratio)
        cw -= cw % 2; ch -= ch % 2
        cx, cy = (W0 - cw) // 2, (H0 - ch) // 2
    else:
        cw, ch = W0 - W0 % 2, H0 - H0 % 2
        cx = cy = 0
    W, H = cw, ch

    subs = []
    if sub_segs and fp:
        for sg in sub_segs:
            ov = make_sub_overlay(sg["text"], fp, W, H, fs, pos_y, ba)
            if ov:
                ov["a"], ov["b"] = sg["start"], sg["end"]
                subs.append(ov)

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
        Si = S.astype(np.int64) % P
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

    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-an",
           "-c:v", "libx264", "-crf", str(FINAL_CRF), "-preset", FINAL_PRESET,
           "-pix_fmt", "yuv420p", "-threads", "0", output_video]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    q_in, q_out = queue.Queue(maxsize=8), queue.Queue(maxsize=8)
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

    def writer():
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

    j = 0; k = 0
    try:
        while True:
            fr = qget(q_in)
            if fr is None: break
            t = j / fps
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
        except Exception: pass
        proc.wait()
    if err: raise err[0]
    if proc.returncode != 0:
        raise Exception("Final render: ffmpeg fail")
    return output_video


# ============================ SPEECH DETECT ============================
_fw = None
def get_fw_model():
    global _fw
    if _fw is None:
        from faster_whisper import WhisperModel
        _fw = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                           cpu_threads=os.cpu_count() or 4)
    return _fw


def detect_speech(video_path, workdir):
    wav = os.path.join(workdir, "whisper_audio.wav")
    try:
        subprocess.run(["ffmpeg", "-y", "-i", video_path, "-ar", "16000", "-ac", "1",
                        "-c:a", "pcm_s16le", wav], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        return []

    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        audio = decode_audio(wav, sampling_rate=16000)
        ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=700, speech_pad_ms=200))
        segs = [(t["start"] / 16000.0, t["end"] / 16000.0) for t in ts]
        if segs: return segs
    except Exception:
        pass

    try:
        model = get_fw_model()
        segments, _ = model.transcribe(wav, language=WHISPER_LANG, vad_filter=False,
                                       beam_size=1, condition_on_previous_text=False, temperature=0)
        return [(s.start, s.end) for s in segments]
    except Exception:
        return []


def prepare_video_job(video_in, workdir):
    segments = detect_speech(video_in, workdir)
    W, H, fps, dur = video_meta(video_in)
    note = ""
    if not segments:
        segments = [(0.0, dur)]
        note = "⚠️ Video ထဲ Speech မတွေ့ — Video တစ်ခုလုံး သုံးမယ်"
    kept, vfps = keep_count(video_in, segments)
    if kept == 0:
        segments = [(0.0, dur)]
        kept, vfps = keep_count(video_in, segments)
    return {"segments": segments, "vdur": kept / vfps, "note": note}


# ============================ TTS ============================
def tts_demo(chunks, ref, space, workdir, cb=None):
    cl = Client(space); files = []
    rf = handle_file(ref) if ref else None
    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks))
        res = cl.predict(
            text_input=c,
            control_instruction="A warm young woman, calm and expressive",
            reference_wav_path_input=rf,
            use_prompt_text=False, prompt_text_input="",
            cfg_value_input=2.0, do_normalize=True, denoise=False,
            api_name="/generate")
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = os.path.join(workdir, f"chunk_{i}.wav")
        shutil.copy(p, dst); files.append(dst)
    return files


def tts_burmese(chunks, ref, space, workdir, cb=None):
    cl = Client(space); files = []
    if not ref: raise Exception("Reference Audio လိုအပ်")
    rf = handle_file(ref)
    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks))
        res = cl.predict(
            target_text=c, ref_audio=rf, ref_text="မြန်မာ အသံနမူနာ",
            cfg_value=2.0, inference_timesteps=10, api_name="/tts")
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = os.path.join(workdir, f"chunk_b_{i}.wav")
        shutil.copy(p, dst); files.append(dst)
    return files


async def _edge_save(text, out_file, voice):
    await edge_tts.Communicate(text, voice).save(out_file)


def concat_audio(files, out_path, workdir):
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for a in files:
            f.write("file '" + os.path.abspath(a).replace("'", "'\\''") + "'\n")
    r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c:a", "libmp3lame", "-b:a", AUDIO_BITRATE, "-ar", "48000", out_path],
                       capture_output=True, text=True, errors="ignore")
    if r.returncode != 0:
        raise Exception(f"Concat: {(r.stderr or '')[-300:]}")


def edge_tts_run(chunks, out_path, workdir, cb=None, log=None):
    small = [x for c in chunks for x in split_scr(c, EDGE_CHUNK) if has_speech(x)]
    if not small: raise Exception("TTS လုပ်စရာ စာမတွေ့ပါ")
    chunks = small

    def tts_one(args):
        i, c = args
        key = hashlib.md5((EDGE_VOICE + c).encode("utf-8")).hexdigest()
        dst = os.path.join(CACHE_DIR, f"{key}.mp3")
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            return (i, dst)
        for attempt in range(4):
            try:
                if os.path.exists(dst): os.remove(dst)
                asyncio.run(_edge_save(c, dst, EDGE_VOICE))
                if os.path.exists(dst) and os.path.getsize(dst) > 0:
                    time.sleep(0.3)
                    return (i, dst)
            except Exception:
                pass
            time.sleep(3 * (attempt + 1))
        return (i, None)

    results = [None] * len(chunks); done = 0; skipped = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=TTS_WORKERS) as ex:
        for idx, dst in ex.map(tts_one, enumerate(chunks)):
            results[idx] = dst; done += 1
            if dst is None: skipped.append(idx)
            if cb: cb(done - 1, len(chunks))

    good = [a for a in results if a]
    if not good:
        raise Exception("Edge TTS အသံမရပါ — ခဏနေ ပြန်စမ်းပါ (Colab IP ကို Microsoft က ပိတ်ထားနိုင်တယ်၊ Runtime → Disconnect and delete → ပြန်ချိတ်ကြည့်ပါ)")
    if skipped and log is not None:
        log.append("⚠️ ကျော်လိုက်တဲ့ အပိုင်း: " + " | ".join(chunks[i][:30] for i in skipped))

    pieces = [(chunks[i], audio_dur(a)) for i, a in enumerate(results) if a]
    concat_audio(good, out_path, workdir)
    return out_path, pieces


def tts_all(text, out, workdir, ref, use_vox, cb, log):
    chunks = split_scr(text, TTS_CHUNK)
    if not use_vox:
        o, p = edge_tts_run(chunks, out, workdir, cb, log)
        log.append("✅ Edge TTS — သီဟ (Thiha)")
        return o, p

    files = None
    for s in SPACES:
        try:
            log.append(f"🎙️ VoxCPM2 — {s['space']} စမ်းနေသည်...")
            fn = tts_demo if s["type"] == "demo" else tts_burmese
            files = fn(chunks, ref, s["space"], workdir, cb)
            log.append("✅ VoxCPM2 — အောင်မြင်")
            break
        except Exception as e:
            log.append(f"⚠️ VoxCPM2 Fail: {str(e)[:100]}")
            files = None

    if files is None:
        log.append("⚠️ VoxCPM2 Busy/Fail — Edge TTS သီဟ Auto")
        o, p = edge_tts_run(chunks, out, workdir, cb, log)
        return o, p

    pieces = [(chunks[i], audio_dur(f)) for i, f in enumerate(files)]
    concat_audio(files, out, workdir)
    return out, pieces


# ============================ MAIN PIPELINE ============================
def generate(script, video, use_vox, ref_audio, use_sub, pos_y, font_size,
             font_up, use_neon, mirror, progress=gr.Progress()):
    log = []
    if not script or not script.strip():
        raise gr.Error("Script paste လုပ်ပါ")
    if not video:
        raise gr.Error("Video upload လုပ်ပါ")

    workdir = tempfile.mkdtemp(prefix="recap_", dir=BASE_DIR)
    t_all = time.time()
    script_n = normalize_script(script)
    if not script_n.strip():
        raise gr.Error("Script မှာ စာမပါပါ")

    fp = find_font(font_up if isinstance(font_up, str) else getattr(font_up, "name", None))
    if use_sub and not fp:
        log.append("⚠️ Myanmar font မတွေ့ — subtitle ပျက်နိုင်တယ်။ Font ဖိုင် upload လုပ်ပါ")

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job = pool.submit(prepare_video_job, video, workdir)

    t0 = time.time()
    def cb(i, tot):
        progress(0.05 + 0.6 * (i + 1) / max(tot, 1), desc=f"🎙️ TTS [{i+1}/{tot}]")
    try:
        voice = os.path.join(workdir, "voice.mp3")
        _, pieces = tts_all(script_n, voice, workdir, ref_audio, use_vox, cb, log)
    except Exception as e:
        raise gr.Error(f"TTS: {e}")
    t_tts = time.time() - t0

    progress(0.7, desc="✂️ Speech segments ရှာနေ...")
    try:
        info = job.result()
    except Exception as e:
        raise gr.Error(f"Cut: {e}")
    pool.shutdown(wait=False)
    if info["note"]: log.append(info["note"])
    segments, vdur = info["segments"], info["vdur"]

    adur = audio_dur(voice)
    tempo = max(0.5, min(2.0, adur / vdur))
    eff = adur / tempo

    t0 = time.time()
    progress(0.75, desc="🎬 Rendering...")
    video_only = os.path.join(workdir, "video_only.mp4")
    final_tmp = os.path.join(workdir, "final.mp4")
    try:
        sub_segs = build_sub_segments(pieces, eff) if use_sub else []
        final_render(video, video_only, segments, sub_segs, fp, int(font_size),
                     pos_y, BA, use_neon=use_neon, mirror=mirror)
        mux_audio(video_only, voice, final_tmp, tempo)
    except Exception as e:
        log.append(f"⚠️ Render Fail: {e} — simple merge သုံးမယ်")
        try:
            simple_merge(video, voice, final_tmp, tempo, segments)
        except Exception as e2:
            raise gr.Error(f"Merge: {e2}")
    t_ren = time.time() - t0

    final = os.path.join(OUT_DIR, f"recap_{int(time.time())}.mp4")
    shutil.move(final_tmp, final)
    shutil.rmtree(workdir, ignore_errors=True)

    log.append(f"⏱️ TTS {t_tts:.1f}s | Render {t_ren:.1f}s | Total {time.time()-t_all:.1f}s")
    progress(1.0, desc="✅ Done")
    return final, final, "\n".join(log)


def clear_all():
    return "", None, None, None, None, ""


# ============================ GRADIO UI ============================
CSS = ".gradio-container{max-width:760px!important;margin:auto}"

with gr.Blocks(title="Myanmar TTS Recap", css=CSS, theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🎬 Myanmar TTS Recap\nVideo → Script → VoxCPM2 / Edge TTS သီဟ → Recap")

    script = gr.Textbox(label="📋 Script (Paste လုပ်ပါ)", lines=8,
                        placeholder="မြန်မာ Script paste...")
    video = gr.Video(label="📁 Video", sources=["upload"])

    with gr.Accordion("📝 Subtitle / Effect", open=True):
        use_sub = gr.Checkbox(value=True, label="Subtitle Burn-in")
        pos_y = gr.Slider(0, 100, value=100, step=1, label="📍 Position (0=အပေါ် 100=အောက်)")
        font_size = gr.Slider(16, 120, value=30, step=1, label="Font size")
        font_up = gr.File(label="Myanmar Font (.ttf) — Optional", file_types=[".ttf", ".otf"],
                          type="filepath")
        use_neon = gr.Checkbox(value=True, label="Neon border")
        mirror = gr.Checkbox(value=True, label="Mirror (ဘယ်ညာ လှန်)")

    with gr.Accordion("🎙️ Voice", open=True):
        use_vox = gr.Checkbox(value=True, label="VoxCPM2 သုံးမယ် (Fail ရင် Edge TTS သီဟ Auto)")
        ref_audio = gr.Audio(label="🎤 Ref Audio (VoxCPM2) — Optional",
                             type="filepath", sources=["upload"])

    btn = gr.Button("✨ Generate Recap Video", variant="primary")
    clr = gr.Button("🗑️ Clear")

    out_video = gr.Video(label="Result")
    out_file = gr.File(label="📥 Download")
    out_log = gr.Textbox(label="Log", lines=5, interactive=False)

    btn.click(generate,
              [script, video, use_vox, ref_audio, use_sub, pos_y, font_size,
               font_up, use_neon, mirror],
              [out_video, out_file, out_log])
    clr.click(clear_all, None, [script, video, out_video, out_file, font_up, out_log])

demo.queue(default_concurrency_limit=1).launch(
    share=True, debug=True, allowed_paths=[OUT_DIR],
    auth=("admin", PASSWORD) if USE_AUTH else None)