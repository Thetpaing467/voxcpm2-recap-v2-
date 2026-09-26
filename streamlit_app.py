import streamlit as st
import os
import re
import time
import ffmpeg
import shutil
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2
from gradio_client import Client, handle_file

# ============================================================
# Config
# ============================================================
VOXCPM_SPACES = [
    {"name": "Primary — VoxCPM Demo", "space": "openbmb/VoxCPM-Demo", "type": "demo"},
    {"name": "Fallback — Burmese TTS", "space": "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts", "type": "burmese"},
]

PASSWORD = "voxcpm2026"
FONT_FILE = "MyanmarPadaung.ttf"

# Default Settings
DEFAULT_SUB_POSITION = "center"
DEFAULT_FONT_SIZE = 30
DEFAULT_BLUR_HEIGHT = 100
DEFAULT_BLUR_ALPHA = 160

# ============================================================
# Password
# ============================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔐 Private App")
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        if pwd == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ Password မှား")
    st.stop()

# ============================================================
# Helper Functions
# ============================================================
def get_video_info(video_path):
    probe = ffmpeg.probe(video_path)
    vs = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    duration = float(probe['format']['duration'])
    return int(vs['width']), int(vs['height']), duration


def srt_time(sec):
    ms = int(round((sec - int(sec)) * 1000))
    total = int(sec)
    if ms >= 1000:
        total += 1
        ms = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_to_sec(ts):
    ts = ts.strip()
    m = re.match(r'^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$', ts)
    if m:
        h, mi, se, ms = m.groups()
        return int(h)*3600 + int(mi)*60 + int(se) + int(ms.ljust(3, '0')) / 1000.0
    m = re.match(r'^(\d{1,2}):(\d{2})[,.](\d{1,3})$', ts)
    if m:
        mi, se, ms = m.groups()
        return int(mi)*60 + int(se) + int(ms.ljust(3, '0')) / 1000.0
    return None


def render_subtitle_png(text, output_path, font_path,
                         width, height, font_size=30,
                         position="center", blur_height=120,
                         blur_alpha=160):
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    if position == "bottom":
        box_y = height - blur_height
    elif position == "center":
        box_y = (height - blur_height) // 2
    else:
        box_y = 0

    draw.rectangle(
        [0, box_y, width, box_y + blur_height],
        fill=(0, 0, 0, blur_alpha)
    )

    max_chars_per_line = max(15, int(width / (font_size * 0.9)))
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars_per_line:
            cur = cur + " " + w if cur else w
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    line_h = int(font_size * 1.3)
    total_h = len(lines) * line_h
    text_y = box_y + (blur_height - total_h) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        line_x = (width - line_w) // 2

        for dx in [-2, -1, 0, 1, 2]:
            for dy in [-2, -1, 0, 1, 2]:
                draw.text((line_x + dx, text_y + dy), line,
                          font=font, fill=(0, 0, 0, 255))
        draw.text((line_x, text_y), line, font=font, fill=(255, 255, 255, 255))

        text_y += line_h

    img.save(output_path, "PNG")
    return output_path


def script_to_srt(script, audio_duration, srt_path, max_chars=30):
    sentences = script.replace("။", "။|").split("|")
    sentences = [s.strip() + "။" for s in sentences if s.strip()]
    if not sentences:
        return None

    split_sentences = []
    for sent in sentences:
        sent = sent.replace("။။", "။")
        if len(sent) <= max_chars:
            split_sentences.append(sent)
        else:
            words = sent.split()
            cur = ""
            for w in words:
                if len(cur) + len(w) + 1 <= max_chars:
                    cur = cur + " " + w if cur else w
                else:
                    if cur:
                        split_sentences.append(cur.strip())
                    cur = w
            if cur:
                split_sentences.append(cur.strip())

    if not split_sentences:
        return None

    total_chars = sum(len(s) for s in split_sentences)
    current = 0.0

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, sent in enumerate(split_sentences, 1):
            dur = (len(sent) / total_chars) * audio_duration
            f.write(f"{i}\n{srt_time(current)} --> {srt_time(current + dur)}\n{sent}\n\n")
            current += dur

    return srt_path


