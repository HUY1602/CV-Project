"""Simple TTS connectivity check for edge-tts.
Run:  
  python tts_check.py

It will:
1) List Vietnamese voices available.
2) Try to synthesize a short sample with voice vi-VN-HoaiMyNeural.
Outputs the saved mp3 path (if success) or error message.
"""

import asyncio
import tempfile
import os
import edge_tts


SAMPLE_TEXT = "Xin chào, đây là bài kiểm tra kết nối edge-tts."
VOICE_NAME = "vi-VN-HoaiMyNeural"


def main():
    asyncio.run(_main_async())


async def _main_async():
    print("=== STEP 1: Liệt kê voice tiếng Việt ===")
    try:
        voices = await edge_tts.list_voices()
        vi_voices = [v for v in voices if v["Locale"].startswith("vi")]
        print(f"Tổng voice: {len(voices)}; Voice tiếng Việt: {len(vi_voices)}")
        for v in vi_voices:
            print(f"- {v['Name']} ({v.get('Gender','?')})")
    except Exception as e:
        print("Lỗi khi lấy danh sách voice:", e)
        return

    print("\n=== STEP 2: Thử synth với", VOICE_NAME, "===")
    try:
        communicate = edge_tts.Communicate(SAMPLE_TEXT, VOICE_NAME)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
            out_path = f.name
        await communicate.save(out_path)
        size = os.path.getsize(out_path)
        print(f"ĐÃ TẠO FILE: {out_path} (size {size} bytes)")
        print("Bạn có thể mở file này để nghe.")
    except Exception as e:
        print("Lỗi synth (thường do mạng / proxy / chặn 443):", e)
        return


if __name__ == "__main__":
    main()
