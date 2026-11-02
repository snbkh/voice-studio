#!/data/data/com.termux/files/usr/bin/bash
# One-tap launcher for Voice Studio
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "============================================="
echo " Starting Voice Studio Local Web Server..."
echo "============================================="
python3 app.py
