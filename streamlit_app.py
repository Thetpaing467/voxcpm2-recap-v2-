# ============================================================
# 🇲🇲 Myanmar TTS Recap Studio
# TikTok Neon Border + Moving Arrow
# Full Streamlit App
# ============================================================

# ============================================================
# 1. INSTALL
# ============================================================

import subprocess
import sys
import os
import io
import re
import math
import time
import json
import uuid
import shutil
import asyncio
import tempfile
from pathlib import Path

def install_package(package):
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", package]
        )
    except Exception:
        pass

# Required packages
for pkg in [
    "streamlit",
    "Pillow",
    "numpy",
    "pydub",
    "edge-tts",
    "openai-whisper",
    "gradio_client",
    "ffmpeg-python",
]:
    install_package(pkg)

# ============================================================
# 2. IMPORTS
# ============================================================

import streamlit as st
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from pydub import AudioSegment

# ============================================================
# 3. FFMPEG
# ============================================================

def ensure_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return

    try:
        subprocess.run(
            ["apt-get", "update", "-qq"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            ["apt-get", "install", "-y", "-qq", "ffmpeg"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass

ensure_ffmpeg()

# ============================================================
# 4. PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Myanmar TTS Recap Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# 5. CSS
# ============================================================

st.markdown(
    """
<style>

.stApp {
    background:
        radial-gradient(circle at 20% 10%, #15152b 0%, transparent 35%),
        radial-gradient(circle at 80% 90%, #111b30 0%, transparent 35%),
        #080810;
}

.main-title {
    text-align:center;
    font-size:42px;
    font-weight:900;
    margin-top:10px;
    margin-bottom:4px;
    background:linear-gradient(
        90deg,
        #00f5ff,
        #7a5cff,
        #ff00d4,
        #00f5ff
    );
    -webkit-background-clip:text;
    -webkit-text-fill-color:transparent;
}

.sub-title {
    text-align:center;
    color:#9da3b8;
    font-size:15px;
    margin-bottom:25px;
}

.section {
    border:1px solid rgba(255,255,255,0.08);
    border-radius:18px;
    padding:20px;
    background:rgba(255,255,255,0.025);
    margin-bottom:18px;
}

.neon-info {
    border:1px solid rgba(0,245,255,0.35);
    box-shadow:
        0 0 12px rgba(0,245,255,0.12),
        inset 0 0 12px rgba(0,245,255,0.03);
    border-radius:15px;
    padding:15px;
}

.small {
    color:#9298aa;
    font-size:13px;
}

.stButton > button {
    border-radius:12px;
    font-weight:700;
}

</style>
""",
    unsafe_allow_html=True,
)

# ============================================================
# 6. PASSWORD
# ============================================================

PASSWORD = "voxcpm2026"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:

    st.markdown(
        '<div class="main-title">🎬 Myanmar TTS Recap Studio</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="sub-title">Private Movie Recap Generator</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns([1, 2, 1])

    with c2:
        password = st.text_input(
            "🔐 Password",
            type="password",
            placeholder="Enter password",
        )

        if st.button(
            "🚀 ENTER",
            use_container_width=True,
        ):
            if password == PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("❌ Password မှားနေပါတယ်")

    st.stop()

# ============================================================
# 7. CONSTANTS
# ============================================================

FONT_PATH = "MyanmarPadaung.ttf"

VOXCPM_SPACE = "openbmb/VoxCPM-Demo"
BURMESE_VOX_SPACE = "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts"

DEFAULT_VOICE_FEMALE = "my-MM-NilarNeural"
DEFAULT_VOICE_MALE = "my-MM-ThihaNeural"

# ============================================================
# 8. UTILITY
# ============================================================

def run_cmd(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr[-4000:]
        )

    return result.stdout


def get_video_duration(path):
    try:
        output = run_cmd(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ]
        )

        return float(output.strip())

    except Exception:
        return 0.0


def safe_filename(name):
    name = re.sub(
        r"[^a-zA-Z0-9._-]+",
        "_",
        name,
    )
    return name


def make_temp_dir():
    path = tempfile.mkdtemp(
        prefix="mm_tts_"
    )
    return path


# ============================================================
# 9. SRT HELPERS
# ============================================================

def format_srt_time(seconds):

    seconds = max(
        0,
        float(seconds)
    )

    h = int(seconds // 3600)

    m = int(
        (seconds % 3600) // 60
    )

    s = int(
        seconds % 60
    )

    ms = int(
        round(
            (seconds - int(seconds))
            * 1000
        )
    )

    if ms >= 1000:
        ms = 0
        s += 1

    if s >= 60:
        s = 0
        m += 1

    if m >= 60:
        m = 0
        h += 1

    return (
        f"{h:02d}:{m:02d}:{s:02d},"
        f"{ms:03d}"
    )


def create_srt(items):

    lines = []

    for i, item in enumerate(
        items,
        start=1
    ):

        start = item["start"]
        end = item["end"]
        text = item["text"].strip()

        lines.append(
            str(i)
        )

        lines.append(
            f"{format_srt_time(start)} --> "
            f"{format_srt_time(end)}"
        )

        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def parse_srt(srt_text):

    blocks = re.split(
        r"\n\s*\n",
        srt_text.strip()
    )

    results = []

    for block in blocks:

        lines = block.splitlines()

        if len(lines) < 3:
            continue

        timing = lines[1]

        match = re.search(
            r"(\d+:\d+:\d+,\d+)\s*-->\s*"
            r"(\d+:\d+:\d+,\d+)",
            timing,
        )

        if not match:
            continue

        def parse_time(t):

            h, m, rest = t.split(":")
            s, ms = rest.split(",")

            return (
                int(h) * 3600
                + int(m) * 60
                + int(s)
                + int(ms) / 1000
            )

        start = parse_time(
            match.group(1)
        )

        end = parse_time(
            match.group(2)
        )

        text = "\n".join(
            lines[2:]
        ).strip()

        results.append(
            {
                "start": start,
                "end": end,
                "text": text,
            }
        )

    return results


# ============================================================
# 10. SUBTITLE PNG
# ============================================================

def create_subtitle_png(
    text,
    output_path,
    width=1080,
    font_size=55,
    text_color=(255, 255, 255, 255),
    stroke_color=(0, 0, 0, 255),
    stroke_width=5,
):

    try:
        from PIL import ImageFont

        if os.path.exists(FONT_PATH):
            font = ImageFont.truetype(
                FONT_PATH,
                font_size,
            )
        else:
            font = ImageFont.load_default()

    except Exception:
        font = ImageFont.load_default()

    dummy = Image.new(
        "RGBA",
        (width, 300),
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(dummy)

    bbox = draw.multiline_textbbox(
        (0, 0),
        text,
        font=font,
        stroke_width=stroke_width,
        spacing=10,
        align="center",
    )

    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    img = Image.new(
        "RGBA",
        (
            min(width, text_w + 80),
            text_h + 60,
        ),
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(img)

    x = (
        img.width - text_w
    ) / 2

    y = 25

    draw.multiline_text(
        (
            x,
            y,
        ),
        text,
        font=font,
        fill=text_color,
        stroke_width=stroke_width,
        stroke_fill=stroke_color,
        spacing=10,
        align="center",
    )

    img.save(
        output_path,
        "PNG",
    )

    return output_path


# ============================================================
# 11. CREATE NEON ARROW PNG
# ============================================================

def create_arrow_png(
    output_path,
    direction="right",
    size=90,
    neon_color=(0, 255, 255),
):

    """
    Creates one glowing arrowhead PNG.

    direction:
        right
        down
        left
        up

    This avoids Unicode font problems.
    """

    canvas_size = size * 2

    base = Image.new(
        "RGBA",
        (
            canvas_size,
            canvas_size,
        ),
        (0, 0, 0, 0),
    )

    # --------------------------------------------------------
    # Draw right-facing arrow
    # --------------------------------------------------------

    arrow = Image.new(
        "RGBA",
        (
            canvas_size,
            canvas_size,
        ),
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(arrow)

    cx = canvas_size // 2
    cy = canvas_size // 2

    length = int(size * 0.95)
    thickness = int(size * 0.18)
    head = int(size * 0.45)

    # Shaft
    draw.rounded_rectangle(
        (
            cx - length // 2,
            cy - thickness // 2,
            cx + length // 2,
            cy + thickness // 2,
        ),
        radius=thickness // 2,
        fill=neon_color + (255,),
    )

    # Arrow head
    draw.polygon(
        [
            (
                cx + length // 2 + head // 2,
                cy,
            ),
            (
                cx + length // 2 - head,
                cy - head,
            ),
            (
                cx + length // 2 - head // 2,
                cy,
            ),
            (
                cx + length // 2 - head,
                cy + head,
            ),
        ],
        fill=neon_color + (255,),
    )

    # --------------------------------------------------------
    # Glow
    # --------------------------------------------------------

    glow1 = arrow.filter(
        ImageFilter.GaussianBlur(
            radius=18
        )
    )

    glow2 = arrow.filter(
        ImageFilter.GaussianBlur(
            radius=8
        )
    )

    result = Image.new(
        "RGBA",
        arrow.size,
        (0, 0, 0, 0),
    )

    result.alpha_composite(
        glow1
    )

    result.alpha_composite(
        glow2
    )

    result.alpha_composite(
        arrow
    )

    # --------------------------------------------------------
    # Rotate
    # --------------------------------------------------------

    rotation = {
        "right": 0,
        "down": 90,
        "left": 180,
        "up": 270,
    }[direction]

    result = result.rotate(
        rotation,
        expand=False,
        resample=Image.Resampling.BICUBIC,
    )

    result.save(
        output_path,
        "PNG",
    )

    return output_path


# ============================================================
# 12. CREATE ALL ARROWS
# ============================================================

def create_arrow_assets(
    temp_dir,
    size=90,
    color=(0, 255, 255),
):

    arrows = {}

    for direction in [
        "right",
        "down",
        "left",
        "up",
    ]:

        path = os.path.join(
            temp_dir,
            f"arrow_{direction}.png",
        )

        create_arrow_png(
            path,
            direction,
            size,
            color,
        )

        arrows[direction] = path

    return arrows


# ============================================================
# 13. NEON BORDER
# ============================================================

def make_neon_border_video(
    input_video,
    output_video,
    speed=1.0,
    border_width=14,
    neon_color="0x00ffff",
    arrow_size=90,
):

    """
    ONE arrow continuously travels around the frame.

    Clockwise path:

        ➜ Top
          ↓ Right
        ← Bottom
          ↑ Left

    speed:
        1.0 = 4 sec / full loop
        2.0 = 2 sec / full loop
        0.5 = 8 sec / full loop
    """

    duration = get_video_duration(
        input_video
    )

    if duration <= 0:
        raise RuntimeError(
            "Video duration မရပါ"
        )

    temp_dir = make_temp_dir()

    try:

        # ----------------------------------------------------
        # Arrow PNG assets
        # ----------------------------------------------------

        arrows = create_arrow_assets(
            temp_dir,
            size=arrow_size,
            color=(0, 255, 255),
        )

        # ----------------------------------------------------
        # Speed
        # ----------------------------------------------------

        speed = max(
            0.1,
            float(speed),
        )

        loop_duration = (
            4.0 / speed
        )

        side_duration = (
            1.0 / speed
        )

        # ----------------------------------------------------
        # Border geometry
        # ----------------------------------------------------

        bw = int(border_width)

        # Arrow dimensions
        aw = int(arrow_size * 2)
        ah = int(arrow_size * 2)

        # ----------------------------------------------------
        # FFmpeg filter
        # ----------------------------------------------------

        # Border glow
        border_filter = (
            "drawbox="
            "x=0:"
            "y=0:"
            "w=iw:"
            "h=ih:"
            f"color={neon_color}@0.95:"
            f"t={bw},"
            "drawbox="
            "x=4:"
            "y=4:"
            "w=iw-8:"
            "h=ih-8:"
            f"color={neon_color}@0.35:"
            f"t={max(2, bw//3)}"
        )

        # ----------------------------------------------------
        # Arrow path
        # ----------------------------------------------------

        #
        # ffmpeg expression:
        #
        # p = mod(t, loop_duration)
        #
        # top:
        #   x moves left -> right
        #
        # right:
        #   y moves top -> bottom
        #
        # bottom:
        #   x moves right -> left
        #
        # left:
        #   y moves bottom -> top
        #

        side = side_duration
        loop = loop_duration

        # Safe inner path
        margin = max(
            bw + 5,
            10
        )

        # Input dimensions are W/H.
        #
        # The arrow is centered on the border.
        #

        # ----------------------------------------------------
        # First draw border
        # ----------------------------------------------------

        filter_complex = (
            f"[0:v]{border_filter}[base];"
        )

        # ----------------------------------------------------
        # TOP ARROW
        # ----------------------------------------------------

        filter_complex += (
            "[1:v]format=rgba[toparrow];"
            "[base][toparrow]"
            "overlay="
            f"x='{margin}+"
            f"(W-{2*margin}-{aw})*"
            f"(mod(t,{loop})/{side})':"
            f"y='{margin/2}-{ah/2}':"
            f"enable='lt(mod(t,{loop}),{side})'"
            "[v1];"
        )

        # ----------------------------------------------------
        # RIGHT ARROW
        # ----------------------------------------------------

        filter_complex += (
            "[2:v]format=rgba[rightarrow];"
            "[v1][rightarrow]"
            "overlay="
            f"x='W-{margin}-{aw/2}':"
            f"y='{margin}+"
            f"(H-{2*margin}-{ah})*"
            f"((mod(t,{loop})-{side})/{side})':"
            f"enable='between(mod(t,{loop}),{side},{2*side})'"
            "[v2];"
        )

        # ----------------------------------------------------
        # BOTTOM ARROW
        # ----------------------------------------------------

        filter_complex += (
            "[3:v]format=rgba[bottomarrow];"
            "[v2][bottomarrow]"
            "overlay="
            f"x='{margin}+"
            f"(W-{2*margin}-{aw})*"
            f"(1-((mod(t,{loop})-{2*side})/{side}))':"
            f"y='H-{margin}-{ah/2}':"
            f"enable='between(mod(t,{loop}),{2*side},{3*side})'"
            "[v3];"
        )

        # ----------------------------------------------------
        # LEFT ARROW
        # ----------------------------------------------------

        filter_complex += (
            "[4:v]format=rgba[leftarrow];"
            "[v3][leftarrow]"
            "overlay="
            f"x='{margin/2}-{aw/2}':"
            f"y='{margin}+"
            f"(H-{2*margin}-{ah})*"
            f"(1-((mod(t,{loop})-{3*side})/{side}))':"
            f"enable='gte(mod(t,{loop}),{3*side})'"
            "[v]"
        )

        # ----------------------------------------------------
        # FFmpeg command
        # ----------------------------------------------------

        cmd = [
            "ffmpeg",
            "-y",

            "-i",
            input_video,

            "-loop",
            "1",
            "-i",
            arrows["right"],

            "-loop",
            "1",
            "-i",
            arrows["down"],

            "-loop",
            "1",
            "-i",
            arrows["left"],

            "-loop",
            "1",
            "-i",
            arrows["up"],

            "-filter_complex",
            filter_complex,

            "-map",
            "[v]",

            "-map",
            "0:a?",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "20",

            "-pix_fmt",
            "yuv420p",

            "-c:a",
            "aac",

            "-b:a",
            "192k",

            "-shortest",

            output_video,
        ]

        run_cmd(cmd)

        return output_video

    finally:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )


# ============================================================
# 14. MIRROR / CROP
# ============================================================

def process_video_for_tiktok(
    input_video,
    output_video,
    crop_ratio=0.95,
    mirror=False,
):

    filters = []

    # --------------------------------------------------------
    # Crop slightly
    # --------------------------------------------------------

    filters.append(
        f"crop="
        f"iw*{crop_ratio}:"
        f"ih*{crop_ratio}:"
        f"(iw-iw*{crop_ratio})/2:"
        f"(ih-ih*{crop_ratio})/2"
    )

    # --------------------------------------------------------
    # Mirror
    # --------------------------------------------------------

    if mirror:
        filters.append(
            "hflip"
        )

    vf = ",".join(filters)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_video,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        output_video,
    ]

    run_cmd(cmd)

    return output_video


# ============================================================
# 15. AUDIO NORMALIZATION
# ============================================================

def normalize_audio(
    input_audio,
    output_audio,
):

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_audio,
        "-af",
        (
            "loudnorm="
            "I=-16:"
            "TP=-1.5:"
            "LRA=11"
        ),
        "-ar",
        "48000",
        "-ac",
        "2",
        output_audio,
    ]

    run_cmd(cmd)

    return output_audio


# ============================================================
# 16. MUTE ORIGINAL VIDEO
# ============================================================

def mute_video(
    input_video,
    output_video,
):

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_video,
        "-c:v",
        "copy",
        "-an",
        output_video,
    ]

    run_cmd(cmd)

    return output_video


# ============================================================
# 17. MERGE VOICEOVER
# ============================================================

def merge_voiceover(
    video,
    audio,
    output,
):

    cmd = [
        "ffmpeg",
        "-y",

        "-i",
        video,

        "-i",
        audio,

        "-map",
        "0:v:0",

        "-map",
        "1:a:0",

        "-c:v",
        "copy",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-shortest",

        output,
    ]

    run_cmd(cmd)

    return output


# ============================================================
# 18. EDGE TTS
# ============================================================

async def edge_tts_generate(
    text,
    output_file,
    voice,
    rate="+0%",
):

    import edge_tts

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
    )

    await communicate.save(
        output_file
    )


def generate_edge_tts(
    text,
    output_file,
    voice=DEFAULT_VOICE_MALE,
    rate="+0%",
):

    asyncio.run(
        edge_tts_generate(
            text,
            output_file,
            voice,
            rate,
        )
    )

    return output_file


# ============================================================
# 19. TTS SPEED
# ============================================================

def change_audio_speed(
    input_audio,
    output_audio,
    speed=1.3,
):

    speed = float(speed)

    # FFmpeg atempo max/min each filter
    # is approximately 0.5 - 2.0.
    #
    # For 1.3 we can use one filter.

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_audio,
        "-filter:a",
        f"atempo={speed}",
        output_audio,
    ]

    run_cmd(cmd)

    return output_audio


# ============================================================
# 20. WHISPER
# ============================================================

@st.cache_resource
def load_whisper_model():

    import whisper

    return whisper.load_model(
        "tiny"
    )


def transcribe_audio(
    audio_file,
):

    model = load_whisper_model()

    result = model.transcribe(
        audio_file,
        language="my",
        task="transcribe",
        fp16=False,
    )

    segments = []

    for seg in result.get(
        "segments",
        []
    ):

        segments.append(
            {
                "start": float(
                    seg["start"]
                ),
                "end": float(
                    seg["end"]
                ),
                "text": seg["text"].strip(),
            }
        )

    return segments


# ============================================================
# 21. SILENCE CUT
# ============================================================

def remove_silence(
    input_audio,
    output_audio,
    silence_threshold="-40dB",
):

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_audio,
        "-af",
        (
            "silenceremove="
            "start_periods=1:"
            "start_duration=0.1:"
            f"start_threshold={silence_threshold}:"
            "stop_periods=1:"
            "stop_duration=0.1:"
            f"stop_threshold={silence_threshold}"
        ),
        output_audio,
    ]

    run_cmd(cmd)

    return output_audio


