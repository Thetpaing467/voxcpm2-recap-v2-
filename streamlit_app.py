import streamlit as st
import os, re, ffmpeg, shutil, subprocess, json
from PIL import Image, ImageDraw, ImageFont
import cv2
from gradio_client import Client, handle_file

# ===== Config =====
SPACES = [
    {"space": "openbmb/VoxCPM-Demo", "type": "demo"},
    {"space": "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts", "type": "burmese"},
]
PASSWORD = "voxcpm2026"
FONT_FILE = "MyanmarPadaung.ttf"

# ⚡ Fast Settings
FS, BH, BA = 30, 100, 100
ENC_PRESET = "ultrafast"
ENC_CRF = 23
AUDIO_BITRATE = "128k"
TTS_CHUNK = 600

# 🎙️ HF Token — Speaker Diarization အတွက်
HF_TOKEN = os.getenv("HF_TOKEN", "")

st.set_page_config(page_title="VoxCPM2 Dubbing", page_icon="🎬", layout="centered")

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


# ===== 🎙️ Speaker Diarization (pyannote) =====
def diarize_speakers(video_path):
    """Video → Audio → pyannote → Speaker Segments"""
    # 1. Audio ခွဲ
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le", "diarize_audio.wav"
    ], capture_output=True, check=True)

    # 2. pyannote Pipeline
    from pyannote.audio import Pipeline
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=HF_TOKEN
    )

    diarization = pipeline("diarize_audio.wav")

    # 3. Segments စုစည်း
    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append({
            "start": turn.start,
            "end": turn.end,
            "speaker": speaker
        })

    if not segments:
        raise Exception("Speaker မတွေ့ဘူး")

    # 4. Speaker တစ်ယောက်ချင်း — Sample Audio ဖြတ်
    os.makedirs("speaker_samples", exist_ok=True)
    speakers = sorted(set(s["speaker"] for s in segments))
    speaker_samples = {}

    for spk in speakers:
        # အရှည်ဆုံး Segment ကို Sample အဖြစ် ရွေး
        spk_segs = [s for s in segments if s["speaker"] == spk]
        longest = max(spk_segs, key=lambda x: x["end"] - x["start"])

        # Sample 10 စက္ကန့် အထိ
        sample_start = longest["start"]
        sample_end = min(longest["end"], sample_start + 10)

        sample_path = f"speaker_samples/{spk}.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-ss", str(sample_start),
            "-to", str(sample_end),
            "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le",
            sample_path
        ], capture_output=True, check=True)

        speaker_samples[spk] = sample_path

    return segments, speakers, speaker_samples
# ===== TTS Helpers =====
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


def tts_with_ref(chunks, ref_audio, space="openbmb/VoxCPM-Demo", cb=None):
    """VoxCPM2 — Ref Audio + Script → မြန်မာ အသံ"""
    cl = Client(space)
    files = []
    rf = handle_file(ref_audio) if ref_audio else None

    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks), c)
        res = cl.predict(
            text_input=c,
            control_instruction="A warm young woman, calm and expressive",
            reference_wav_path_input=rf,
            use_prompt_text=False,
            prompt_text_input="",
            cfg_value_input=2.0,
            do_normalize=True,
            denoise=False,
            api_name="/generate"
        )
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = f"dub_chunk_{i}.wav"
        shutil.copy(p, dst)
        files.append(dst)

    return files


# ===== UI =====
st.markdown("<div class='main-title'>🎬 VoxCPM2 Dubbing</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>Video → Speaker Diarization → Voice Clone Dubbing</div>", unsafe_allow_html=True)
st.divider()

# Step 1 — Video Upload
st.subheader("📹 Step 1 — Video Upload")
vid = st.file_uploader("📹 Video", type=["mp4","mov","avi","mkv"], label_visibility="collapsed")
if vid: st.success(f"✅ Video — {vid.size/(1024*1024):.1f} MB")
st.divider()

# Step 2 — Speaker Diarization
st.subheader("🎙️ Step 2 — Speaker Diarization")
st.caption("⚡ pyannote — လူတစ်ယောက်ချင်းစီ — အသံ ခွဲ")

if not HF_TOKEN:
    st.warning("⚠️ HF_TOKEN မထည့်ထားဘူး — Hugging Face Settings → Tokens → Read Token ယူပါ")
    hf_token_input = st.text_input("HF Token", type="password", placeholder="hf_...")
    if hf_token_input:
        HF_TOKEN = hf_token_input
        st.session_state.hf_token = hf_token_input
elif "hf_token" in st.session_state:
    HF_TOKEN = st.session_state.hf_token

if vid and HF_TOKEN:
    if st.button("🎙️ Speaker Diarization — Start", use_container_width=True):
        vid.seek(0)
        with open("diarize_input.mp4", "wb") as f:
            f.write(vid.read())
        try:
            with st.spinner("🎙️ Speaker Diarization လုပ်နေသည်..."):
                segments, speakers, speaker_samples = diarize_speakers("diarize_input.mp4")
            st.session_state.segments = segments
            st.session_state.speakers = speakers
            st.session_state.speaker_samples = speaker_samples
            st.success(f"✅ Speaker {len(speakers)} ယောက် တွေ့ပြီး")
        except Exception as e:
            st.error(f"❌ Diarization — {e}")
        finally:
            vid.seek(0)
elif vid:
    st.info("📹 Video တင်ပြီး — HF Token ထည့်ပါ")
