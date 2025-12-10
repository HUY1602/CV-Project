# CV-Project – Restaurant Emotion Assistant

This app detects customer emotions via webcam (OpenFace) and gives service advice via Gemini. It can read responses aloud with multiple TTS backends (edge-tts).

## Prerequisites

- Python 3.11+ (venv recommended)
- Webcam + microphone permissions in browser

## Setup

1. Create and activate venv (recommended)
   - Windows: `python -m venv venv && venv\Scripts\activate`
2. Install deps
   - `pip install -r requirements.txt`
3. Environment variables
   - Create `.env` in repo root with:
     - `GEMINI_API_KEY=your_google_gemini_key`

## Run the app

`streamlit run app.py`

## Key features

- Face ID + Emotion analysis (OpenFace) → Gemini advice
- If edge-tts fails (network/WebSocket), you can falls back to gTTS; if gTTS fails, to pyttsx3.

## Testing Gemini

- `python test_gemini.py` (requires `GEMINI_API_KEY` in `.env`).

## Common issues

- No audio or cut-off audio: ensure ffmpeg installed; edge-tts reachable; buffer tuned in `camera_auto.py` (time_to_wait uses estimated duration).
- Cannot detect camera: allow browser camera access and reload.

## Notes

- Default auto interval is set in `render_camera_auto` (15s). Force detect via the UI buttons.