# ============================================================
# 22. CONCAT AUDIO
# ============================================================

def concat_audio_files(
    files,
    output,
):

    if not files:
        raise ValueError(
            "Audio files မရှိပါ"
        )

    temp_dir = make_temp_dir()

    try:

        list_file = os.path.join(
            temp_dir,
            "concat.txt",
        )

        with open(
            list_file,
            "w",
            encoding="utf-8",
        ) as f:

            for path in files:

                safe = os.path.abspath(
                    path
                ).replace(
                    "'",
                    "'\\''"
                )

                f.write(
                    f"file '{safe}'\n"
                )

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_file,
            "-c",
            "copy",
            output,
        ]

        run_cmd(cmd)

        return output

    finally:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )


# ============================================================
# 23. SUBTITLE OVERLAY
# ============================================================

def burn_subtitles(
    input_video,
    srt_file,
    output_video,
    font_size=24,
):

    # Use subtitles filter.
    #
    # If Myanmar font is available, use it.
    #

    font_name = "Arial"

    if os.path.exists(
        FONT_PATH
    ):
        font_name = "Myanmar Padaung"

    vf = (
        f"subtitles='{srt_file}':"
        f"force_style="
        f"'FontName={font_name},"
        f"FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"BorderStyle=1,"
        f"Outline=3,"
        f"Shadow=1,"
        f"Alignment=2,"
        f"MarginV=70'"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_video,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        output_video,
    ]

    run_cmd(cmd)

    return output_video


