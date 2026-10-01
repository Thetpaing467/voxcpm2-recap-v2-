import streamlit as st
import os, re, ffmpeg, shutil, subprocess, asyncio
import edge_tts
from PIL import Image, ImageDraw, ImageFont
import cv2

# ===== Config =====
PASSWORD = "voxcpm2026"
FONT_FILE = "MyanmarPadaung.ttf"

FS, BH, BA = 30, 100, 100
ENC_PRESET = "ultrafast"
ENC_CRF = 23
AUDIO_BITRATE = "128k"

WHISPER_MODEL = "tiny"
WHISPER_LANG = "my"

EDGE_VOICES = {
    "female": "my-MM-NilarNeural",
    "male":   "my-MM-ThihaNeural",
}

st.set_page_config(page_title="Timeline Sync Dubbing", page_icon="🎬", layout="centered")

# ===== CSS =====
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
</style>
""", unsafe_allow_html=True)

# ===== Password =====
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


# ===== Helpers =====
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
    m = re.match(r'^(\d{1,2}):(\d{2})[,.](\d{1,3})$', ts)
    if m:
        mi, se, ms = m.groups()
        return int(mi)*60 + int(se) + int(ms.ljust(3,'0'))/1000
    return None


def render_png(text, out, fp, W, H, fs=30, pos_y=100, bh=100, ba=100):
    img = Image.new("RGBA", (W, H), (0,0,0,0)); d = ImageDraw.Draw(img)
    try: f = ImageFont.truetype(fp, fs)
    except: f = ImageFont.load_default()
    max_y = H - bh
    by = int((pos_y / 100) * max_y)
    if by < 0: by = 0
    if by > max_y: by = max_y
    d.rectangle([0, by, W, by+bh], fill=(0,0,0,ba))
    mc = max(15, int(W/(fs*0.9))); lines, cur = [], ""
    for w in text.split():
        if len(cur)+len(w)+1 <= mc: cur = cur+" "+w if cur else w
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    lh = int(fs*1.3); ty = by + (bh - len(lines)*lh)//2
    for ln in lines:
        bb = d.textbbox((0,0), ln, font=f); lw = bb[2]-bb[0]; lx = (W-lw)//2
        for dx in [-2,-1,0,1,2]:
            for dy in [-2,-1,0,1,2]: d.text((lx+dx, ty+dy), ln, font=f, fill=(0,0,0,255))
        d.text((lx, ty), ln, font=f, fill=(255,255,255,255)); ty += lh
    img.save(out, "PNG"); return out


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


def overlay(vp, sp, op, fp, fs=30, pos_y=100, bh=100, ba=100):
    W, H, _ = vid_info(vp); segs = parse_srt(sp)
    if not segs: raise Exception("SRT empty")
    os.makedirs("subtitle_pngs", exist_ok=True); pngs = []
    for i, s in enumerate(segs):
        p = f"subtitle_pngs/s_{i:04d}.png"
        render_png(s["text"], p, fp, W, H, fs, pos_y, bh, ba)
        pngs.append({"p": p, "a": s["start"], "b": s["end"]})
    cmd = ["ffmpeg","-y","-i",vp] + sum([["-i",x["p"]] for x in pngs], [])
    flt, cur = [], "[0:v]"
    for i, x in enumerate(pngs):
        lbl = f"[v{i}]"
        flt.append(f"{cur}[{i+1}:v]overlay=0:0:enable='between(t,{x['a']:.3f},{x['b']:.3f})'{lbl}")
        cur = lbl
    cmd += ["-filter_complex",";".join(flt),"-map",cur,"-map","0:a?",
            "-c:v","libx264","-crf",str(ENC_CRF),"-preset",ENC_PRESET,
            "-c:a","copy",op]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0: raise Exception(f"FFmpeg: {(r.stderr or '')[-500:]}")
    for x in pngs:
        try: os.remove(x["p"])
        except: pass
    return op


# ===== Whisper Timestamps =====
def whisper_timestamps(video_path):
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", "ts_audio.wav"
    ], capture_output=True, check=True)

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            "ts_audio.wav", language=WHISPER_LANG,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=0)
        )
        result = []
        for seg in segments:
            result.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
    except ImportError:
        import whisper
        model = whisper.load_model(WHISPER_MODEL)
        r = model.transcribe("ts_audio.wav", language=WHISPER_LANG)
        result = []
        for seg in r["segments"]:
            result.append({"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()})
    return result


# ===== Edge TTS =====
async def _edge_tts_async(text, out_file, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_file)


# ===== 🎯 Timeline Sync — atrim + asetpts =====
def timeline_sync_dubbing_atrim(video_path, segments_myanmar, output_path, voice="female"):
    """atrim + asetpts — Segment တစ်ခုချင်း — ဖြတ် → Timeline Sync"""
    voice_id = EDGE_VOICES.get(voice, EDGE_VOICES["female"])

    seg_audios = []
    for i, seg in enumerate(segments_myanmar):
        my_text = seg["text"].strip()
        if not my_text: continue

        seg_audio = f"seg_{i}.mp3"
        try:
            asyncio.run(_edge_tts_async(my_text, seg_audio, voice_id))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_edge_tts_async(my_text, seg_audio, voice_id))
            loop.close()

        orig_dur = seg["end"] - seg["start"]
        seg_dur = float(ffmpeg.probe(seg_audio)['format']['duration'])
        tempo = max(0.5, min(2.0, seg_dur / orig_dur)) if orig_dur > 0 else 1.0

        adjusted = f"seg_{i}_adj.mp3"
        cmd = [
            "ffmpeg", "-y", "-i", seg_audio,
            "-filter:a", f"atempo={tempo:.4f}",
            adjusted
        ]
        subprocess.run(cmd, capture_output=True)

        seg_audios.append({
            "path": adjusted,
            "start": seg["start"],
            "end": seg["end"],
            "orig_dur": orig_dur
        })

    cmd = ["ffmpeg", "-y", "-i", video_path]
    for s in seg_audios:
        cmd += ["-i", s["path"]]

    filters = []
    filters.append("[0:v]copy[v]")

    trim_labels = []
    for i, s in enumerate(seg_audios):
        label = f"a_{i}"
        delay_ms = int(s["start"] * 1000)
        filters.append(
            f"[{i+1}:a]asetpts=PTS-STARTPTS,"
            f"adelay={delay_ms}|{delay_ms}[{label}]"
        )
        trim_labels.append(f"[{label}]")

    if trim_labels:
        filters.append(
            f"{''.join(trim_labels)}amix=inputs={len(trim_labels)}:duration=longest:dropout_transition=0[aout]"
        )
        filter_complex = ";".join(filters)
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[aout]",
            "-c:v", "libx264", "-crf", str(ENC_CRF), "-preset", ENC_PRESET,
            "-c:a", "aac", "-b:a", AUDIO_BITRATE,
            "-shortest",
            output_path
        ]
    else:
        filter_complex = ";".join(filters)
        cmd += ["-filter_complex", filter_complex, "-map", "[v]", "-an", output_path]

    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-500:]}")

    return output_path


# ===== UI =====
st.markdown("<div class='main-title'>🎬 Timeline Sync Dubbing</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>Video → Timestamps → မြန်မာ Script → Sync</div>", unsafe_allow_html=True)
st.caption(f"⚡ atrim + asetpts  •  🎙️ Edge TTS  •  ❌ Lip Sync မဟုတ်")
st.divider()

# Step 1 — Video Upload
st.subheader("📹 Step 1 — Video Upload")
vid = st.file_uploader("Video", type=["mp4","mov","avi","mkv"], label_visibility="collapsed")
if vid: st.success(f"✅ Video — {vid.size/(1024*1024):.1f} MB")
st.divider()

# Step 2 — Whisper Timestamps
st.subheader("🎙️ Step 2 — Whisper Timestamps")
st.caption("မူရင်း Video — စကားပြောချိန် — Timestamps")

if "ts_segments" not in st.session_state:
    st.session_state.ts_segments = None
if "ts_scripts" not in st.session_state:
    st.session_state.ts_scripts = {}

if vid:
    if st.button("🎙️ Whisper — Timestamps ထုတ်", use_container_width=True):
        vid.seek(0)
        with open("ts_input.mp4", "wb") as f: f.write(vid.read())
        try:
            with st.spinner("🎙️ Whisper — Timestamps..."):
                segs = whisper_timestamps("ts_input.mp4")
            st.session_state.ts_segments = segs
            st.session_state.ts_scripts = {}
            st.success(f"✅ Segment {len(segs)} ခု တွေ့")
        except Exception as e:
            st.error(f"❌ Whisper — {e}")
        finally:
            vid.seek(0)
else:
    st.info("📹 Video Upload တင်ပါ")
st.divider()

# Step 3 — Segment Scripts
if st.session_state.ts_segments:
    st.subheader("📝 Step 3 — Segment တစ်ခုချင်း — မြန်မာ Script")
    st.caption(f"📊 Segment {len(st.session_state.ts_segments)} ခု")

    for i, seg in enumerate(st.session_state.ts_segments):
        dur = seg["end"] - seg["start"]
        st.markdown(f"**⏱️ Segment {i+1}** — `{seg['start']:.1f}s` → `{seg['end']:.1f}s` ({dur:.1f}s)")
        st.caption(f"🗣️ မူရင်း — {seg['text'][:80]}")
        my_text = st.text_area(
            f"မြန်မာ — Segment {i+1}",
            value=st.session_state.ts_scripts.get(i, ""),
            height=80,
            key=f"ts_script_{i}",
            placeholder="မြန်မာ ဘာသာပြန်/စကားပြန်..."
        )
        st.session_state.ts_scripts[i] = my_text
        st.markdown("---")
    st.divider()

# Step 4 — Edge TTS Voice
st.subheader("🎤 Step 4 — Edge TTS Voice")
edge_voice = st.radio(
    "အသံ ရွေးပါ",
    options=["female", "male"],
    format_func=lambda x: "👩 နီလာ (Nilar)" if x == "female" else "👨 သီဟ (Thiha)",
    horizontal=True,
    index=0
)
st.divider()

# Step 5 — Generate
st.subheader("🚀 Step 5 — Generate Timeline Sync Dubbing")

if st.button("✨ Generate Dubbing Video", type="primary", use_container_width=True):
    if vid is None:
        st.error("Video Upload တင်ပါ"); st.stop()
    if not st.session_state.ts_segments:
        st.error("Whisper Timestamps မရှိ"); st.stop()

    segs_my = []
    for i, seg in enumerate(st.session_state.ts_segments):
        my_text = st.session_state.ts_scripts.get(i, "").strip()
        if my_text:
            segs_my.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": my_text
            })

    if not segs_my:
        st.error("Segment တစ်ခုခု — မြန်မာ Script ရေးပါ"); st.stop()

    vid.seek(0)
    with open("input.mp4", "wb") as f: f.write(vid.read())

    with st.spinner("🎬 Timeline Sync — atrim + asetpts..."):
        try:
            timeline_sync_dubbing_atrim("input.mp4", segs_my, "final.mp4", voice=edge_voice)
        except Exception as e:
            st.error(f"❌ Timeline Sync — {e}")
            st.stop()

    st.success(f"✅ Done — Segment {len(segs_my)} ခု — Timeline Sync")
    st.video("final.mp4")

    with open("final.mp4", "rb") as f:
        st.download_button("📥 Download Dubbing Video", f, file_name="dubbing.mp4")