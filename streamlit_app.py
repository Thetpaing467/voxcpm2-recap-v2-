import streamlit as st
import os, re, ffmpeg, shutil, subprocess, asyncio
import concurrent.futures
import edge_tts
from PIL import Image, ImageDraw, ImageFont
import cv2

# ===== Config =====
PASSWORD = "voxcpm2026"
FONT_FILE = "MyanmarPadaung.ttf"

# ⚡ Fast Settings
FS, BH, BA = 30, 100, 100
ENC_PRESET = "ultrafast"
ENC_CRF = 23
AUDIO_BITRATE = "128k"
TTS_CHUNK = 600
TTS_WORKERS = 3
PNG_WORKERS = 4

# 🎙️ Whisper Settings
WHISPER_MODEL = "tiny"
WHISPER_LANG = "my"
SPEECH_MERGE_GAP = 0.5

EDGE_VOICES = {
    "female": "my-MM-NilarNeural",
    "male":   "my-MM-ThihaNeural",
}

st.set_page_config(page_title="Myanmar TTS Recap", page_icon="🎬", layout="centered")

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
.stButton>button:hover{transform:translateY(-1px)!important;
 box-shadow:0 6px 20px rgba(102,126,234,.5)!important}
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
    st.markdown("<div class='main-sub'>Password ထည့်ပြီး ဝင်ပါ</div>", unsafe_allow_html=True)
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
    """Subtitle PNG Overlay — Parallel PNG Render — 30-50% မြန်"""
    W, H, _ = vid_info(vp); segs = parse_srt(sp)
    if not segs: raise Exception("SRT empty")
    os.makedirs("subtitle_pngs", exist_ok=True)

    # ⚡ PNG — Parallel Render
    def render_one(args):
        i, s = args
        p = f"subtitle_pngs/s_{i:04d}.png"
        render_png(s["text"], p, fp, W, H, fs, pos_y, bh, ba)
        return {"p": p, "a": s["start"], "b": s["end"]}

    pngs = [None] * len(segs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=PNG_WORKERS) as ex:
        for idx, item in ex.map(render_one, enumerate(segs)):
            pngs[idx] = item

    # FFmpeg — Overlay
    cmd = ["ffmpeg","-y","-i",vp] + sum([["-i",x["p"]] for x in pngs], [])
    flt, cur = [], "[0:v]"
    for i, x in enumerate(pngs):
        lbl = f"[v{i}]"
        flt.append(f"{cur}[{i+1}:v]overlay=0:0:enable='between(t,{x['a']:.3f},{x['b']:.3f})'{lbl}")
        cur = lbl
    cmd += ["-filter_complex",";".join(flt),"-map",cur,"-map","0:a?",
            "-c:v","libx264","-crf",str(ENC_CRF),"-preset",ENC_PRESET,
            "-tune","fastdecode",
            "-c:a","copy",op]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0: raise Exception(f"FFmpeg: {(r.stderr or '')[-500:]}")
    for x in pngs:
        try: os.remove(x["p"])
        except: pass
    return op


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


# ===== Edge TTS — Parallel =====
async def _edge_tts_async(text, out_file, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_file)


def edge_tts_run(chunks, out_path, voice="female", cb=None, workers=TTS_WORKERS):
    voice_id = EDGE_VOICES.get(voice, EDGE_VOICES["female"])

    def tts_one(args):
        i, c = args
        dst = f"edge_chunk_{i}.mp3"
        try:
            asyncio.run(_edge_tts_async(c, dst, voice_id))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_edge_tts_async(c, dst, voice_id))
            loop.close()
        return (i, dst)

    results = [None] * len(chunks)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for idx, dst in ex.map(tts_one, enumerate(chunks)):
            results[idx] = dst
            done += 1
            if cb: cb(done - 1, len(chunks), chunks[idx])

    with open("edge_concat.txt", "w", encoding="utf-8") as f:
        for a in results: f.write(f"file '{a}'\n")

    ffmpeg.input("edge_concat.txt", format="concat", safe=0).output(
        out_path, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
    ).run(overwrite_output=True)

    return out_path