# ============================================================
# 24. VIDEO + SUBTITLE + AUDIO
# ============================================================

def create_final_video(
    video,
    audio,
    output,
):

    cmd = [
        "ffmpeg",
        "-y",

        "-i",
        video,

        "-i",
        audio,

        "-map",
        "0:v:0",

        "-map",
        "1:a:0",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "20",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-shortest",

        output,
    ]

    run_cmd(cmd)

    return output


# ============================================================
# 25. UI HEADER
# ============================================================

st.markdown(
    '<div class="main-title">🎬 Myanmar TTS Recap Studio</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="sub-title">'
    'Movie Recap • Burmese TTS • Subtitle • TikTok Neon Border'
    '</div>',
    unsafe_allow_html=True,
)

# ============================================================
# 26. SESSION STATE
# ============================================================

if "work_dir" not in st.session_state:
    st.session_state.work_dir = make_temp_dir()

WORK_DIR = st.session_state.work_dir

# ============================================================
# 27. STEP 1 — SCRIPT
# ============================================================

st.markdown(
    "## 📝 Step 1 — Burmese Script"
)

script_text = st.text_area(
    "Burmese Recap Script",
    height=260,
    placeholder=(
        "ဒီနေရာမှာ Burmese recap script "
        "ထည့်ပါ..."
    ),
)

