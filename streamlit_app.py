import streamlit as st
import os
import re
import ffmpeg
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFont
import cv2
import base64
import requests
from gradio_client import Client, handle_file

SPACES = [
    {"space": "openbmb/VoxCPM-Demo", "type": "demo"},
    {"space": "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts", "type": "burmese"},
]
PASSWORD = "voxcpm2026"
FONT_FILE = "MyanmarPadaung.ttf"

# Fast FFmpeg Encoding Settings
FS, BH, BA = 30, 100, 100
ENC_PRESET = "ultrafast"
ENC_CRF = 23
AUDIO_BITRATE = "128k"
TTS_CHUNK = 600

st.set_page_config(page_title="VoxCPM2 Recap (No API Key)", page_icon="🎬", layout="centered")

st.markdown("""
<style>
.stApp { background: linear-gradient(160deg, #0f0f23, #1a1a35, #0f0f23); color: #e8e8f0; }
#MainMenu, footer, header { visibility: hidden; }
.main-title {
    text-align: center; font-size: 2.2rem; font-weight: 900;
    background: linear-gradient(90deg, #00f2fe, #4facfe, #00c6ff);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text; margin-bottom: 4px;
}
.main-sub { text-align: center; color: #8888aa; font-size: 0.9rem; margin-bottom: 20px; }
.stButton>button {
    background: linear-gradient(135deg, #00c6ff, #0072ff) !important;
    color: #fff !important; border: none !important; border-radius: 10px !important;
    padding: 12px 20px !important; font-weight: 600 !important;
    box-shadow: 0 4px 15px rgba(0,198,255,0.3) !important;
}
.stButton>button:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 6px 20px rgba(0,198,255,0.5) !important;
}
.stTextArea textarea {
    background: rgba(255, 255, 255, 0.04) !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important; color: #fff !important;
    border-radius: 10px !important;
}
.stTextArea textarea:focus {
    border-color: #00c6ff !important;
    box-shadow: 0 0 0 2px rgba(0,198,255,0.2) !important;
}
.stFileUploader { background: rgba(255, 255, 255, 0.02); border-radius: 10px; padding: 8px; }
.stAlert { border-radius: 10px !important; border: none !important; }
hr { border-color: rgba(255, 255, 255, 0.08); margin: 24px 0; }
</style>
""", unsafe_allow_html=True)

if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.markdown("<div class='main-title'>🔐 Private App</div>", unsafe_allow_html=True)
    st.markdown("<div class='main-sub'>Password ထည့်ပြီး ဝင်ပါ</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        pwd = st.text_input("Password", type="password", label_visibility="collapsed", placeholder="Enter Password")
        if st.button("Login", use_container_width=True):
            if pwd == PASSWORD:
                st.session_state.auth = True
                st.rerun()
            else:
                st.error("Password မှားယွင်းနေပါသည်")
    st.stop()

def generate_script_local_ollama(video_path, ollama_url="http://localhost:11434", model_name="llama3.2-vision"):
    """
    API Key လုံးဝ မလိုအပ်ဘဲ Local စက်ပေါ်ရှိ Ollama (Llama 3.2 Vision) ကို တိုက်ရိုက် ခေါ်ယူသည့် စနစ်
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception("Video ဖိုင်ကို ဖတ်ရှု၍ မရပါ")
        
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_count = 8
    step = max(1, total_frames // frame_count)
    
    images_b64 = []
    curr = 0
    while cap.isOpened() and curr < total_frames and len(images_b64) < frame_count:
        cap.set(cv2.CAP_PROP_POS_FRAMES, curr)
        ret, frame = cap.read()
        if not ret:
            break
        resized = cv2.resize(frame, (512, 288))
        _, buffer = cv2.imencode('.jpg', resized, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        b64 = base64.b64encode(buffer).decode('utf-8')
        images_b64.append(b64)
        curr += step
    cap.release()
    
    if not images_b64:
        raise Exception("Video မှ Keyframes များ ထုတ်ယူ၍ မရရှိပါ")
        
    prompt = (
        "Watch these video frames and write a continuous movie recap script in Myanmar language "
        "for audio voiceover narration. Return plain Myanmar narration text only without markdown headings or brackets."
    )
    
    payload = {
        "model": model_name,
        "prompt": prompt,
        "images": images_b64,
        "stream": False
    }
    
    try:
        resp = requests.post(f"{ollama_url}/api/generate", json=payload, timeout=120)
        if resp.status_code != 200:
            raise Exception(f"Local Ollama Server Error ({resp.status_code}). Please make sure 'ollama run {model_name}' is active.")
        data = resp.json()
        text = data.get("response", "")
        cleaned = re.sub(r'#+\s*', '', text)
        cleaned = re.sub(r'\*\*|\*', '', cleaned)
        cleaned = re.sub(r'\[.*?\]', '', cleaned)
        return cleaned.strip()
    except requests.exceptions.ConnectionError:
        raise Exception("Local Ollama Server (http://localhost:11434) သို့ ချိတ်ဆက်၍ မရပါ။ စက်ထဲတွင် Ollama ဖွင့်ထားပါသလား သို့မဟုတ် Script ကို အောက်တွင် တိုက်ရိုက် ရိုက်ထည့်/Paste လုပ်နိုင်ပါသည်။")

def vid_info(p):
    """Retrieve video dimensions and duration using ffmpeg probe."""
    pr = ffmpeg.probe(p)
    v = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    return int(v['width']), int(v['height']), float(pr['format']['duration'])

def t2s(s):
    """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)."""
    ms = int(round((s - int(s)) * 1000)); tot = int(s)
    if ms >= 1000: tot += 1; ms = 0
    h, r = divmod(tot, 3600); m, sec = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

def s2t(ts):
    """Parse SRT timestamp format to floating point seconds."""
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

def render_png(text, out, fp, W, H, fs=30, pos="center", bh=100, ba=100):
    """Render subtitle text onto a transparent PNG overlay image."""
    img = Image.new("RGBA", (W, H), (0,0,0,0)); d = ImageDraw.Draw(img)
    try: f = ImageFont.truetype(fp, fs)
    except: f = ImageFont.load_default()
    by = (H-bh)//2 if pos=="center" else (H-bh if pos=="bottom" else 0)
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
    """Convert full Myanmar text script into synchronized SRT subtitles."""
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
    """Parse SRT file entries into structured objects."""
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

def overlay(vp, sp, op, fp, fs=30, pos="center", bh=100, ba=100):
    """Burn PNG subtitle layers into video using FFmpeg complex filter."""
    W, H, _ = vid_info(vp); segs = parse_srt(sp)
    if not segs: raise Exception("SRT file is empty or invalid")
    os.makedirs("subtitle_pngs", exist_ok=True); pngs = []
    for i, s in enumerate(segs):
        p = f"subtitle_pngs/s_{i:04d}.png"
        render_png(s["text"], p, fp, W, H, fs, pos, bh, ba)
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
    if r.returncode != 0: raise Exception(f"FFmpeg Error: {(r.stderr or '')[-500:]}")
    for x in pngs:
        try: os.remove(x["p"])
        except: pass
    return op

def split_scr(t, mc=TTS_CHUNK):
    """Split long script text into chunks for TTS processing."""
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

def tts_run(chunks, ref, space, cb=None):
    """Call standard VoxCPM Gradio TTS endpoint."""
    cl = Client(space); files = []; rf = handle_file(ref) if ref else None
    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks), c)
        res = cl.predict(text_input=c,
            control_instruction="A warm young woman, calm and expressive",
            reference_wav_path_input=rf, use_prompt_text=False,
            prompt_text_input="", cfg_value_input=2.0,
            do_normalize=True, denoise=False, api_name="/generate")
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = f"chunk_{i}.wav"; shutil.copy(p, dst); files.append(dst)
    return files

def tts_burmese(chunks, ref, space, cb=None):
    """Call Burmese customized VoxCPM Gradio TTS endpoint."""
    cl = Client(space); files = []
    if not ref: raise Exception("Reference Audio needed for Burmese TTS")
    rf = handle_file(ref)
    for i, c in enumerate(chunks):
        if cb: cb(i, len(chunks), c)
        res = cl.predict(target_text=c, ref_audio=rf,
            ref_text="မြန်မာ အသံနမူနာ", cfg_value=2.0,
            inference_timesteps=10, api_name="/tts")
        p = res[0] if isinstance(res, (tuple, list)) else res
        dst = f"chunk_b_{i}.wav"; shutil.copy(p, dst); files.append(dst)
    return files

def tts_all(text, out, ref=None, cb=None):
    """Orchestrate TTS synthesis across available spaces and concatenate output."""
    chunks = split_scr(text, TTS_CHUNK); files = None
    for s in SPACES:
        try:
            if s["type"] == "demo": files = tts_run(chunks, ref, s["space"], cb)
            else: files = tts_burmese(chunks, ref, s["space"], cb)
            break
        except Exception:
            files = None; continue
    if files is None: raise Exception("TTS Synthesis failed on all configured spaces.")
    with open("concat.txt", "w", encoding="utf-8") as f:
        for a in files: f.write(f"file '{a}'\n")
    ffmpeg.input("concat.txt", format="concat", safe=0).output(
        out, acodec="libmp3lame", audio_bitrate=AUDIO_BITRATE, ar=48000
    ).run(overwrite_output=True)
    return out

st.markdown("<div class='main-title'>🎬 VoxCPM2 Recap</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>၁၀၀% API Key မလိုသော Video → မြန်မာ Script → Recap Video</div>", unsafe_allow_html=True)
st.caption(f"⚡ Fast Mode — {ENC_PRESET} @ CRF {ENC_CRF}")
st.divider()

# Step 1 & 2: Local AI / Manual Script
st.subheader("📝 Step 1 & 2 — Movie Recap Script (No API Key)")

if "script" not in st.session_state: 
    st.session_state.script = ""

script_vid = st.file_uploader("📹 Video တင်ပါ (Auto AI Script ထုတ်ယူရန်)", type=["mp4", "mov", "avi", "mkv"], key="script_gen_vid")

c1, c2 = st.columns(2)
with c1:
    local_model = st.text_input("💻 Local AI Model Name", value="llama3.2-vision", help="Ollama model name e.g. llama3.2-vision, llava")
with c2:
    ollama_host = st.text_input("🌐 Local Ollama Server", value="http://localhost:11434")

if script_vid:
    if st.button("🤖 Local AI (Ollama) ဖြင့် Script အလိုအလျောက် ရေးခိုင်းမည်", type="primary", use_container_width=True):
        with st.spinner("🎬 Local AI ဖြင့် Video Frames များကို စိစစ်ပြီး Script ရေးသားနေပါသည်..."):
            try:
                temp_vid_path = "temp_script_input.mp4"
                with open(temp_vid_path, "wb") as f:
                    f.write(script_vid.read())
                
                generated_script = generate_script_local_ollama(temp_vid_path, ollama_url=ollama_host, model_name=local_model)
                st.session_state.script = generated_script
                st.success("✨ Local AI ဖြင့် Script ထုတ်ယူပြီးပါပြီ!")
                
                if os.path.exists(temp_vid_path):
                    os.remove(temp_vid_path)
            except Exception as e:
                st.warning(f"💡 {e}")

script = st.text_area("📝 မြန်မာ Script (တိုက်ရိုက် ရိုက်ထည့် သို့မဟုတ် Paste လုပ်ပါ)", value=st.session_state.script, height=220, placeholder="မြန်မာ Script ဤနေရာတွင် တိုက်ရိုက် Paste/ရိုက်ထည့်ပါ...")
st.session_state.script = script

c1, c2 = st.columns([3, 1])
with c1: 
    st.caption(f"📝 စာလုံး — {len(script):,}")
with c2:
    if st.button("🗑️ Clear", use_container_width=True):
        st.session_state.script = ""
        st.rerun()

st.divider()

# Step 3: Ref Audio & Video Upload
st.subheader("📁 Step 3 — Ref Audio + Video")
if "ref" not in st.session_state: st.session_state.ref = None
c1, c2 = st.columns(2)
with c1:
    ref = st.file_uploader("🎤 Ref Audio (Optional)", type=["wav","mp3","m4a"])
    if ref:
        with open("ref.wav", "wb") as f: f.write(ref.read())
        st.session_state.ref = "ref.wav"; st.success("✅ Ref Audio Ready")
with c2:
    vid = st.file_uploader("📹 Video Upload", type=["mp4","mov","avi","mkv"], key="final_recap_video")
    if vid: st.success(f"✅ Video — {vid.size/(1024*1024):.1f} MB")
st.divider()

# Step 4: Subtitle & Preview
st.subheader("📝 Step 4 — Subtitle")
use_sub = st.toggle("စာတန်းထိုး (Burn-in)", value=True)
if use_sub: st.caption(f"🔤 Font {FS}  •  ⬛ Box {BH}px  •  🎨 Opacity {BA}")

if vid and use_sub:
    st.markdown("**🖼️ Preview**")
    with st.spinner("Preview ပြုလုပ်နေပါသည်..."):
        vid.seek(0)
        with open("preview.mp4", "wb") as f: f.write(vid.read())
        W, H, _ = vid_info("preview.mp4")
        render_png("စာတန်းထိုး Preview", "prev.png", FONT_FILE, W, H, FS, "center", BH, BA)
        cap = cv2.VideoCapture("preview.mp4"); ok, fr = cap.read(); cap.release()
        if ok:
            bg = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).convert("RGBA")
            fg = Image.open("prev.png").convert("RGBA")
            comp = Image.alpha_composite(bg, fg); pw = 720
            comp.resize((pw, int(H*(pw/W))), Image.LANCZOS).convert("RGB").save("prev_out.png")
            st.image("prev_out.png", use_container_width=True)
st.divider()

# Step 5: Final Video Recap Generation
st.subheader("🚀 Step 5 — Generate Recap")
if st.button("✨ Generate Recap Video", type="primary", use_container_width=True):
    if not script.strip(): st.error("Script ရေးထားခြင်း မရှိပါ"); st.stop()
    if vid is None: st.error("Video Upload တင်ပါ"); st.stop()

    vid.seek(0)
    with open("input.mp4", "wb") as f: f.write(vid.read())
    _, _, vdur = vid_info("input.mp4")

    pb = st.progress(0); txt = st.empty()
    def cb(i, tot, c):
        pb.progress((i+1)/tot); txt.caption(f"[{i+1}/{tot}] {len(c)} စာလုံး")

    try: tts_all(script, "voice.mp3", st.session_state.ref, cb)
    except Exception as e: st.error(f"TTS Error — {e}"); st.stop()

    adur = float(ffmpeg.probe("voice.mp3")['format']['duration'])
    tempo = max(0.5, min(2.0, adur/vdur))

    sp = scr_to_srt(script, vdur, "sub.srt") if use_sub else None

    with st.spinner("🎬 Rendering — Fast Mode..."):
        vi = ffmpeg.input("input.mp4")
        va = ffmpeg.input("voice.mp3").audio.filter('atempo', tempo)
        ffmpeg.output(vi.video, va, "temp.mp4",
                       vcodec='libx264', crf=ENC_CRF, preset=ENC_PRESET,
                       acodec='aac', audio_bitrate=AUDIO_BITRATE, shortest=None
                       ).run(overwrite_output=True)

        if use_sub and sp:
            overlay("temp.mp4", sp, "final.mp4", FONT_FILE, FS, "center", BH, BA)
        else:
            shutil.copy("temp.mp4", "final.mp4")

    st.success(f"✅ ပြီးစီးပါပြီ — Audio Duration: {adur:.0f}s @ Tempo: {tempo:.2f}x")
    st.video("final.mp4")

    with open("final.mp4", "rb") as f:
        st.download_button("📥 Download Recap Video", f, file_name="recap.mp4")
```html
<!DOCTYPE html>
<html lang="my" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Myanmar Movie Recap Script Generator AI</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- FontAwesome Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <!-- Google Fonts for Myanmar & English -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Padauk:wght@400;700&display=swap" rel="stylesheet">
    
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['Inter', 'Padauk', 'sans-serif'],
                        myanmar: ['Padauk', 'sans-serif'],
                    },
                    colors: {
                        brand: {
                            50: '#f0f5ff',
                            100: '#e0ebff',
                            500: '#3b82f6',
                            600: '#2563eb',
                            700: '#1d4ed8',
                            800: '#1e40af',
                            900: '#1e3a8a',
                            950: '#0f172a',
                        }
                    }
                }
            }
        }
    </script>
    <style>
        body {
            font-family: 'Padauk', 'Inter', sans-serif;
            background-color: #0b0f19;
            color: #e2e8f0;
        }
        /* Custom scrollbar for Myanmar text */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        ::-webkit-scrollbar-track {
            background: #111827;
        }
        ::-webkit-scrollbar-thumb {
            background: #374151;
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: #4b5563;
        }
        .myanmar-text-view {
            line-height: 2.2;
            font-size: 1.15rem;
            letter-spacing: 0.02em;
        }
    </style>
</head>
<body class="min-h-screen flex flex-col bg-slate-950 text-slate-100">

    <!-- Top Navigation Bar -->
    <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-40">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center text-white font-bold text-xl shadow-lg shadow-blue-500/20">
                    <i class="fa-solid fa-film"></i>
                </div>
                <div>
                    <h1 class="text-lg font-bold text-white leading-tight">Myanmar Movie Recap Generator</h1>
                    <p class="text-xs text-slate-400">AI Multimodal Script Creator for Audio Narration</p>
                </div>
            </div>

            <!-- API Key Controls & Status -->
            <div class="flex items-center gap-3">
                <button onclick="openApiKeyModal()" class="px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 text-xs font-medium transition flex items-center gap-2">
                    <i class="fa-solid fa-key text-amber-400"></i>
                    <span id="apiKeyBadgeText">Gemini API Key</span>
                </button>
            </div>
        </div>
    </header>

    <!-- Main Content Grid -->
    <main class="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 grid grid-cols-1 lg:grid-cols-12 gap-6">

        <!-- Left Column: Video Input & Config (5 cols) -->
        <section class="lg:col-span-5 flex flex-col gap-5">
            
            <!-- Video Upload Card -->
            <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl">
                <h2 class="text-sm font-semibold text-slate-200 mb-3 flex items-center gap-2">
                    <i class="fa-solid fa-file-video text-blue-400"></i>
                    ၁။ ဗီဒီယိုဖိုင် တင်ယူပါ (Upload Video)
                </h2>

                <div id="dropzone" onclick="document.getElementById('videoFileInput').click()" class="border-2 border-dashed border-slate-700 hover:border-blue-500 bg-slate-950/50 rounded-xl p-6 text-center cursor-pointer transition group flex flex-col items-center justify-center min-h-[160px]">
                    <i class="fa-solid fa-cloud-arrow-up text-3xl text-slate-500 group-hover:text-blue-400 mb-2 transition transform group-hover:-translate-y-1"></i>
                    <p class="text-sm font-medium text-slate-300">ဗီဒီယိုဖိုင်အား ဤနေရာသို့ ဆွဲထည့်ပါ သို့မဟုတ် နှိပ်ပါ</p>
                    <p class="text-xs text-slate-500 mt-1">MP4, WEBM, MOV (Max 500MB)</p>
                    <input type="file" id="videoFileInput" accept="video/*" class="hidden" onchange="handleFileSelect(event)">
                </div>

                <!-- Video Preview Box (Hidden initially) -->
                <div id="videoContainer" class="hidden mt-4">
                    <video id="videoPlayer" controls class="w-full rounded-xl bg-black max-h-64 object-contain border border-slate-800"></video>
                    
                    <div class="mt-3 flex items-center justify-between text-xs text-slate-400 px-1">
                        <span id="videoNameDisplay" class="truncate max-w-[200px]"></span>
                        <span id="videoDurationDisplay" class="font-mono bg-slate-800 px-2 py-0.5 rounded text-blue-300">00:00</span>
                    </div>

                    <!-- Keyframes Preview Drawer -->
                    <div class="mt-4 pt-3 border-t border-slate-800">
                        <div class="flex items-center justify-between mb-2">
                            <span class="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                                <i class="fa-solid fa-images text-indigo-400"></i>
                                စစ်ဆေးထုတ်ယူထားသော Frames (<span id="frameCount">0</span>)
                            </span>
                            <button onclick="reExtractFrames()" class="text-[11px] text-blue-400 hover:underline">Re-sample</button>
                        </div>
                        <div id="frameThumbnails" class="flex gap-2 overflow-x-auto pb-2 scrollbar-thin">
                            <!-- Canvas Thumbnails injected dynamically -->
                        </div>
                    </div>
                </div>
            </div>

            <!-- Custom Myanmar Prompt Box -->
            <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl flex flex-col gap-3">
                <div class="flex items-center justify-between">
                    <h2 class="text-sm font-semibold text-slate-200 flex items-center gap-2">
                        <i class="fa-solid fa-wand-magic-sparkles text-amber-400"></i>
                        ၂။ AI Prompt စည်းမျဉ်း
                    </h2>
                    <button onclick="resetPrompt()" class="text-xs text-slate-400 hover:text-slate-200">Reset Prompt</button>
                </div>

                <textarea id="promptInput" rows="4" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-slate-200 focus:outline-none focus:border-blue-500 font-myanmar resize-none leading-relaxed"></textarea>

                <div class="p-3 bg-blue-950/40 border border-blue-900/50 rounded-xl text-xs text-blue-300 flex items-start gap-2">
                    <i class="fa-solid fa-circle-info text-blue-400 mt-0.5 shrink-0"></i>
                    <span>အထက်ပါ Prompt သည် ခေါင်းစဉ်များ၊ ခွဲခြားစကားများနှင့် သင်္ကေတများ မပါဘဲ သန့်ရှင်းသော မြန်မာစကားပြော Script သာ ထွက်ရှိအောင် ပြုလုပ်ထားပါသည်။</span>
                </div>

                <!-- Action Button -->
                <button id="generateBtn" onclick="startScriptGeneration()" disabled class="w-full py-3.5 px-4 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold shadow-lg shadow-blue-600/25 transition flex items-center justify-center gap-2 text-sm mt-1">
                    <i class="fa-solid fa-bolt"></i>
                    <span>Recap Script စတင်ရေးသားမည်</span>
                </button>
            </div>

        </section>

        <!-- Right Column: Clean Myanmar Script Output (7 cols) -->
        <section class="lg:col-span-7 flex flex-col">
            <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl flex-1 flex flex-col h-full min-h-[500px]">
                
                <!-- Script Output Header -->
                <div class="flex flex-wrap items-center justify-between gap-3 pb-4 border-b border-slate-800">
                    <div class="flex items-center gap-2">
                        <div class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></div>
                        <h2 class="text-sm font-semibold text-slate-100">မြန်မာ စကားပြော Script (Plain Myanmar Narration)</h2>
                    </div>

                    <!-- Quick Tools -->
                    <div class="flex items-center gap-2 text-xs">
                        <button onclick="cleanScriptTextManual()" class="px-3 py-1.5 rounded-lg bg-indigo-950 hover:bg-indigo-900 text-indigo-300 border border-indigo-800/80 transition flex items-center gap-1.5" title="မလိုအပ်သော သင်္ကေတများ ရှင်းလင်းမည်">
                            <i class="fa-solid fa-wand-magic"></i>
                            <span>Clean Text</span>
                        </button>
                        <button onclick="copyScriptToClipboard()" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center gap-1.5" title="ကူးယူမည်">
                            <i class="fa-regular fa-copy text-blue-400"></i>
                            <span>Copy</span>
                        </button>
                        <button onclick="downloadScriptTxt()" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center gap-1.5" title="ဆွဲယူမည် (.txt)">
                            <i class="fa-solid fa-download text-emerald-400"></i>
                            <span>Export .txt</span>
                        </button>
                        <button onclick="toggleTeleprompter()" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center gap-1.5" title="Teleprompter">
                            <i class="fa-solid fa-scroll text-amber-400"></i>
                            <span>Teleprompter</span>
                        </button>
                    </div>
                </div>

                <!-- Main Myanmar Output Area -->
                <div class="relative flex-1 mt-4 flex flex-col">
                    
                    <!-- Text Area / Preview Display -->
                    <textarea id="scriptOutput" class="w-full flex-1 bg-slate-950 border border-slate-800 rounded-xl p-5 text-slate-100 myanmar-text-view focus:outline-none focus:border-blue-500 font-myanmar resize-none leading-relaxed" placeholder="ဗီဒီယို ဖိုင်တင်ပြီး 'Recap Script စတင်ရေးသားမည်' ကို နှိပ်ပါ..." spellcheck="false"></textarea>

                    <!-- Loading Overlay inside script box -->
                    <div id="generationLoading" class="hidden absolute inset-0 bg-slate-950/90 backdrop-blur-sm rounded-xl flex flex-col items-center justify-center p-6 text-center z-20">
                        <div class="relative w-16 h-16 mb-4">
                            <div class="absolute inset-0 rounded-full border-4 border-blue-500/20 border-t-blue-500 animate-spin"></div>
                            <i class="fa-solid fa-brain absolute inset-0 m-auto text-xl text-blue-400 flex items-center justify-center"></i>
                        </div>
                        <p id="loadingStatusText" class="text-sm font-medium text-slate-200 mb-1">ဗီဒီယို အချက်အလက်များကို AI ဖြင့် စိစစ်နေပါသည်...</p>
                        <p class="text-xs text-slate-400">သန့်ရှင်းသော မြန်မာ စကားပြော ရာဇဝင်/Script ကို သီးသန့် ရေးသားနေပါသည်</p>
                    </div>
                </div>

                <!-- Stats Bar -->
                <div class="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
                    <div class="flex items-center gap-4">
                        <span>စကားလုံးပေါင်း: <strong id="wordCountDisplay" class="text-slate-200">0</strong></span>
                        <span>ခန့်မှန်း ဖတ်ကြားချိန်: <strong id="readingTimeDisplay" class="text-slate-200">0 မိနစ်</strong></span>
                    </div>
                    <div class="text-[11px] text-slate-500">
                        Gemini 3 Flash Multimodal AI
                    </div>
                </div>

            </div>
        </section>

    </main>

    <!-- Fullscreen Teleprompter Modal -->
    <div id="prompterModal" class="fixed inset-0 bg-black/95 z-50 hidden flex flex-col">
        <div class="p-4 border-b border-slate-800 bg-slate-900 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <i class="fa-solid fa-scroll text-amber-400 text-lg"></i>
                <h3 class="font-bold text-slate-100">Audio Voiceover Teleprompter</h3>
            </div>
            <div class="flex items-center gap-4">
                <div class="flex items-center gap-2">
                    <label class="text-xs text-slate-400">Font Size:</label>
                    <button onclick="changePrompterFontSize(-2)" class="w-8 h-8 rounded bg-slate-800 text-slate-200 font-bold">-</button>
                    <button onclick="changePrompterFontSize(2)" class="w-8 h-8 rounded bg-slate-800 text-slate-200 font-bold">+</button>
                </div>
                <button id="scrollToggleBtn" onclick="togglePrompterScroll()" class="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium text-xs flex items-center gap-2">
                    <i class="fa-solid fa-play"></i>
                    <span>Auto Scroll</span>
                </button>
                <button onclick="toggleTeleprompter()" class="w-8 h-8 rounded-full bg-slate-800 text-slate-400 hover:text-white flex items-center justify-center">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>
        </div>

        <div id="prompterScrollArea" class="flex-1 overflow-y-auto p-12 text-center flex flex-col items-center">
            <div id="prompterText" class="max-w-4xl w-full text-slate-100 myanmar-text-view font-myanmar leading-loose pt-20 pb-64 text-2xl">
                <!-- Text populated dynamically -->
            </div>
        </div>
    </div>

    <!-- API Key Settings Modal -->
    <div id="apiKeyModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 shadow-2xl">
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-base font-bold text-slate-100 flex items-center gap-2">
                    <i class="fa-solid fa-key text-amber-400"></i>
                    Gemini API Key ထည့်သွင်းရန်
                </h3>
                <button onclick="closeApiKeyModal()" class="text-slate-400 hover:text-white">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>

            <p class="text-xs text-slate-400 mb-4 leading-relaxed">
                Gemini API Key ထည့်သွင်းထားပါက ပိုမိုမြန်ဆန်စွာ စာမူထုတ်ယူနိုင်ပါမည်။ API Key မရှိပါကလည်း စနစ်မှ အခမဲ့ စမ်းသပ်ခွင့် ပေးထားပါသည်။
            </p>

            <input type="password" id="apiKeyInput" placeholder="AIzaSy..." class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-slate-100 focus:outline-none focus:border-blue-500 font-mono mb-4">

            <div class="flex items-center justify-end gap-2">
                <button onclick="closeApiKeyModal()" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-medium text-slate-300">မလုပ်ဆောင်ပါ</button>
                <button onclick="saveApiKey()" class="px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-xs font-medium text-white">သိမ်းဆည်းမည်</button>
            </div>
        </div>
    </div>

    <!-- Toast Notification Container -->
    <div id="toastContainer" class="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none"></div>

    <!-- Hidden Canvas for sampling frames -->
    <canvas id="hiddenCanvas" class="hidden"></canvas>

    <script>
        // Default Prompt targeting exact pure Myanmar script narration
        const DEFAULT_PROMPT = "Watch this video carefully and write a clear, continuous movie recap script in Myanmar language for audio narration that matches the length of the video. Return plain speech text only without markdown titles.";

        // Application State
        let customApiKey = localStorage.getItem('user_gemini_api_key') || "";
        let uploadedVideoFile = null;
        let videoDuration = 0;
        let extractedFrames = []; // Array of Base64 strings
        let isGenerating = false;
        let prompterScrollInterval = null;
        let isPrompterScrolling = false;
        let prompterFontSize = 24;

        // Initialize on Load
        window.onload = function() {
            document.getElementById('promptInput').value = DEFAULT_PROMPT;
            updateApiKeyBadge();
            setupDropzone();
            
            // Textarea auto-update stats
            document.getElementById('scriptOutput').addEventListener('input', updateScriptStats);
        };

        // --- Video & Canvas Keyframe Sampling ---
        function setupDropzone() {
            const dropzone = document.getElementById('dropzone');
            
            ['dragenter', 'dragover'].forEach(eventName => {
                dropzone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    dropzone.classList.add('border-blue-500', 'bg-blue-950/20');
                }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                dropzone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    dropzone.classList.remove('border-blue-500', 'bg-blue-950/20');
                }, false);
            });

            dropzone.addEventListener('drop', (e) => {
                const dt = e.dataTransfer;
                const files = dt.files;
                if (files && files.length > 0) {
                    handleVideoFile(files[0]);
                }
            });
        }

        function handleFileSelect(event) {
            const file = event.target.files[0];
            if (file) {
                handleVideoFile(file);
            }
        }

        function handleVideoFile(file) {
            if (!file.type.startsWith('video/')) {
                showToast('ကျေးဇူးပြု၍ ဗီဒီယို ဖိုင်ကိုသာ တင်ပေးပါ', 'error');
                return;
            }

            uploadedVideoFile = file;
            const videoPlayer = document.getElementById('videoPlayer');
            const url = URL.createObjectURL(file);
            videoPlayer.src = url;

            document.getElementById('videoNameDisplay').innerText = file.name;
            document.getElementById('videoContainer').classList.remove('hidden');