def tts_all(text, out, voice="female", cb=None):
    chunks = split_scr(text, TTS_CHUNK)
    st.info(f"🎙️ Edge TTS — {EDGE_VOICES[voice]} — Parallel x{TTS_WORKERS}...")
    edge_tts_run(chunks, out, voice=voice, cb=cb)
    st.success(f"✅ Edge TTS — အောင်မြင်")
    return out


# ===== Whisper Auto Cut =====
def whisper_cut_speech(input_video, output_video="input_cut.mp4"):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_video,
        "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", "audio.wav"
    ], capture_output=True, check=True)

    speech_segments = []
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            "audio.wav", language=WHISPER_LANG,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )
        for seg in segments:
            speech_segments.append((seg.start, seg.end))
    except Exception:
        import whisper
        model = whisper.load_model(WHISPER_MODEL)
        result = model.transcribe("audio.wav", language=WHISPER_LANG)
        for seg in result["segments"]:
            speech_segments.append((seg["start"], seg["end"]))

    if not speech_segments:
        raise Exception("စကားပြောခန်း မတွေ့ဘူး")

    merged = []
    for s, e in speech_segments:
        if merged and s - merged[-1][1] < SPEECH_MERGE_GAP:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    select_exprs = [f"between(t,{s:.2f},{e:.2f})" for s, e in merged]
    select_str = "+".join(select_exprs)

    cmd = [
        "ffmpeg", "-y", "-i", input_video,
        "-vf", f"select='{select_str}',setpts=N/FRAME_RATE/TB",
        "-af", f"aselect='{select_str}',asetpts=N/SR/TB",
        "-c:v", "libx264", "-crf", "23", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "128k",
        output_video
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")

    total = sum(e - s for s, e in merged)
    return output_video, len(merged), total


# ===== UI =====
st.markdown("<div class='main-title'>🎬 Myanmar TTS Recap</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>Video → မြန်မာ Script → Edge TTS → Recap</div>", unsafe_allow_html=True)
st.caption(f"⚡ Fast Mode — {ENC_PRESET} @ CRF {ENC_CRF}  •  🎙️ Edge TTS x{TTS_WORKERS}  •  🖼️ PNG x{PNG_WORKERS}")
st.divider()

# Step 1
st.subheader("📝 Step 1 — Gemini Web မှ Script")
st.link_button("🌐 Open Gemini Web", "https://gemini.google.com", use_container_width=True)
with st.expander("📋 Prompt — Copy"):
    st.code("Watch this video carefully and write a clear, continuous movie recap script in Myanmar language for audio narration that matches the length of the video. Return plain speech text only without markdown titles.", language="text")
st.divider()

# Step 2
st.subheader("📝 Step 2 — Script Paste")
if "script" not in st.session_state: st.session_state.script = ""
script = st.text_area("Script", value=st.session_state.script, height=220, label_visibility="collapsed", placeholder="မြန်မာ Script paste...")
st.session_state.script = script
c1, c2 = st.columns([3, 1])
with c1: st.caption(f"📝 စာလုံး — {len(script):,}")
with c2:
    if st.button("🗑️ Clear", use_container_width=True):
        st.session_state.script = ""; st.rerun()
st.divider()

# Step 3
st.subheader("📁 Step 3 — Video")
vid = st.file_uploader("📹 Video Upload", type=["mp4","mov","avi","mkv"], label_visibility="collapsed")
if vid: st.success(f"✅ Video — {vid.size/(1024*1024):.1f} MB")
st.divider()

# Step 4
st.subheader("📝 Step 4 — Subtitle")
use_sub = st.toggle("စာတန်းထိုး (Burn-in)", value=True)

pos_y = 100

if use_sub:
    pos_y = st.slider(
        "📍 Subtitle Position (0=အပေါ်, 50=အလယ်, 100=အောက်)",
        min_value=0, max_value=100, value=100, step=5
    )
    st.caption(f"📍 {pos_y}%  •  🔤 Font {FS}  •  ⬛ Box {BH}px  •  🎨 Opacity {BA}")

if vid and use_sub:
    st.markdown("**🖼️ Preview**")
    with st.spinner("Preview..."):
        vid.seek(0)
        with open("preview.mp4", "wb") as f: f.write(vid.read())
        W, H, _ = vid_info("preview.mp4")
        render_png("စာတန်းထိုး Preview", "prev.png", FONT_FILE, W, H, FS, pos_y, BH, BA)
        cap = cv2.VideoCapture("preview.mp4"); ok, fr = cap.read(); cap.release()
        if ok:
            bg = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).convert("RGBA")
            fg = Image.open("prev.png").convert("RGBA")
            comp = Image.alpha_composite(bg, fg); pw = 720
            comp.resize((pw, int(H*(pw/W))), Image.LANCZOS).convert("RGB").save("prev_out.png")
            st.image("prev_out.png", use_container_width=True)
