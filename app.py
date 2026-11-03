#!/usr/bin/env python3
"""
Voice Studio - Clean Smart Audio Server
Runs 100% locally on Android Termux & Ready for Vercel Deployment
Features:
- RNNoise AI & SpeechNorm Auto-Leveling
- 5-Minute Auto-Delete File Purger
- Vercel Serverless Function Compatibility
"""

import os
import sys
import json
import time
import email
import email.policy
import mimetypes
import urllib.parse
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import audio_engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Vercel uses /tmp as its only writable filesystem
IS_VERCEL = bool(os.environ.get("VERCEL"))
if IS_VERCEL:
    UPLOADS_DIR = "/tmp/uploads"
    OUTPUTS_DIR = "/tmp/outputs"
else:
    UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
    OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# Auto-cleanup files older than 5 minutes (300 seconds)
FILE_RETENTION_SECONDS = 300

def purge_old_files():
    """Removes files in uploads and outputs older than 5 minutes."""
    now = time.time()
    for directory in [UPLOADS_DIR, OUTPUTS_DIR]:
        if not os.path.exists(directory):
            continue
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            try:
                if os.path.isfile(fpath):
                    # Check age
                    mtime = os.path.getmtime(fpath)
                    if (now - mtime) > FILE_RETENTION_SECONDS:
                        os.remove(fpath)
            except Exception:
                pass

def cleanup_daemon_loop():
    """Background thread that runs file purge every 30 seconds."""
    while True:
        try:
            purge_old_files()
        except Exception:
            pass
        time.sleep(30)

# Start auto-delete daemon in background
cleanup_thread = threading.Thread(target=cleanup_daemon_loop, daemon=True)
cleanup_thread.start()

class VoiceStudioHandler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_file_with_range(self, file_path, as_attachment=False):
        """Serves file with 206 Partial Content range requests & attachment headers."""
        if not os.path.exists(file_path):
            self.send_error(404, "File not found or expired")
            return

        file_size = os.path.getsize(file_path)
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "audio/mpeg" if file_path.endswith(".mp3") else "application/octet-stream"

        filename = os.path.basename(file_path)
        range_header = self.headers.get("Range")

        if range_header and range_header.startswith("bytes="):
            try:
                ranges = range_header.replace("bytes=", "").split("-")
                start = int(ranges[0]) if ranges[0] else 0
                end = int(ranges[1]) if ranges[1] else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if as_attachment:
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.end_headers()

                with open(file_path, "rb") as f:
                    f.seek(start)
                    self.wfile.write(f.read(length))
                return
            except Exception:
                pass

        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        if as_attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()

        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        as_attachment = "download" in query

        # 1. UI Root
        if path in ["/", "/index.html"]:
            index_path = os.path.join(TEMPLATES_DIR, "index.html")
            if os.path.exists(index_path):
                with open(index_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404, "index.html not found")
            return

        # 2. Media Playback & Download
        if path.startswith("/media/outputs/"):
            rel_name = urllib.parse.unquote(os.path.basename(path))
            target_path = os.path.join(OUTPUTS_DIR, rel_name)
            self.serve_file_with_range(target_path, as_attachment=as_attachment)
            return

        if path.startswith("/media/uploads/"):
            rel_name = urllib.parse.unquote(os.path.basename(path))
            target_path = os.path.join(UPLOADS_DIR, rel_name)
            self.serve_file_with_range(target_path, as_attachment=as_attachment)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/process":
            self.handle_upload_and_process()
            return
        self.send_error(404, "Endpoint not found")

    def handle_upload_and_process(self):
        # Trigger purge of files older than 5 minutes
        purge_old_files()

        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self.send_json({"success": False, "error": "Content-Type must be multipart/form-data"}, 400)
                return

            content_length = int(self.headers.get("Content-Length", 0))
            MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB limit
            if content_length > MAX_FILE_SIZE:
                self.send_json({"success": False, "error": "File exceeds the 10 MB limit"}, 413)
                return

            body_bytes = self.rfile.read(content_length)

            header_str = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
            msg = email.message_from_bytes(header_str + body_bytes, policy=email.policy.default)

            fields = {}
            file_data = None
            filename = "audio.mp3"

            for part in msg.iter_parts():
                field_name = part.get_param("name", header="content-disposition")
                part_filename = part.get_filename()

                if part_filename:
                    filename = os.path.basename(part_filename)
                    file_data = part.get_payload(decode=True)
                elif field_name:
                    fields[field_name] = part.get_payload(decode=True).decode("utf-8", errors="ignore").strip()

            if not file_data:
                self.send_json({"success": False, "error": "No file uploaded"}, 400)
                return

            raw_base, ext = os.path.splitext(filename)
            clean_base = "".join(c if (c.isalnum() or c in ".-_") else "_" for c in raw_base)
            clean_base = "_".join(filter(None, clean_base.split("_"))) or "audio"
            ext = ext.lower() or ".mp3"

            ts = int(time.time())
            input_filename = f"raw_{ts}_{clean_base}{ext}"
            input_path = os.path.join(UPLOADS_DIR, input_filename)

            with open(input_path, "wb") as f:
                f.write(file_data)

            output_filename = f"enhanced_{ts}_{clean_base}.mp3"
            output_path = os.path.join(OUTPUTS_DIR, output_filename)

            options = {
                "denoise": fields.get("denoise", "true").lower() == "true",
                "denoise_engine": fields.get("denoise_engine", "spectral"),
                "denoise_strength": fields.get("denoise_strength", "medium"),
                "speech_leveler": fields.get("speech_leveler", "true").lower() == "true",
                "enhance": fields.get("enhance", "true").lower() == "true",
                "remove_silence": fields.get("remove_silence", "true").lower() == "true",
                "silence_threshold": fields.get("silence_threshold", "auto"),
                "silence_duration": float(fields.get("silence_duration", 0.5)),
                "normalize": fields.get("normalize", "true").lower() == "true",
            }

            result = audio_engine.process_audio(input_path, output_path, options)

            quoted_output = urllib.parse.quote(output_filename)
            quoted_input = urllib.parse.quote(input_filename)

            result.update({
                "output_url": f"/media/outputs/{quoted_output}",
                "output_filename": output_filename,
                "input_url": f"/media/uploads/{quoted_input}"
            })

            self.send_json(result)

        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, 500)

# Vercel Serverless Function entrypoint handler
handler = VoiceStudioHandler

def run_server(port=8080):
    for try_port in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("0.0.0.0", try_port), VoiceStudioHandler)
            print("\n" + "=" * 50)
            print("  VOICE STUDIO - LOCAL SERVER")
            print("=" * 50)
            print(f"  Browser URL: http://localhost:{try_port}")
            print(f"               http://127.0.0.1:{try_port}")
            print("  Auto-Purge: Files deleted after 5 mins")
            print("=" * 50 + "\n")
            server.serve_forever()
            break
        except OSError as e:
            if e.errno == 98:
                continue
            raise

if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
    run_server(p)
