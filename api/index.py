import os
import sys
import time
import urllib.parse
from flask import Flask, request, jsonify, send_file

# Add parent directory to path so audio_engine can be imported
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

import audio_engine

# On Vercel, /tmp is the only writable filesystem
UPLOADS_DIR = "/tmp/uploads"
OUTPUTS_DIR = "/tmp/outputs"
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

FILE_RETENTION_SECONDS = 300

def purge_old_files():
    """Purge files older than 5 minutes."""
    now = time.time()
    for directory in [UPLOADS_DIR, OUTPUTS_DIR]:
        if not os.path.exists(directory):
            continue
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            try:
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > FILE_RETENTION_SECONDS:
                    os.remove(fpath)
            except Exception:
                pass

# Vercel entrypoint WSGI application
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # 25MB max

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    if not path or path == 'index.html':
        template_path = os.path.join(BASE_DIR, "templates", "index.html")
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
        return "index.html not found", 404

    # Media streaming / download
    if path.startswith("media/outputs/"):
        rel_name = urllib.parse.unquote(os.path.basename(path))
        target_path = os.path.join(OUTPUTS_DIR, rel_name)
        if not os.path.exists(target_path):
            return "File not found or expired", 404
        as_attachment = request.args.get('download') == '1'
        return send_file(target_path, mimetype="audio/mpeg", as_attachment=as_attachment, download_name=rel_name)

    return "Not Found", 404

@app.route('/api/process', methods=['POST'])
def process_audio():
    purge_old_files()

    if 'audio' not in request.files:
        return jsonify({"success": False, "error": "No audio file uploaded"}), 400

    file = request.files['audio']
    if not file.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    filename = file.filename
    raw_base, ext = os.path.splitext(filename)
    clean_base = "".join(c if (c.isalnum() or c in ".-_") else "_" for c in raw_base)
    clean_base = "_".join(filter(None, clean_base.split("_"))) or "audio"
    ext = ext.lower() or ".mp3"

    ts = int(time.time())
    input_filename = f"raw_{ts}_{clean_base}{ext}"
    input_path = os.path.join(UPLOADS_DIR, input_filename)
    file.save(input_path)

    output_filename = f"enhanced_{ts}_{clean_base}.mp3"
    output_path = os.path.join(OUTPUTS_DIR, output_filename)

    options = {
        "denoise": request.form.get("denoise", "true").lower() == "true",
        "denoise_engine": request.form.get("denoise_engine", "spectral"),
        "denoise_strength": request.form.get("denoise_strength", "medium"),
        "speech_leveler": request.form.get("speech_leveler", "true").lower() == "true",
        "enhance": request.form.get("enhance", "true").lower() == "true",
        "remove_silence": request.form.get("remove_silence", "true").lower() == "true",
        "silence_threshold": request.form.get("silence_threshold", "-34dB"),
        "silence_duration": float(request.form.get("silence_duration", 0.5)),
        "normalize": request.form.get("normalize", "true").lower() == "true",
    }

    try:
        result = audio_engine.process_audio(input_path, output_path, options)
        quoted_output = urllib.parse.quote(output_filename)
        result.update({
            "output_url": f"/media/outputs/{quoted_output}",
            "output_filename": output_filename
        })
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