st.divider()

# Step 4.5 — Dubbing Mode
st.subheader("🎙️ Step 4.5 — Dubbing Mode")
st.caption("🎬 စကားပြောခန်းပဲ ထားပြီး — ကျန်တာ ဖြတ်မယ် (Whisper Auto)")
dubbing_mode = st.toggle("✂️ Dubbing Mode — Whisper Auto Cut", value=False)
if dubbing_mode:
    st.info(f"⚡ Whisper `{WHISPER_MODEL}` — မြန်မာ ({WHISPER_LANG}) — RAM နည်း")
st.divider()

# Step 5
st.subheader("🚀 Step 5 — Generate Recap")

edge_voice = st.radio(
    "🎤 Edge TTS အသံ ရွေးပါ",
    options=["female", "male"],
    format_func=lambda x: "👩 နီလာ (Nilar)" if x == "female" else "👨 သီဟ (Thiha)",
    horizontal=True,
    index=0
)

if st.button("✨ Generate Recap Video", type="primary", use_container_width=True):
    if not script.strip(): st.error("Script paste လုပ်ပါ"); st.stop()
    if vid is None: st.error("Video Upload တင်ပါ"); st.stop()

    vid.seek(0)
    with open("input.mp4", "wb") as f: f.write(vid.read())
    _, _, vdur = vid_info("input.mp4")

    # Whisper Dubbing Mode
    if dubbing_mode:
        with st.spinner(f"🎙️ Whisper `{WHISPER_MODEL}` — စကားပြောခန်း ရှာနေသည်..."):
            try:
                cut_path, seg_count, cut_dur = whisper_cut_speech("input.mp4", "input_cut.mp4")
                shutil.move("input_cut.mp4", "input.mp4")
                _, _, vdur = vid_info("input.mp4")
                st.success(f"✅ ဖြတ်ပြီး — {seg_count} ခန်း • {cut_dur:.0f}s")
            except Exception as e:
                st.error(f"❌ Whisper — {e}")
                st.stop()

    pb = st.progress(0); txt = st.empty()
    def cb(i, tot, c):
        pb.progress((i+1)/tot); txt.caption(f"[{i+1}/{tot}] {len(c)} စာလုံး")

    # TTS — Edge TTS Parallel
    try:
        tts_all(script, "voice.mp3", voice=edge_voice, cb=cb)
    except Exception as e:
        st.error(f"TTS — {e}")
        st.stop()

    adur = float(ffmpeg.probe("voice.mp3")['format']['duration'])
    tempo = max(0.5, min(2.0, adur/vdur))

    sp = scr_to_srt(script, vdur, "sub.srt") if use_sub else None

    with st.spinner("🎬 Rendering — Fast Mode..."):
        vi = ffmpeg.input("input.mp4")
        va = ffmpeg.input("voice.mp3").audio.filter('atempo', tempo)
        ffmpeg.output(
            vi.video, va, "temp.mp4",
            vcodec='libx264',
            crf=ENC_CRF,
            preset='ultrafast',
            tune='fastdecode',
            movflags='+faststart',
            acodec='aac',
            audio_bitrate=AUDIO_BITRATE,
            shortest=None,
            threads=0
        ).run(overwrite_output=True)

        if use_sub and sp:
            overlay("temp.mp4", sp, "final.mp4", FONT_FILE, FS, pos_y, BH, BA)
        else:
            shutil.copy("temp.mp4", "final.mp4")

    st.success(f"✅ Done — {adur:.0f}s @ {tempo:.2f}x")
    st.video("final.mp4")

    with open("final.mp4", "rb") as f:
        st.download_button("📥 Download Recap Video", f, file_name="recap.mp4")