# ============================================================
# 28. STEP 2 — VIDEO
# ============================================================

st.markdown(
    "## 🎬 Step 2 — Upload Video"
)

uploaded_video = st.file_uploader(
    "Upload MP4 / MOV / MKV",
    type=[
        "mp4",
        "mov",
        "mkv",
        "webm",
    ],
)

video_path = None

if uploaded_video:

    video_path = os.path.join(
        WORK_DIR,
        safe_filename(
            uploaded_video.name
        ),
    )

    with open(
        video_path,
        "wb",
    ) as f:

        f.write(
            uploaded_video.getbuffer()
        )

    st.success(
        f"✅ Uploaded: {uploaded_video.name}"
    )

    duration = get_video_duration(
        video_path
    )

    if duration:
        st.caption(
            f"Duration: {duration:.2f} sec"
        )

# ============================================================
# 29. STEP 3 — VOICE
# ============================================================

st.markdown(
    "## 🎙️ Step 3 — Burmese Voice"
)

voice_engine = st.selectbox(
    "Voice Engine",
    [
        "Edge TTS",
        "VoxCPM",
    ],
)

if voice_engine == "Edge TTS":

    voice_gender = st.radio(
        "Voice",
        [
            "Male — Thiha",
            "Female — Nilar",
        ],
        horizontal=True,
    )

    if voice_gender.startswith(
        "Male"
    ):
        selected_voice = (
            DEFAULT_VOICE_MALE
        )
    else:
        selected_voice = (
            DEFAULT_VOICE_FEMALE
        )

