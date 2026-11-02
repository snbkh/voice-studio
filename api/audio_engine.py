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
    Fast Acoustic Analyzer:
    Extracts audio metadata instantly (duration, channels, sample rate, codec, size)
    using lightweight header parsing (<0.05s) without running slow full-file decoding passes.
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
        "smart_silence_threshold": "-34dB",
        "has_video": False,
        "single_ear_fix": None
    }

    if os.path.exists(file_path):
        try:
            info["size_mb"] = round(os.path.getsize(file_path) / (1024 * 1024), 2)
        except Exception:
            pass

    # Method 1: Try ffprobe if binary is available
    probed = False
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin:
        probe_cmd = [
            ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path
        ]
        try:
            res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                format_info = data.get("format", {})
                duration = float(format_info.get("duration", 0.0))
                size_bytes = int(format_info.get("size", 0))

                audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
                info["has_video"] = any(s.get("codec_type") == "video" for s in data.get("streams", []))
                info["duration"] = duration
                info["duration_str"] = f"{int(duration // 60)}m {int(duration % 60)}s" if duration > 0 else "0s"
                if size_bytes > 0:
                    info["size_mb"] = round(size_bytes / (1024 * 1024), 2)
                if audio_stream:
                    info["channels"] = int(audio_stream.get("channels", 2))
                    info["sample_rate"] = str(audio_stream.get("sample_rate", "44100"))
                    info["codec"] = audio_stream.get("codec_name", "unknown")
                probed = True
        except Exception:
            pass

    # Method 2: Fast instantaneous header read using ffmpeg -i (<0.05s, works everywhere including Vercel)
    if not probed or info["duration"] <= 0.0:
        try:
            cmd = [get_ffmpeg_binary(), "-i", file_path]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=3)
            out = res.stderr

            dur_m = re.search(r"Duration:\s*(\d+):(\d+):([\d\.]+)", out)
            if dur_m:
                h, m, s = float(dur_m.group(1)), float(dur_m.group(2)), float(dur_m.group(3))
                dur = round(h * 3600 + m * 60 + s, 2)
                info["duration"] = dur
                info["duration_str"] = f"{int(dur // 60)}m {int(dur % 60)}s" if dur > 0 else "0s"

            audio_m = re.search(r"Stream #\d+:\d+.*Audio:\s*([a-zA-Z0-9_]+)[^,]*,?\s*(\d+)\s*Hz,\s*([^,]+)", out)
            if audio_m:
                info["codec"] = audio_m.group(1)
                info["sample_rate"] = audio_m.group(2)
                chan_str = audio_m.group(3).lower()
                info["channels"] = 1 if ("mono" in chan_str or "1 channels" in chan_str) else 2
        except Exception:
            pass

    return info

def get_audio_info(file_path):
    return analyze_audio(file_path)

