import streamlit as st
import os
import re
import time
import ffmpeg
import shutil
import subprocess
import cv2
from PIL import Image, ImageDraw, ImageFont
from gradio_client import Client, handle_file

# ============================================================
# Config
# ============================================================
VOXCPM_SPACES = [
    {"name": "Primary — VoxCPM Demo", "space": "openbmb/VoxCPM-Demo", "type": "demo"},
    {"name": "Fallback — Burmese TTS", "space": "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts", "type": "burmese"},
]

PASSWORD = "voxcpm2026"
FONT_FILE = "pyidaungsu.ttf"
MYANMAR_FONT = "Pyidaungsu"

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
# Helper — Video Info
# ============================================================
def get_video_info(video_path):
    """Video — Width + Height + Duration"""
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


# ============================================================
# 🆕 Preview — Video Frame + Blur Box + Text
# ============================================================
def generate_preview(video_path, font_path, position="bottom",
                      blur_height=150, font_size=40,
                      preview_text="စာတန်းထိုး Preview"):
    """Preview — First Frame + Blur Box + Text"""
    W, H, _ = get_video_info(video_path)

    cap = cv2.VideoCapture(video_path)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        return None

    # Frame → PIL
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(frame_rgb).convert("RGBA")

    # Blur Box Y Position
    if position == "bottom":
        box_y = H - blur_height
    elif position == "center":
        box_y = (H - blur_height) // 2
    else:  # top
        box_y = 0

    # Overlay — Black 50%
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, box_y, W, box_y + blur_height], fill=(0, 0, 0, 128))

    # Composite
    img = Image.alpha_composite(img, overlay)

    # Font
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    draw = ImageDraw.Draw(img)

    # Text — Center of Blur Box
    bbox = draw.textbbox((0, 0), preview_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    text_x = (W - text_w) // 2
    text_y = box_y + (blur_height - text_h) // 2

    # Outline
    for dx in [-2, -1, 0, 1, 2]:
        for dy in [-2, -1, 0, 1, 2]:
            draw.text((text_x + dx, text_y + dy), preview_text,
                      font=font, fill=(0, 0, 0, 255))
    draw.text((text_x, text_y), preview_text, font=font, fill=(255, 255, 255, 255))

    # Save — Resize for Streamlit
    preview_path = "preview.png"
    preview_w = 720
    preview_h = int(H * (preview_w / W))
    img_resized = img.resize((preview_w, preview_h), Image.LANCZOS)
    img_resized.convert("RGB").save(preview_path)
    return preview_path


# ============================================================
# Script → SRT
# ============================================================
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


# ============================================================
# TTS — Split Script
# ============================================================
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


# ============================================================
# TTS — Demo / Burmese
# ============================================================
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
# 🆕 FFmpeg — Burn Subtitle + Blur Box
# ============================================================
def burn_subtitle_with_blur(video_path, srt_path, output_path,
                              font_name=MYANMAR_FONT, font_size=24,
                              position="bottom", blur_height=150):
    """Subtitle + Blur Box — FFmpeg"""
    W, H, _ = get_video_info(video_path)

    # Box Y Position
    box_y = {
        "bottom": H - blur_height,
        "center": (H - blur_height) // 2,
        "top": 0
    }[position]

    # Subtitle Alignment
    alignment = {"bottom": 2, "center": 5, "top": 8}[position]
    margin_v = {"bottom": 30, "center": 0, "top": 30}[position]

    style = (
        f"FontName={font_name},"
        f"FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"BorderStyle=1,Outline=2,Shadow=1,"
        f"Alignment={alignment},MarginV={margin_v}"
    )

    font_dir = os.getcwd()
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    font_dir_escaped = font_dir.replace("\\", "/").replace(":", "\\:")

    # Filter Complex — Blur Box + Subtitle
    filter_complex = (
        f"color=c=black@0.5:s={W}x{blur_height}:d=999999[box];"
        f"[0:v][box]overlay=0:{box_y}:shortest=1[blurred];"
        f"[blurred]subtitles='{srt_escaped}':force_style='{style}':fontsdir='{font_dir_escaped}'[outv]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "medium",
        "-c:a", "copy",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        raise Exception(f"FFmpeg error:\n{(result.stderr or '')[-1000:]}")

    return output_path


# ============================================================
# UI
# ============================================================
st.set_page_config(page_title="🎬 VoxCPM2 Movie Recap", page_icon="🎬")
st.title("🎬 VoxCPM2 Movie Recap")
st.write("Gemini Web မှ Script ရယူပြီး VoxCPM2 အသံနဲ့ Recap ဖန်တီးပါ")

# Font Status
if os.path.exists(FONT_FILE):
    st.sidebar.success(f"✅ Font: {FONT_FILE}")
    st.sidebar.caption(f"FontName: {MYANMAR_FONT}")
else:
    st.sidebar.warning(f"⚠️ {FONT_FILE} — မရှိ")

# Step 1: Gemini Web
st.header("📝 Step 1: Gemini Web → Script")
st.info("**Gemini Web:** [gemini.google.com](https://gemini.google.com) → Video Upload → Prompt → Script Copy")

with st.expander("📋 Prompt (Copy → Gemini Web)", expanded=True):
    st.code(
        "Watch this video carefully and write a clear, continuous movie recap script "
        "in Myanmar language for audio narration that matches the length of the video. "
        "Return plain speech text only without markdown titles.",
        language="text"
    )

# Step 2: Script Paste
st.header("📝 Step 2: Script Paste")
script = st.text_area("Script", height=250, placeholder="မြန်မာ Script paste...")

# Step 3: Reference + Video
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

# ============================================================
# 🆕 Subtitle + Blur Settings
# ============================================================
st.header("📝 Subtitle + Blur Settings")

use_subtitle = st.toggle("📝 စာတန်းထိုး (Burn-in)", value=True)

if use_subtitle:
    sub_position = st.selectbox(
        "Subtitle နေရာ",
        ["bottom", "center", "top"],
        format_func=lambda x: {"bottom": "⬇️ အောက်ခြေ", "center": "⬅️ အလယ်", "top": "⬆️ အပေါ်"}[x]
    )
    sub_font_size = st.slider("Font Size", 16, 72, 24)
    blur_height = st.slider("Blur Box အမြင့်", 80, 300, 150)
else:
    sub_position = "bottom"
    sub_font_size = 24
    blur_height = 150

# ============================================================
# 🆕 Preview — Blur Box + Text
# ============================================================
if video_file is not None and use_subtitle:
    with st.spinner("🖼️ Preview — ဖန်တီးနေသည်..."):
        # Save Video — Preview
        temp_video_preview = "preview_video.mp4"
        with open(temp_video_preview, "wb") as f:
            video_file.seek(0)
            f.write(video_file.read())

        preview_path = generate_preview(
            temp_video_preview,
            font_path=FONT_FILE,
            position=sub_position,
            blur_height=blur_height,
            font_size=sub_font_size,
            preview_text="စာတန်းထိုး Preview"
        )

        if preview_path:
            st.image(preview_path, caption="🖼️ Preview — Blur Box + Subtitle", use_container_width=True)
            st.caption(f"📍 Position: {sub_position} | Font: {sub_font_size} | Blur: {blur_height}px")

# Sidebar
st.sidebar.header("🎙️ TTS Spaces")
for i, s in enumerate(VOXCPM_SPACES, 1):
    st.sidebar.write(f"**{i}.** `{s['space']}`")

# Step 4: Generate
st.header("🚀 Step 4: Generate Recap")

if st.button("✨ Generate Recap Video", type="primary"):
    if not script.strip():
        st.error("❌ Script paste — Step 2")
        st.stop()
    if video_file is None:
        st.error("❌ Video Upload — Step 3")
        st.stop()

    with st.spinner("📹 ဗီဒီယို စစ်ဆေးနေသည်..."):
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

    # Audio Speed
    tempo = audio_dur / video_duration
    tempo = max(0.5, min(2.0, tempo))
    st.write(f"⚡ Audio Speed: {tempo:.2f}x")

    # SRT
    srt_path = None
    if use_subtitle:
        with st.spinner("📝 Script → SRT..."):
            srt_path = script_to_srt(script, video_duration, "recap.srt")
            if srt_path and os.path.exists(srt_path):
                st.success("✅ SRT — ဖန်တီးပြီး")

    # Render
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
            with st.spinner("📝 Blur + Subtitle မြှုပ်ထည့်နေသည်..."):
                try:
                    burn_subtitle_with_blur(temp_video, srt_path, final_path,
                                              font_name=MYANMAR_FONT,
                                              font_size=sub_font_size,
                                              position=sub_position,
                                              blur_height=blur_height)
                    st.success("✅ Blur + Subtitle — မြှုပ်ပြီး")
                except Exception as e:
                    st.error(f"❌ Subtitle error: {e}")
                    shutil.copy(temp_video, final_path)
        else:
            shutil.copy(temp_video, final_path)

    st.success("✅ ပြီးပါပြီ!")
    st.video(final_path)

    with open(final_path, "rb") as f:
        st.download_button("📥 Recap Video Download", f, file_name="final_recap.mp4")

    if srt_path and os.path.exists(srt_path):
        with open(srt_path, "rb") as f:
            st.download_button("📥 SRT Download", f, file_name="recap.srt")

    with st.expander("📝 Script"):
        st.text(script)