else:

    st.info(
        "VoxCPM model/API ကို "
        "သင့် environment အလိုက် configure လုပ်ပါ။"
    )

    selected_voice = (
        DEFAULT_VOICE_MALE
    )

tts_speed = st.slider(
    "🎚️ Voice Speed",
    min_value=0.8,
    max_value=2.0,
    value=1.3,
    step=0.05,
)

# ============================================================
# 30. STEP 4 — MIRROR / CROP
# ============================================================

st.markdown(
    "## 📱 Step 4 — TikTok Video"
)

c1, c2 = st.columns(2)

with c1:

    mirror_video = st.checkbox(
        "↔️ Mirror Video",
        value=False,
    )

with c2:

    crop_ratio = st.slider(
        "Crop Ratio",
        min_value=0.90,
        max_value=1.0,
        value=0.95,
        step=0.01,
    )

# ============================================================
# 31. STEP 5 — NEON BORDER
# ============================================================

st.markdown(
    "## ⚡ Step 5 — TikTok Neon Border"
)

enable_neon = st.checkbox(
    "Enable Neon Border",
    value=True,
)

if enable_neon:

    neon_speed = st.slider(
        "🏹 Arrow Speed",
        min_value=0.25,
        max_value=4.0,
        value=1.0,
        step=0.25,
        help=(
            "1.0 = 4 seconds per full loop. "
            "2.0 = 2 seconds. "
            "0.5 = 8 seconds."
        ),
    )

    border_width = st.slider(
        "Neon Border Width",
        min_value=5,
        max_value=30,
        value=14,
        step=1,
    )

    arrow_size = st.slider(
        "Arrow Size",
        min_value=40,
        max_value=120,
        value=70,
        step=5,
    )

    st.markdown(
        f"""
<div class="neon-info">

<b>🏹 Arrow Path</b><br><br>

➜ TOP — Left → Right<br>
↓ RIGHT — Top → Bottom<br>
← BOTTOM — Right → Left<br>
↑ LEFT — Bottom → Top<br><br>

<b>Speed:</b> {neon_speed}x<br>
<b>Full loop:</b> {4/neon_speed:.2f} seconds

</div>
""",
        unsafe_allow_html=True,
    )

