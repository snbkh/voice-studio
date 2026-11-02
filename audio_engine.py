"""
Smart Audio Processing Engine for Termux Voice Studio
Powered by RNNoise Neural Network, SpeechNorm Auto-Leveler, Audio Gate & Native FFmpeg DSP
Anti-Hiss Calibrated (Lowpass 9kHz + Agate Noise Gate + Vocal Expansion)
Zero external pip dependencies required
"""

import os
import subprocess
import json
import time
import re

import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
DEFAULT_RNNOISE_MODEL = os.path.join(MODELS_DIR, "bd.rnnn")
if not os.path.exists(DEFAULT_RNNOISE_MODEL):
    for alt in [
        os.path.join(os.path.dirname(BASE_DIR), "models", "bd.rnnn"),
        os.path.join(os.getcwd(), "models", "bd.rnnn"),
        os.path.join(os.getcwd(), "api", "models", "bd.rnnn")
    ]:
        if os.path.exists(alt):
            DEFAULT_RNNOISE_MODEL = alt
            break


def get_ffmpeg_binary():
    sys_bin = shutil.which("ffmpeg")
    if sys_bin:
        return sys_bin
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

def get_ffprobe_binary():
    sys_bin = shutil.which("ffprobe")
    if sys_bin:
        return sys_bin
    return "ffprobe"