def parse_srt(srt_path):
    with open(srt_path, "r", encoding="utf-8") as f:
        raw = f.read().replace("\r\n", "\n").replace("\r", "\n")

    chunks = re.split(r"\n\s*\n", raw.strip())
    segments = []

    for chunk in chunks:
        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if len(lines) < 3:
            continue
        ts_line = next((ln for ln in lines if "-->" in ln), None)
        if not ts_line:
            continue

        parts = re.split(r"\s*-->\s*", ts_line)
        if len(parts) != 2:
            continue

        start = ts_to_sec(parts[0])
        end = ts_to_sec(parts[1])
        if start is None or end is None:
            continue

        ts_idx = lines.index(ts_line)
        text = " ".join(lines[ts_idx + 1:]).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})

    return segments


def overlay_subtitle_on_video(video_path, srt_path, output_path,
                                font_path, font_size=30,
                                position="center", blur_height=120,
                                blur_alpha=160):
    W, H, duration = get_video_info(video_path)

    segments = parse_srt(srt_path)
    if not segments:
        raise Exception("SRT — segments မရှိ")

    png_dir = "subtitle_pngs"
    os.makedirs(png_dir, exist_ok=True)

    png_files = []
    for i, seg in enumerate(segments):
        png_path = os.path.join(png_dir, f"sub_{i:04d}.png")
        render_subtitle_png(
            text=seg["text"],
            output_path=png_path,
            font_path=font_path,
            width=W,
            height=H,
            font_size=font_size,
            position=position,
            blur_height=blur_height,
            blur_alpha=blur_alpha
        )
        png_files.append({
            "path": png_path,
            "start": seg["start"],
            "end": seg["end"]
        })

    cmd = ["ffmpeg", "-y", "-i", video_path]
    for p in png_files:
        cmd += ["-i", p["path"]]

    filters = []
    current = "[0:v]"

    for i, p in enumerate(png_files):
        out_label = f"[v{i}]"
        filters.append(
            f"{current}[{i+1}:v]overlay=0:0:"
            f"enable='between(t,{p['start']:.3f},{p['end']:.3f})'"
            f"{out_label}"
        )
        current = out_label

    filter_complex = ";".join(filters)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", current,
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "medium",
        "-c:a", "copy",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        raise Exception(f"FFmpeg error:\n{(result.stderr or '')[-1000:]}")

    for p in png_files:
        try:
            os.remove(p["path"])
        except Exception:
            pass

    return output_path


def split_script(text, max_chars=400):
    sentences = text.replace("။", "။|").split("|")
    sentences = [s.strip() + "။" for s in sentences if s.strip()]
    chunks = []
    current = ""
    for s in sentences:
        if len(current) + len(s) <= max_chars:
            current += s
        else:
            if current:
                chunks.append(current)
            if len(s) > max_chars:
                for i in range(0, len(s), max_chars):
                    chunks.append(s[i:i + max_chars])
                current = ""
            else:
                current = s
    if current:
        chunks.append(current)
    return chunks


def tts_demo(chunks, ref_audio_path, space, progress_callback=None):
    client = Client(space)
    audio_files = []
    ref_file = handle_file(ref_audio_path) if ref_audio_path else None
    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback(i, len(chunks), chunk)
        result = client.predict(
            text_input=chunk,
            control_instruction="A warm young woman, calm and expressive",
            reference_wav_path_input=ref_file,
            use_prompt_text=False,
            prompt_text_input="",
            cfg_value_input=2.0,
            do_normalize=True,
            denoise=False,
            api_name="/generate",
        )
        audio_path = result[0] if isinstance(result, (tuple, list)) else result
        chunk_path = f"chunk_demo_{i}.wav"
        shutil.copy(audio_path, chunk_path)
        audio_files.append(chunk_path)
    return audio_files


def tts_burmese(chunks, ref_audio_path, space, progress_callback=None):
    client = Client(space)
    audio_files = []
    if not ref_audio_path:
        raise Exception(f"{space} — Reference Audio လိုတယ်")
    ref_file = handle_file(ref_audio_path)
    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback(i, len(chunks), chunk)
        result = client.predict(
            target_text=chunk,
            ref_audio=ref_file,
            ref_text="မြန်မာ အသံနမူနာ",
            cfg_value=2.0,
            inference_timesteps=10,
            api_name="/tts"
        )
        audio_path = result[0] if isinstance(result, (tuple, list)) else result
        chunk_path = f"chunk_burmese_{i}.wav"
        shutil.copy(audio_path, chunk_path)
        audio_files.append(chunk_path)
    return audio_files