# ============================================================
# 32. STEP 6 — SUBTITLE
# ============================================================

st.markdown(
    "## 💬 Step 6 — Subtitle"
)

enable_subtitle = st.checkbox(
    "Generate Burmese Subtitle",
    value=True,
)

subtitle_font_size = st.slider(
    "Subtitle Font Size",
    min_value=18,
    max_value=60,
    value=28,
    step=2,
)

# ============================================================
# 33. STEP 7 — GENERATE
# ============================================================

st.markdown(
    "## 🚀 Step 7 — Generate"
)

generate = st.button(
    "🔥 GENERATE FINAL VIDEO",
    type="primary",
    use_container_width=True,
)

# ============================================================
# 34. GENERATION
# ============================================================

if generate:

    if not script_text.strip():
        st.error(
            "❌ Burmese script ထည့်ပါ"
        )
        st.stop()

    if not video_path:
        st.error(
            "❌ Video upload လုပ်ပါ"
        )
        st.stop()

    progress = st.progress(
        0
    )

    status = st.empty()

    try:

        # ----------------------------------------------------
        # FILE PATHS
        # ----------------------------------------------------

        processed_video = os.path.join(
            WORK_DIR,
            "01_processed.mp4",
        )

        muted_video = os.path.join(
            WORK_DIR,
            "02_muted.mp4",
        )

        border_video = os.path.join(
            WORK_DIR,
            "03_neon.mp4",
        )

        voice_raw = os.path.join(
            WORK_DIR,
            "04_voice_raw.mp3",
        )

        voice_fast = os.path.join(
            WORK_DIR,
            "05_voice_fast.mp3",
        )

        voice_clean = os.path.join(
            WORK_DIR,
            "06_voice_clean.mp3",
        )

        final_video = os.path.join(
            WORK_DIR,
            "Myanmar_Recap_Final.mp4",
        )

        srt_file = os.path.join(
            WORK_DIR,
            "Myanmar_Recap.srt",
        )

        # ----------------------------------------------------
        # 1. VIDEO PROCESS
        # ----------------------------------------------------

        status.write(
            "🎬 Video ကို prepare လုပ်နေပါတယ်..."
        )

        process_video_for_tiktok(
            video_path,
            processed_video,
            crop_ratio,
            mirror_video,
        )

        progress.progress(
            15
        )

        # ----------------------------------------------------
        # 2. MUTE ORIGINAL
        # ----------------------------------------------------

        status.write(
            "🔇 Original audio ကို mute လုပ်နေပါတယ်..."
        )

        mute_video(
            processed_video,
            muted_video,
        )

        progress.progress(
            25
        )

        # ----------------------------------------------------
        # 3. NEON BORDER
        # ----------------------------------------------------

        if enable_neon:

            status.write(
                "⚡ Neon border + moving arrow..."
            )

            make_neon_border_video(
                muted_video,
                border_video,
                speed=neon_speed,
                border_width=border_width,
                arrow_size=arrow_size,
            )

        else:

            shutil.copy2(
                muted_video,
                border_video,
            )

        progress.progress(
            45
        )

        # ----------------------------------------------------
        # 4. TTS
        # ----------------------------------------------------

        status.write(
            "🎙️ Burmese voice generate လုပ်နေပါတယ်..."
        )

        if voice_engine == "Edge TTS":

            generate_edge_tts(
                script_text.strip(),
                voice_raw,
                selected_voice,
                rate="+0%",
            )

        else:

            st.warning(
                "VoxCPM integration မထည့်ရသေးတဲ့အတွက် "
                "Edge TTS ကို fallback အဖြစ်သုံးနေပါတယ်။"
            )

            generate_edge_tts(
                script_text.strip(),
                voice_raw,
                DEFAULT_VOICE_MALE,
                rate="+0%",
            )

        progress.progress(
            60
        )

        # ----------------------------------------------------
        # 5. SPEED
        # ----------------------------------------------------

        status.write(
            f"🎚️ Voice {tts_speed}x speed..."
        )

        change_audio_speed(
            voice_raw,
            voice_fast,
            tts_speed,
        )

        progress.progress(
            70
        )

        # ----------------------------------------------------
        # 6. REMOVE SILENCE
        # ----------------------------------------------------

        status.write(
            "✂️ Silence တွေကို clean လုပ်နေပါတယ်..."
        )

        try:

            remove_silence(
                voice_fast,
                voice_clean,
            )

        except Exception:

            shutil.copy2(
                voice_fast,
                voice_clean,
            )

        progress.progress(
            78
        )

        # ----------------------------------------------------
        # 7. SUBTITLE
        # ----------------------------------------------------

        subtitle_video = border_video

        if enable_subtitle:

            status.write(
                "💬 Burmese subtitle generate လုပ်နေပါတယ်..."
            )

            # Whisper can transcribe generated voice
            segments = transcribe_audio(
                voice_clean
            )

            if segments:

                srt_text = create_srt(
                    segments
                )

                with open(
                    srt_file,
                    "w",
                    encoding="utf-8",
                ) as f:

                    f.write(
                        srt_text
                    )

                subtitle_video = os.path.join(
                    WORK_DIR,
                    "07_subtitle.mp4",
                )

                try:

                    burn_subtitles(
                        border_video,
                        srt_file,
                        subtitle_video,
                        subtitle_font_size,
                    )

                except Exception as e:

                    st.warning(
                        "Subtitle burn မအောင်မြင်ပါ။ "
                        "Subtitle မပါဘဲ ဆက်လုပ်ပါမယ်။"
                    )

                    subtitle_video = border_video

            else:

                st.warning(
                    "Whisper subtitle မထုတ်နိုင်ပါ။"
                )

        progress.progress(
            88
        )

        # ----------------------------------------------------
        # 8. FINAL MERGE
        # ----------------------------------------------------

        status.write(
            "🎬 Final MP4 ပြုလုပ်နေပါတယ်..."
        )

        create_final_video(
            subtitle_video,
            voice_clean,
            final_video,
        )

        progress.progress(
            100
        )

        status.success(
            "✅ Final video ready!"
        )

        # ----------------------------------------------------
        # 9. PREVIEW
        # ----------------------------------------------------

        st.markdown(
            "## 🎥 Preview"
        )

        st.video(
            final_video
        )

        # ----------------------------------------------------
        # 10. DOWNLOAD
        # ----------------------------------------------------

        with open(
            final_video,
            "rb",
        ) as f:

            video_bytes = f.read()

        st.download_button(
            "⬇️ Download Final MP4",
            data=video_bytes,
            file_name=(
                "Myanmar_Recap_Final.mp4"
            ),
            mime="video/mp4",
            use_container_width=True,
        )

        # ----------------------------------------------------
        # 11. SRT DOWNLOAD
        # ----------------------------------------------------

        if os.path.exists(
            srt_file
        ):

            with open(
                srt_file,
                "rb",
            ) as f:

                srt_bytes = f.read()

            st.download_button(
                "⬇️ Download SRT",
                data=srt_bytes,
                file_name=(
                    "Myanmar_Recap.srt"
                ),
                mime="application/x-subrip",
                use_container_width=True,
            )

    except Exception as e:

        progress.progress(
            0
        )

        status.error(
            "❌ Generation failed"
        )

        st.exception(e)

# ============================================================
# 35. FOOTER
# ============================================================

st.markdown(
    """
<hr>

<div style="
text-align:center;
color:#656b7a;
font-size:12px;
padding:15px;
">

Myanmar TTS Recap Studio<br>
🎬 Movie Recap • 🇲🇲 Burmese Voice • ⚡ Neon Border

</div>
""",
    unsafe_allow_html=True,
)