st.divider()

# Step 3 — Speaker Scripts
if "speakers" in st.session_state and st.session_state.speakers:
    st.subheader("📝 Step 3 — Speaker Scripts")
    st.caption("👤 Speaker တစ်ယောက်ချင်း — မြန်မာ Script ရေးပါ")

    if "speaker_scripts" not in st.session_state:
        st.session_state.speaker_scripts = {}

    for spk in st.session_state.speakers:
        with st.expander(f"👤 {spk} — Script", expanded=False):
            # Sample Audio Player
            sample_path = st.session_state.speaker_samples.get(spk)
            if sample_path and os.path.exists(sample_path):
                st.audio(sample_path, format="audio/wav")

            script_input = st.text_area(
                f"Script — {spk}",
                value=st.session_state.speaker_scripts.get(spk, ""),
                height=120,
                key=f"script_{spk}",
                placeholder="မြန်မာ Script ရေးပါ..."
            )
            st.session_state.speaker_scripts[spk] = script_input

    st.divider()
# Step 4 — Generate Dubbing
st.subheader("🚀 Step 4 — Generate Dubbing")

if "speakers" in st.session_state and st.session_state.speakers:
    if st.button("✨ Generate Dubbing Video", type="primary", use_container_width=True):
        if not HF_TOKEN:
            st.error("HF Token မရှိဘူး")
            st.stop()

        vid.seek(0)
        with open("input.mp4", "wb") as f:
            f.write(vid.read())
        _, _, vdur = vid_info("input.mp4")

        # Speaker တစ်ယောက်ချင်း — TTS
        all_audio = {}  # {spk: [files]}
        for spk in st.session_state.speakers:
            spk_script = st.session_state.speaker_scripts.get(spk, "").strip()
            if not spk_script:
                continue

            st.write(f"🎙️ {spk} — TTS...")
            pb = st.progress(0)

            chunks = split_scr(spk_script, TTS_CHUNK)
            sample = st.session_state.speaker_samples.get(spk)

            def cb(i, tot, c, _pb=pb, _spk=spk):
                _pb.progress((i+1)/tot)
                st.caption(f"👤 {_spk} — [{i+1}/{tot}]")

            try:
                files = tts_with_ref(chunks, sample, "openbmb/VoxCPM-Demo", cb)
                # Concat
                concat_file = f"dub_concat_{spk}.txt"
                with open(concat_file, "w", encoding="utf-8") as f:
                    for a in files: f.write(f"file '{a}'\n")

                spk_audio = f"dub_{spk}.mp3"
                ffmpeg.input(concat_file, format="concat", safe=0).output(
                    spk_audio, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
                ).run(overwrite_output=True)

                all_audio[spk] = spk_audio
            except Exception as e:
                st.error(f"❌ {spk} — TTS — {e}")
                st.stop()

        # Timeline — Speaker Segment တစ်ခုချင်း — Audio ထည့်
        st.write("🎬 Timeline — Render...")
        with st.spinner("Timeline ပြန်ထည့်နေသည်..."):
            # မူရင်း Video — Mute + Audio Track ထည့်
            cmd = ["ffmpeg", "-y", "-i", "input.mp4"]

            # Speaker Audio File တွေ — Input ထည့်
            spk_list = list(all_audio.keys())
            for spk in spk_list:
                cmd += ["-i", all_audio[spk]]

            # Filter Complex
            filters = []
            audio_labels = []

            # Video — Original
            filters.append("[0:v]copy[v]")

            # Speaker Segment တစ်ခုချင်း — Split + Delay + Mix
            amix_inputs = []
            for idx, spk in enumerate(spk_list, start=1):
                # Speaker Segment တွေ — Timeline အတိုင်း — Trim + Delay
                spk_segs = [s for s in st.session_state.segments if s["speaker"] == spk]
                for si, seg in enumerate(spk_segs):
                    start_ms = int(seg["start"] * 1000)
                    dur = seg["end"] - seg["start"]

                    # Audio — Trim + Delay
                    label = f"a_{spk}_{si}"
                    filters.append(
                        f"[{idx}:a]atrim=0:{dur:.3f},"
                        f"adelay={start_ms}|{start_ms}[{label}]"
                    )
                    amix_inputs.append(f"[{label}]")

            # Mix All
            if amix_inputs:
                filters.append(f"{''.join(amix_inputs)}amix=inputs={len(amix_inputs)}:duration=first[aout]")
                filter_complex = ";".join(filters)
                cmd += ["-filter_complex", filter_complex,
                        "-map", "[v]", "-map", "[aout]"]
            else:
                filter_complex = ";".join(filters)
                cmd += ["-filter_complex", filter_complex, "-map", "[v]"]

            cmd += ["-c:v", "libx264", "-crf", str(ENC_CRF), "-preset", ENC_PRESET,
                    "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest",
                    "dub_final.mp4"]

            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="ignore")
            if r.returncode != 0:
                st.error(f"❌ FFmpeg — {(r.stderr or '')[-400:]}")
                st.stop()

        st.success(f"✅ Dubbing Video — ပြီးပါပြီ")
        st.video("dub_final.mp4")

        with open("dub_final.mp4", "rb") as f:
            st.download_button("📥 Download Dubbing Video", f, file_name="dubbing.mp4")
else:
    st.info("📹 Video Upload → HF Token → Diarization → Scripts → Generate")