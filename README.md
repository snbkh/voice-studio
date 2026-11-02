# 🎙️ Voice Studio (Local Termux Web App & CLI)

Voice Studio ek 100% offline, private aur high-speed audio tool hai jo aapke Termux / Android device par chalta hai.

## 🚀 Features
1. **🔇 Silence Remover**: Speech ke beech ke dead pauses aur lambe gaps ko automatically cut karta hai bina words ko disturb kiye.
2. **🧹 Noise Remover**: Fan noise, room hiss, humming aur mic static ko FFmpeg ke FFT adaptive filter se clean karta hai.
3. **✨ Voice Enhancer**: Broadcast EQ (vocal presence + warmth), studio compression aur -16 LUFS loudness normalization lagata hai.
4. **🌟 All-in-One Studio Magic**: Teenon features ko optimal order me 1-click me process karta hai.

---

## 📱 Kaise Chalayein (Local Browser)

### Step 1: Server Start Karein
Termux me is folder me jakar run karein:
```bash
cd "/storage/emulated/0/antigravity/enhance voice"
python3 app.py
```
*(Ya fir direct `./start.sh` run kar sakte hain)*

### Step 2: Browser me Kholein
Apne phone ke kisi bhi browser (Chrome, Brave, Firefox) me ye link kholein:
👉 **`http://localhost:8080`** ya **`http://127.0.0.1:8080`**

### Step 3: Use Karein
1. **Tap to Choose File**: Apna audio ya video select karein.
2. Preset choose karein (**All-in-One**, **Cut Silence**, **Denoise Only**, ya **Vocal Polish**).
3. **"Enhance Voice Now ⚡"** par tap karein.
4. Kuch hi seconds me **Before vs After** sun kar **Download** kar lein!

---

## 💻 Terminal CLI Se Direct Kaise Chalayein

Agar browser nahi kholna chahte, toh direct terminal command bhi use kar sakte hain:

```bash
# All-in-one enhance (Noise + Enhance + Silence)
python3 app.py my_voice.mp3 all

# Sirf silence cut karna ho:
python3 app.py my_voice.mp3 silence

# Sirf noise hatana ho:
python3 app.py my_voice.mp3 noise

# Sirf vocal EQ & loudness badhana ho:
python3 app.py my_voice.mp3 enhance
```

---

## 📁 Folders
- `templates/` - Mobile responsive dark UI
- `uploads/` - Aapke upload kiye raw audio files
- `outputs/` - Processed crystal clear audio files