def analyze_audio(file_path):
    """
    Smart Acoustic Analyzer:
    Detects format, duration, volume levels, and dynamic range.
    """
    info = {
        "duration": 0.0,
        "duration_str": "0s",
        "size_mb": 0.0,
        "channels": 2,
        "sample_rate": "44100",
        "codec": "unknown",
        "mean_volume": -20.0,
        "max_volume": -1.0,
        "smart_silence_threshold": "-42dB",
        "has_video": False
    }

    probe_cmd = [
        get_ffprobe_binary(),
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]
    try:
        res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res.stdout)
        format_info = data.get("format", {})
        duration = float(format_info.get("duration", 0.0))
        size_bytes = int(format_info.get("size", 0))

        audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
        info["has_video"] = any(s.get("codec_type") == "video" for s in data.get("streams", []))
        
        info["duration"] = duration
        info["duration_str"] = f"{int(duration // 60)}m {int(duration % 60)}s" if duration > 0 else "0s"
        info["size_mb"] = round(size_bytes / (1024 * 1024), 2)
        
        if audio_stream:
            info["channels"] = audio_stream.get("channels", 2)
            info["sample_rate"] = audio_stream.get("sample_rate", "44100")
            info["codec"] = audio_stream.get("codec_name", "unknown")
    except Exception:
        pass

    try:
        vol_cmd = [
            get_ffmpeg_binary(), "-i", file_path,
            "-af", "volumedetect",
            "-vn", "-sn", "-dn",
            "-f", "null", "/dev/null"
        ]
        vol_res = subprocess.run(vol_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        vol_out = vol_res.stderr

        mean_match = re.search(r"mean_volume:\s*(-?[\d\.]+)\s*dB", vol_out)
        max_match = re.search(r"max_volume:\s*(-?[\d\.]+)\s*dB", vol_out)

        if mean_match:
            info["mean_volume"] = float(mean_match.group(1))
        if max_match:
            info["max_volume"] = float(max_match.group(1))

        # Calibrated real-world silence threshold: 13 dB below mean speech volume
        # Clamped between -38dB (soft/quiet room) and -28dB (ambient room/fan noise)
        smart_db = min(-28.0, max(-38.0, info["mean_volume"] - 13.0))
        info["smart_silence_threshold"] = f"{round(smart_db)}dB"

    except Exception:
        pass

    return info

def get_audio_info(file_path):
    return analyze_audio(file_path)

def build_filter_chain(options: dict, audio_info: dict) -> str:
    """
    Constructs an intelligent FFmpeg audio filter chain:
    1. Bandpass Clean: Highpass (75Hz rumble cut) + Lowpass (9000Hz air hiss cut)
    2. Smart Denoising (RNNoise AI with strict aformat or Adaptive Fast FFT)
    3. Noise Gate (agate: silences background noise floor during pauses)
    4. SpeechNorm Auto-Leveler with noise floor threshold (never amplifies room hiss)
    5. Studio Warmth & Articulation EQ
    6. Conversational Silence Cut
    7. Broadcast Loudness Normalization (-16 LUFS)
    """
    filters = []

    # 1. Clean bandpass (75 Hz rumble cut + 9000 Hz electronic hiss cut)
    filters.append("highpass=f=75")
    filters.append("lowpass=f=9000")

    # 2. Smart Denoising
    if options.get("denoise", False):
        engine = options.get("denoise_engine", "spectral")
        strength = options.get("denoise_strength", "medium")
        has_rnnoise = os.path.exists(DEFAULT_RNNOISE_MODEL)

        if engine == "rnnoise" and has_rnnoise:
            # Strict aformat prevents RNNoise SIGABRT (code 6) on non-48kHz inputs
            mix = "0.80" if strength == "light" else ("0.96" if strength == "strong" else "0.90")
            filters.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
            filters.append(f"arnndn=m='{DEFAULT_RNNOISE_MODEL}':mix={mix}")
        else:
            # Adaptive Fast FFT
            if strength == "light":
                filters.append("afftdn=nf=-36:nr=6:tn=1:om=o")
            elif strength == "strong":
                filters.append("afftdn=nf=-26:nr=12:tn=1:om=o")
            else: # natural medium default
                filters.append("afftdn=nf=-32:nr=8:tn=1:om=o")

    # 3. Audio Noise Gate (Mutes empty background air/hiss between speech)
    filters.append("agate=threshold=0.02:ratio=2.5:attack=15:release=200:range=0.04")

    # 4. SpeechNorm (Auto-Leveler: Lifts dipped voice while ignoring noise floor t=0.03)
    if options.get("speech_leveler", True):
        filters.append("speechnorm=e=3.5:r=0.005:p=0.95:t=0.03")

    # 5. Natural Voice Polish (Warmth & Articulation without harsh treble sizzle)
    if options.get("enhance", False):
        filters.append("bass=g=1.2:f=180:w=0.6")
        filters.append("treble=g=1.2:f=3200:w=0.6")
        filters.append("compand=attacks=0.04:decays=0.3:points=-50/-50|-20/-16|0/-3:soft-knee=6:gain=0")

    # 6. Smart Conversational Silence Removal
    if options.get("remove_silence", False):
        threshold = options.get("silence_threshold")
        if not threshold or threshold == "auto":
            threshold = audio_info.get("smart_silence_threshold", "-34dB")
            
        duration = float(options.get("silence_duration", 0.5))
        
        silence_filter = (
            f"silenceremove=start_periods=1:start_duration=0.15:start_threshold={threshold}"
            f":stop_periods=-1:stop_duration={duration}:stop_threshold={threshold}"
        )
        filters.append(silence_filter)

    # 7. Broadcast Loudness Normalization (EBU R128)
    if options.get("normalize", True):
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    return ",".join(filters)

def process_audio(input_path: str, output_path: str, options: dict) -> dict:
    """
    Executes single-pass intelligent FFmpeg processing.
    """
    start_time = time.time()
    
    input_info = analyze_audio(input_path)
    filter_chain = build_filter_chain(options, input_info)
    
    ext = os.path.splitext(output_path)[1].lower()
    cmd = [get_ffmpeg_binary(), "-y", "-err_detect", "ignore_err", "-i", input_path, "-af", filter_chain]
    
    if ext == ".mp3":
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k", "-q:a", "2"])
    elif ext == ".wav":
        cmd.extend(["-c:a", "pcm_s16le", "-ar", "44100"])
    elif ext in [".m4a", ".aac"]:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k"])

    cmd.append(output_path)
    
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed (code {process.returncode}):\n{process.stderr}")

    elapsed = round(time.time() - start_time, 2)
    output_info = analyze_audio(output_path)
    
    duration_reduced = 0.0
    if input_info["duration"] > 0 and output_info["duration"] > 0:
        duration_reduced = round(input_info["duration"] - output_info["duration"], 2)

    return {
        "success": True,
        "elapsed_seconds": elapsed,
        "input_info": input_info,
        "output_info": output_info,
        "duration_reduced_seconds": max(0.0, duration_reduced),
        "smart_threshold_used": input_info.get("smart_silence_threshold", "-42dB"),
        "filter_chain_used": filter_chain
    }