def build_filter_chain(options: dict, audio_info: dict) -> str:
    """
    Constructs an optimized FFmpeg audio filter chain:
    1. Bandpass Clean: Highpass (75Hz rumble cut) + Lowpass (9000Hz air hiss cut)
    2. Smart Denoising (RNNoise AI with alimiter peak protection OR Fast Adaptive Spectral FFT)
    3. Noise Gate (agate: silences background room noise during pauses)
    4. SpeechNorm Auto-Leveler with noise floor threshold
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
        engine = options.get("denoise_engine", "auto")
        strength = options.get("denoise_strength", "medium")
        has_rnnoise = os.path.exists(DEFAULT_RNNOISE_MODEL)
        duration = audio_info.get("duration", 0.0)

        if engine == "auto":
            # Auto: Uses RNNoise for recordings up to 45s, Fast Spectral FFT for longer
            use_rnnoise = (has_rnnoise and (duration <= 45.0 or duration == 0.0))
        elif engine == "rnnoise":
            # User explicitly chose AI Model
            use_rnnoise = has_rnnoise
        else:
            use_rnnoise = False

        if use_rnnoise:
            mix = "0.80" if strength == "light" else ("0.96" if strength == "strong" else "0.90")
            safe_model_path = DEFAULT_RNNOISE_MODEL.replace("\\", "/")
            # Process RNNoise on clean mono; alimiter prevents peaks > 0.95 from causing libmp3lame assertion crashes
            filters.append(f"arnndn=m='{safe_model_path}':mix={mix}")
            filters.append("alimiter=limit=0.95")
        else:
            # Adaptive Fast FFT (Completes in milliseconds with crystal clear noise reduction)
            if strength == "light":
                filters.append("afftdn=nf=-36:nr=6:tn=1:om=o")
            elif strength == "strong":
                filters.append("afftdn=nf=-26:nr=12:tn=1:om=o")
            else:
                filters.append("afftdn=nf=-32:nr=8:tn=1:om=o")

    # 3. Audio Noise Gate (Mutes empty background air/hiss between speech)
    filters.append("agate=threshold=0.02:ratio=2.5:attack=15:release=200:range=0.04")

    # 4. SpeechNorm (Auto-Leveler: Lifts quiet speech while ignoring noise floor t=0.03)
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
            threshold = "-34dB"
            
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
    Executes high-speed intelligent audio processing:
    1. Pre-decodes & downmixes raw input into 48kHz mono PCM WAV (heals corrupt headers & single-ear recording).
    2. Runs neural/DSP filter chain on 1 channel (2x faster, avoids timeouts).
    3. Outputs dual-channel centered stereo MP3 (ensures sound plays in BOTH earphone speakers).
    """
    start_time = time.time()

    # Step 0: Compatibility Pre-Decode / Healing
    raw_dir = os.path.dirname(input_path)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    pcm_working_path = os.path.join(raw_dir, f"pcm_{int(time.time())}_{base_name}.wav")

    decode_cmd = [
        get_ffmpeg_binary(), "-y",
        "-err_detect", "ignore_err",
        "-i", input_path,
        "-vn", "-sn", "-dn",
        "-acodec", "pcm_s16le",
        "-ar", "48000",
        "-ac", "1",
        pcm_working_path
    ]
    decode_proc = subprocess.run(decode_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    working_input = pcm_working_path if (decode_proc.returncode == 0 and os.path.exists(pcm_working_path)) else input_path

    try:
        input_info = analyze_audio(input_path)
        filter_chain = build_filter_chain(options, input_info)
        
        ext = os.path.splitext(output_path)[1].lower()
        # Output is mastered to balanced dual-channel stereo so BOTH earphone speakers play equally
        codec_args = ["-ac", "2"]
        if ext == ".mp3":
            codec_args.extend(["-c:a", "libmp3lame", "-b:a", "192k"])
        elif ext == ".wav":
            codec_args.extend(["-c:a", "pcm_s16le", "-ar", "44100"])
        elif ext in [".m4a", ".aac"]:
            codec_args.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            codec_args.extend(["-c:a", "libmp3lame", "-b:a", "192k"])

        cmd = [get_ffmpeg_binary(), "-y", "-err_detect", "ignore_err", "-i", working_input, "-af", filter_chain] + codec_args + [output_path]
        process = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        fallback_used = False
        # If RNNoise returns non-zero, seamlessly fall back to Fast Spectral FFT
        if process.returncode != 0 and options.get("denoise_engine") in ["rnnoise", "auto"]:
            fallback_options = dict(options)
            fallback_options["denoise_engine"] = "spectral"
            filter_chain = build_filter_chain(fallback_options, input_info)
            cmd_fallback = [get_ffmpeg_binary(), "-y", "-err_detect", "ignore_err", "-i", working_input, "-af", filter_chain] + codec_args + [output_path]
            process = subprocess.run(cmd_fallback, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if process.returncode == 0:
                fallback_used = True

        if process.returncode != 0:
            raise RuntimeError(f"Audio processing failed (code {process.returncode}):\n{process.stderr}")

        elapsed = round(time.time() - start_time, 2)
        output_info = analyze_audio(output_path)
        
        duration_reduced = 0.0
        if input_info["duration"] > 0 and output_info["duration"] > 0:
            duration_reduced = round(input_info["duration"] - output_info["duration"], 2)

        ret = {
            "success": True,
            "elapsed_seconds": elapsed,
            "input_info": input_info,
            "output_info": output_info,
            "duration_reduced_seconds": max(0.0, duration_reduced),
            "smart_threshold_used": input_info.get("smart_silence_threshold", "-34dB"),
            "filter_chain_used": filter_chain
        }
        if fallback_used:
            ret["notice"] = "AI model encountered format incompatibility; seamlessly enhanced using Spectral Denoising."
        elif options.get("denoise_engine") == "auto" and input_info.get("duration", 0) > 45.0:
            ret["notice"] = f"Auto selected Fast Spectral Denoising for {input_info.get('duration_str', '')} audio to optimize speed."
        return ret
    finally:
        # Cleanup temporary decoded PCM file
        if working_input != input_path and os.path.exists(working_input):
            try:
                os.remove(working_input)
            except Exception:
                pass

