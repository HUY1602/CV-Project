"""
Text-to-Speech service with fallback chain:
1. edge-tts (Microsoft, online, high quality)
2. gTTS (Google Translate TTS, online, good quality, no API key)
3. pyttsx3 (offline fallback, always available)
"""

import asyncio
import tempfile
import os
import streamlit as st
import edge_tts

try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False

try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False


# Cache for Vietnamese voices
_vietnamese_voices = None


async def get_vietnamese_voices():
    """Get list of Vietnamese voices."""
    global _vietnamese_voices
    if _vietnamese_voices is None:
        try:
            voices = await edge_tts.list_voices()
            # Filter Vietnamese voices
            _vietnamese_voices = [
                voice for voice in voices 
                if voice["Locale"].startswith("vi")
            ]
            # Prefer female voices (usually sound more natural)
            _vietnamese_voices.sort(key=lambda x: "Female" in x.get("Gender", ""), reverse=True)
        except Exception as e:
            st.warning(f"Không thể lấy danh sách giọng: {e}")
            _vietnamese_voices = []
    return _vietnamese_voices


async def get_best_vietnamese_voice():
    """Get the best Vietnamese voice available."""
    voices = await get_vietnamese_voices()
    if voices:
        # Prefer female voices, then by name
        return voices[0]["Name"]
    # Fallback to any Vietnamese voice
    return "vi-VN-HoaiMyNeural"  # Good quality Vietnamese female voice


def text_to_speech(text: str, lang: str = "vi", slow: bool = False) -> bool:
    """
    Convert text to speech and play using system player (blocking call).
    For Streamlit, use text_to_speech_file() instead.
    """
    audio_file = text_to_speech_file(text, lang, slow)
    if not audio_file:
        return False
    
    try:
        # Play audio using system player
        import platform
        system = platform.system()
        
        if system == "Linux":
            os.system(f"mpg123 -q {audio_file} 2>/dev/null || aplay {audio_file} 2>/dev/null || paplay {audio_file} 2>/dev/null")
        elif system == "Darwin":  # macOS
            os.system(f"afplay {audio_file}")
        elif system == "Windows":
            os.system(f'powershell -c (New-Object Media.SoundPlayer "{audio_file}").PlaySync()')
        else:
            # Fallback: try common players
            os.system(f"mpg123 -q {audio_file} 2>/dev/null || play {audio_file} 2>/dev/null")
        
        # Cleanup
        cleanup_audio_file(audio_file)
        return True
    except Exception as e:
        st.error(f"Lỗi khi phát audio: {e}")
        cleanup_audio_file(audio_file)
        return False


async def _text_to_speech_async(text: str, slow: bool = False, speed: float = 1.0) -> str:
    """Internal async function to generate TTS audio file using edge-tts."""
    try:
        # Get best Vietnamese voice
        voice = await get_best_vietnamese_voice()
        
        # Adjust rate based on slow flag and speed multiplier
        if slow:
            rate = "-20%"
        else:
            # Convert speed to percentage (+50% for 1.5x, +100% for 2.0x)
            rate_percent = int((speed - 1.0) * 100)
            if rate_percent >= 0:
                rate = f"+{rate_percent}%"
            else:
                rate = f"{rate_percent}%"
        
        # Generate TTS
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
            tmp_path = tmp_file.name
            await communicate.save(tmp_path)
        
        return tmp_path
    except Exception as e:
        # Log but don't show error yet - will try fallback
        print(f"[edge-tts failed] {e}")
        return None


def _text_to_speech_gtts(text: str, slow: bool = False, speed: float = 1.0) -> str:
    """Fallback online TTS using gTTS (Google Translate TTS)."""
    if not GTTS_AVAILABLE:
        return None
    
    try:
        # Note: gTTS does not support fine-grained speed control, only slow=True/False
        gtts_obj = gTTS(text=text, lang='vi', slow=slow, lang_check=False)
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
            tmp_path = tmp_file.name
        
        gtts_obj.save(tmp_path)
        
        # Check if file was created
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            return tmp_path
        return None
    except Exception as e:
        print(f"[gTTS failed] {e}")
        return None