def run_tts_chunked(text, output_path, ref_audio_path=None, progress_callback=None):
    chunks = split_script(text, max_chars=400)
    audio_files = None
    last_error = None

    for space_info in VOXCPM_SPACES:
        space = space_info["space"]
        name = space_info["name"]
        space_type = space_info["type"]

        try:
            st.info(f"🎙️ {name}...")
            if space_type == "demo":
                audio_files = tts_demo(chunks, ref_audio_path, space, progress_callback)
            else:
                audio_files = tts_burmese(chunks, ref_audio_path, space, progress_callback)
            st.success(f"✅ {name} — အောင်မြင်")
            break
        except Exception as e:
            last_error = str(e)
            st.warning(f"⚠️ {name} — Fail: {last_error[:120]}")
            audio_files = None
            continue

    if audio_files is None:
        raise Exception(f"❌ Space အားလုံး — Fail\nLast error: {last_error}")

    with open("concat_list.txt", "w", encoding="utf-8") as f:
        for audio in audio_files:
            f.write(f"file '{audio}'\n")

    ffmpeg.input("concat_list.txt", format="concat", safe=0).output(
        output_path, acodec="libmp3lame", audio_bitrate="192k", ar=48000
    ).run(overwrite_output=True)

    return output_path
    # ============================================================
# UI
# ============================================================
st.set_page_config(page_title="🎬 VoxCPM2 Movie Recap", page_icon="🎬")
st.title("🎬 VoxCPM2 Movie Recap")
st.write("Gemini Web မှ Script ရယူပြီး VoxCPM2 အသံနဲ့ Recap ဖန်တီးပါ")

if os.path.exists(FONT_FILE):
    st.sidebar.success(f"✅ Font: {FONT_FILE}")
else:
    st.sidebar.warning(f"⚠️ {FONT_FILE} — မရှိ")

# Step 1
st.header("📝 Step 1: Gemini Web → Script")
st.info("**Gemini Web:** [gemini.google.com](https://gemini.google.com) → Video Upload → Prompt → Script Copy")

with st.expander("📋 Prompt (Copy → Gemini Web)", expanded=True):
    st.code(
        "Watch this video carefully and write a clear, continuous movie recap script "
        "in Myanmar language for audio narration that matches the length of the video. "
        "Return plain speech text only without markdown titles.",
        language="text"
    )

# ============================================================
# Step 2: Script Paste + Delete Button
# ============================================================
st.header("📝 Step 2: Script Paste")

if "script_text" not in st.session_state:
    st.session_state.script_text = ""

# ⚠️ — Text Area — Key မပါ
script = st.text_area(
    "Script",
    value=st.session_state.script_text,
    height=250,
    placeholder="မြန်မာ Script paste..."
)

st.session_state.script_text = script

# 🆕 Counter
st.caption(f"📝 စာလုံး: {len(script)}")

# 🆕 Delete Button — ကြီး + သီးသန့်
st.markdown("---")
if st.button("🗑️  Script အားလုံး ဖျက်မယ်  🗑️", type="primary", use_container_width=True):
    st.session_state.script_text = ""
    st.rerun()
st.markdown("---")

# Step 3
st.header("🎙️ Step 3: Reference Audio + Video")
if "ref_audio_path" not in st.session_state:
    st.session_state.ref_audio_path = None

ref_audio = st.file_uploader("Reference Audio", type=["wav", "mp3", "m4a"])
if ref_audio is not None:
    ref_path = "reference_voice.wav"
    with open(ref_path, "wb") as f:
        f.write(ref_audio.read())
    st.session_state.ref_audio_path = ref_path
    st.success("✅ Reference Audio")

video_file = st.file_uploader("📹 Video Upload", type=["mp4", "mov", "avi", "mkv"])

# Subtitle Settings
st.header("📝 Subtitle Settings")
use_subtitle = st.toggle("📝 စာတန်းထိုး (Burn-in)", value=True)

sub_position = DEFAULT_SUB_POSITION
sub_font_size = DEFAULT_FONT_SIZE
blur_height = DEFAULT_BLUR_HEIGHT
blur_alpha = DEFAULT_BLUR_ALPHA

st.info(
    f"📍 နေရာ: **အလယ်** | "
    f"🔤 Font Size: **{sub_font_size}** | "
    f"⬛ Blur Box: **{blur_height}px** | "
    f"🎨 Opacity: **{blur_alpha}**"
)

# Preview
if video_file is not None and use_subtitle:
    st.subheader("🖼️ Preview")
    with st.spinner("🖼️ Preview..."):
        temp_video_preview = "preview_video.mp4"
        video_file.seek(0)
        with open(temp_video_preview, "wb") as f:
            f.write(video_file.read())

        W, H, _ = get_video_info(temp_video_preview)
        preview_png = "preview_sub.png"
        render_subtitle_png(
            text="စာတန်းထိုး Preview",
            output_path=preview_png,
            font_path=FONT_FILE,
            width=W,
            height=H,
            font_size=sub_font_size,
            position=sub_position,
            blur_height=blur_height,
            blur_alpha=blur_alpha
        )

        cap = cv2.VideoCapture(temp_video_preview)
        ret, frame = cap.read()
        cap.release()

        if ret:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            bg = Image.fromarray(frame_rgb).convert("RGBA")
            fg = Image.open(preview_png).convert("RGBA")
            composite = Image.alpha_composite(bg, fg)

            pw = 720
            ph = int(H * (pw / W))
            composite.resize((pw, ph), Image.LANCZOS).convert("RGB").save("preview_result.png")
            st.image("preview_result.png", caption="🖼️ Preview", use_container_width=True)

# Sidebar
st.sidebar.header("🎙️ TTS Spaces")
for i, s in enumerate(VOXCPM_SPACES, 1):
    st.sidebar.write(f"**{i}.** `{s['space']}`")

# Step 4
st.header("🚀 Step 4: Generate Recap")

if st.button("✨ Generate Recap Video", type="primary"):
    if not script.strip():
        st.error("❌ Script paste — Step 2")
        st.stop()
    if video_file is None:
        st.error("❌ Video Upload — Step 3")
        st.stop()

    with st.spinner("📹 Video — စစ်ဆေးနေသည်..."):
        video_filename = "input_video.mp4"
        video_file.seek(0)
        with open(video_filename, "wb") as f:
            f.write(video_file.read())
        W, H, video_duration = get_video_info(video_filename)
        st.write(f"📹 Video: {W}x{H} | Duration: {video_duration:.2f}s")

    st.write("🎙️ VoxCPM2 → အသံ...")
    progress_bar = st.progress(0)
    status_text = st.empty()

    def update_progress(i, total, chunk):
        progress_bar.progress((i + 1) / total)
        status_text.write(f"🎙️ [{i+1}/{total}] ({len(chunk)} စာလုံး)")

    audio_path = "recap_voice.mp3"

    try:
        run_tts_chunked(script, audio_path,
                        ref_audio_path=st.session_state.ref_audio_path,
                        progress_callback=update_progress)
        st.write("✅ အသံ ထုတ်ပြီး")
        audio_dur = float(ffmpeg.probe(audio_path)['format']['duration'])
        st.write(f"🎙️ Audio: {audio_dur:.1f}s")
    except Exception as e:
        st.error(f"❌ VoxCPM2 error: {e}")
        st.stop()

    tempo = audio_dur / video_duration
    tempo = max(0.5, min(2.0, tempo))
    st.write(f"⚡ Audio Speed: {tempo:.2f}x")

    srt_path = None
    if use_subtitle:
        with st.spinner("📝 Script → SRT..."):
            srt_path = script_to_srt(script, video_duration, "recap.srt")
            if srt_path and os.path.exists(srt_path):
                st.success("✅ SRT — ဖန်တီးပြီး")

    with st.spinner("🎬 Recap Video Render..."):
        temp_video = "temp_recap.mp4"
        input_video = ffmpeg.input(video_filename)
        input_audio = ffmpeg.input(audio_path).audio.filter('atempo', tempo)

        stream = ffmpeg.output(
            input_video.video, input_audio, temp_video,
            vcodec='libx264', crf=18, preset='medium',
            acodec='aac', audio_bitrate='192k',
            shortest=None
        )
        ffmpeg.run(stream, overwrite_output=True)

        final_path = "final_recap.mp4"

        if use_subtitle and srt_path:
            with st.spinner("📝 Subtitle Overlay — လုပ်နေသည်..."):
                overlay_subtitle_on_video(
                    video_path=temp_video,
                    srt_path=srt_path,
                    output_path=final_path,
                    font_path=FONT_FILE,
                    font_size=sub_font_size,
                    position=sub_position,
                    blur_height=blur_height,
                    blur_alpha=blur_alpha
                )
                st.success("✅ Subtitle — Overlay ပြီး")
        else:
            shutil.copy(temp_video, final_path)

    st.success("✅ ပြီးပါပြီ!")
    st.video(final_path)

    f = open(final_path, "rb")
    st.download_button("📥 Recap Video Download", f, file_name="final_recap.mp4")
    f.close()