def _text_to_speech_pyttsx3(text: str, slow: bool = False, speed: float = 1.0) -> str:
    """Fallback offline TTS using pyttsx3."""
    if not PYTTSX3_AVAILABLE:
        return None
    
    try:
        engine = pyttsx3.init()
        
        # List available voices
        voices = engine.getProperty('voices')
        
        # Try to find Vietnamese voice first (unlikely on Windows)
        vi_voice = None
        en_female_voice = None
        
        for voice in voices:
            voice_name = voice.name.lower()
            # Look for Vietnamese
            if 'vietnamese' in voice_name or 'vi_vn' in voice_name or 'vietnam' in voice_name:
                vi_voice = voice.id
                break
            # Look for English female voice (fallback)
            if 'female' in voice_name or 'woman' in voice_name or 'zira' in voice_name.lower():
                en_female_voice = voice.id
        
        # Use Vietnamese if found, else English female, else default
        if vi_voice:
            engine.setProperty('voice', vi_voice)
        elif en_female_voice:
            engine.setProperty('voice', en_female_voice)
        
        # Adjust rate based on speed (base rate = 150 WPM)
        base_rate = 150
        if slow:
            base_rate = 100  # slower
        adjusted_rate = int(base_rate * speed)
        engine.setProperty('rate', adjusted_rate)
        
        # Volume
        engine.setProperty('volume', 0.9)
        
        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
            tmp_path = tmp_file.name
        
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()
        
        # Check if file was created
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            return tmp_path
        return None
    except Exception as e:
        print(f"[pyttsx3 failed] {e}")
        return None


# ==========================================
# CÁC THAY ĐỔI CHÍNH Ở DƯỚI ĐÂY
# ==========================================

def text_to_speech_file(text: str, lang: str = "vi", slow: bool = False, speed: float = 1.5) -> str:
    """
    Convert text to speech and return audio file path.
    Args:
        speed: Playback speed multiplier (default: 1.5 now)
    """
    if not text or not text.strip():
        return None
    
    try:
        # Clean text - remove markdown formatting
        clean_text = text
        clean_text = clean_text.replace("###", "").replace("##", "").replace("#", "")
        clean_text = clean_text.replace("**", "").replace("*", "")
        clean_text = " ".join(clean_text.split())
        
        # 1. Try edge-tts first (high quality)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            audio_file = loop.run_until_complete(_text_to_speech_async(clean_text, slow, speed))
            if audio_file:
                st.info(f"🎤 [edge-tts] Tạo audio thành công (Tốc độ x{speed})")
                return audio_file
        finally:
            loop.close()
        
        # 2. Fallback to gTTS (Google TTS)
        # Note: gTTS does not support speed adjustment via API
        st.warning("⚠️ edge-tts không khả dụng, thử gTTS...")
        audio_file = _text_to_speech_gtts(clean_text, slow, speed)
        if audio_file:
            st.info("🎤 [gTTS] Tạo audio thành công (Google TTS)")
            return audio_file
        
        # 3. Fallback to pyttsx3 (offline)
        st.warning("⚠️ gTTS không khả dụng, thử pyttsx3 (offline)...")
        audio_file = _text_to_speech_pyttsx3(clean_text, slow, speed)
        if audio_file:
            st.info("🎤 [pyttsx3] Tạo audio thành công (offline)")
            return audio_file
        
        st.error("❌ Không thể tạo audio (cả 3 phương pháp đều fail)")
        return None
    except Exception as e:
        st.error(f"Lỗi khi tạo audio file: {e}")
        return None


def text_to_speech_async(text: str, lang: str = "vi", slow: bool = False):
    """
    Convert text to speech asynchronously (non-blocking).
    """
    return _text_to_speech_async(text, slow)


def estimate_speech_duration(text: str, words_per_minute: int = 150, speed: float = 1.5) -> float:
    """
    Estimate speech duration based on text length.
    Updated default speed to 1.5 to match TTS generation.
    """
    if not text:
        return 0
    
    # Count words (approximate)
    word_count = len(text.split())
    # Calculate duration
    duration_minutes = word_count / (words_per_minute * speed)
    duration_seconds = duration_minutes * 60
    
    # Add buffer for pauses (less buffer for faster speeds)
    buffer = max(0.5, 1.0 / speed)
    return duration_seconds + buffer


def cleanup_audio_file(file_path):
    """Clean up audio file to free up space."""
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass