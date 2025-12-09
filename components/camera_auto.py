import av
import streamlit as st
import time
from PIL import Image
from streamlit_webrtc import WebRtcMode, VideoProcessorBase, webrtc_streamer

# Import các service
from services.deepface_service import analyze_emotion
from services.gemini_service import (
    get_gemini_api_key,
    init_gemini,
    create_emotion_intro,
)
from services.emotion_agent_service import (
    generate_advice_with_memory_from_result,
)
from services.tts_service import text_to_speech_file, estimate_speech_duration, cleanup_audio_file


# ===========================
# Phase labels (0..5) - TIẾNG VIỆT
# ===========================
PHASE_LABELS = {
    0: "Ấn tượng ban đầu khi khách vừa bước vào nhà hàng",
    1: "Đánh giá thái độ và cách phục vụ của nhân viên bồi bàn",
    2: "Đánh giá trình bày món ăn khi được mang ra",
    3: "Đánh giá chất lượng món ăn khi khách đang ăn",
    4: "Khách trò chuyện – chỉ quan sát, không suy diễn thành đánh giá dịch vụ",
    5: "Trạng thái bình thường (sau các phase đặc biệt / không vào phase)"
}


class EmotionVideoProcessor(VideoProcessorBase):
    """
    Video processor dùng cho streamlit-webrtc.
    Chỉ giữ frame mới nhất từ camera, để thread chính quyết định khi nào chụp.
    """

    def __init__(self):
        # Frame BGR mới nhất từ camera
        self.last_frame_bgr = None
        # Ảnh đã được "chụp" (PIL Image) giống như Take photo
        self.captured_image = None
        # Kết quả phân tích cảm xúc gần nhất
        self.last_result = None

    def recv(self, frame: av.VideoFrame) -> av.VideoFrame:
        # Lưu frame mới nhất, không phân tích tại đây
        img_bgr = frame.to_ndarray(format="bgr24")
        self.last_frame_bgr = img_bgr
        return frame


def render_camera_auto(interval_seconds: int = 15):
    """
    Giao diện và logic cho chế độ Camera auto.
    Sequential flow: Detect emotion → Call Gemini → Show response → Detect tiếp
    """

    st.subheader("🤖 Trợ lý cảm xúc AI - Chế độ tự động (6 phase)")
    st.write(
        "**Quy trình:** Detect cảm xúc → AI phân tích → Detect tiếp\n\n"
        "Khi bật 'Tự động detect', 6 lần detect đầu sẽ tương ứng các phase 0..5 (xem nhãn). "
        "Sau khi hoàn tất 6 phase, hệ thống sẽ chuyển về trạng thái bình thường (phase 5)."
    )

    # -------------------------
    # session_state defaults
    # -------------------------
    if "previous_emotion" not in st.session_state:
        st.session_state.previous_emotion = None
    if "is_gemini_processing" not in st.session_state:
        st.session_state.is_gemini_processing = False
    if "last_gemini_suggestion" not in st.session_state:
        st.session_state.last_gemini_suggestion = None
    if "last_detection_time" not in st.session_state:
        st.session_state.last_detection_time = 0
    if "waiting_for_ai" not in st.session_state:
        st.session_state.waiting_for_ai = False
    if "is_playing_audio" not in st.session_state:
        st.session_state.is_playing_audio = False
    if "current_audio_file" not in st.session_state:
        st.session_state.current_audio_file = None

    # detect_count: số lần special-phase đã chạy (0..6). Khi <6 và auto_mode thì sẽ lấy phase = detect_count (0..5)
    if "detect_count" not in st.session_state:
        st.session_state.detect_count = 0

    # lưu phase active gần nhất (0..5). Mặc định là 5 (bình thường)
    if "current_phase" not in st.session_state:
        st.session_state.current_phase = 5

    # force_detect flag
    if "force_detect" not in st.session_state:
        st.session_state.force_detect = False

    # Control buttons
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔄 Reset và bắt đầu lại"):
            st.session_state.previous_emotion = None
            st.session_state.last_gemini_suggestion = None
            st.session_state.waiting_for_ai = False
            st.session_state.is_gemini_processing = False
            st.session_state.last_detection_time = 0
            st.session_state.force_detect = True
            st.session_state.detect_count = 0
            st.session_state.current_phase = 5
            st.success("✅ Đã reset! Sẵn sàng detect cảm xúc mới.")
            st.rerun()

    with col2:
        if st.button("▶️ Detect cảm xúc ngay"):
            st.session_state.waiting_for_ai = False
            st.session_state.last_detection_time = 0
            st.session_state.force_detect = True
            st.rerun()

    with col3:
        auto_mode = st.checkbox("🔄 Tự động detect", value=False, key="auto_detect_mode")

    # TTS settings
    tts_enabled = st.checkbox("🔊 Bật đọc text-to-speech", value=True, key="tts_enabled")

    # -------------------------
    # webrtc streamer
    # -------------------------
    webrtc_ctx = webrtc_streamer(
        key=f"emotion-auto-{interval_seconds}",
        mode=WebRtcMode.SENDRECV,
        media_stream_constraints={"video": True, "audio": False},
        video_processor_factory=EmotionVideoProcessor,
    )

    result_placeholder = st.empty()
    chart_placeholder = st.empty()
    suggestion_placeholder = st.empty()
    status_placeholder = st.empty()
    audio_placeholder = st.empty()

    # -------------------------
    # nếu webrtc sẵn sàng
    # -------------------------
    if webrtc_ctx.video_processor is not None:
        processor = webrtc_ctx.video_processor

        # nếu hệ thống đang chờ kết quả Gemini, hiển thị trạng thái và kết quả cũ
        if st.session_state.is_gemini_processing or st.session_state.waiting_for_ai:
            status_placeholder.info("⏳ **Đang chờ AI trả lời...** Vui lòng đợi.")
            if processor.last_result:
                result = processor.last_result
                dominant_emotion = result.get("dominant_emotion")
                emotions = result.get("emotion", {})
                result_placeholder.success(f"**Cảm xúc chính**: {dominant_emotion}")
                if emotions:
                    chart_placeholder.subheader("Chi tiết các cảm xúc")
                    chart_placeholder.bar_chart(emotions)
            if st.session_state.last_gemini_suggestion:
                suggestion_placeholder.markdown(
                    f"### 💬 Gợi ý từ trợ lý nhà hàng\n\n{st.session_state.last_gemini_suggestion}"
                )
            # không đi tiếp khi chờ AI
            return

        # Kiểm tra có nên detect không (auto mode hoặc force detect hoặc quá thời gian)
        should_detect = (
            st.session_state.force_detect
            or auto_mode
            or st.session_state.last_detection_time == 0
            or (time.time() - st.session_state.last_detection_time) > interval_seconds
        )

        # Reset force_detect flag nếu đã dùng
        if st.session_state.force_detect:
            st.session_state.force_detect = False

        # Nếu có frame và cần detect
        if should_detect and processor.last_frame_bgr is not None:
            try:
                frame_rgb = processor.last_frame_bgr[:, :, ::-1]  # BGR -> RGB
                image = Image.fromarray(frame_rgb)
                processor.captured_image = image

                # Phân tích cảm xúc (DeepFace)
                with st.spinner("🔍 Đang phân tích cảm xúc..."):
                    result = analyze_emotion(image)
                processor.last_result = result
                st.session_state.last_detection_time = time.time()

            except Exception as e:
                # Không để exception làm crash app
                st.warning(f"Lỗi khi detect cảm xúc: {e}")
                # hiển thị lỗi trong status, nhưng tiếp tục (không reset state)
                status_placeholder.error(f"Lỗi detect: {e}")
                return

        # Hiển thị ảnh đã capture
        if processor.captured_image is not None:
            st.image(
                processor.captured_image,
                caption="Ảnh đã capture",
                width='stretch',
            )

        # Nếu có kết quả phân tích thì xử lý
        result = processor.last_result
        if not result:
            # chưa có kết quả
            if processor.last_frame_bgr is None:
                result_placeholder.info(
                    "📷 **Đang chờ camera...** Hãy đảm bảo camera đã được bật và cho phép truy cập."
                )
            else:
                result_placeholder.info("💡 Nhấn 'Detect cảm xúc ngay' để bắt đầu phân tích.")
            return

        # --- lấy kết quả cảm xúc ---
        dominant_emotion = result.get("dominant_emotion")
        emotions = result.get("emotion", {})

        result_placeholder.success(f"**Cảm xúc chính**: {dominant_emotion}")
        if emotions:
            chart_placeholder.subheader("Chi tiết các cảm xúc")
            chart_placeholder.bar_chart(emotions)

        # -------------------------
        # XÁC ĐỊNH PHASE
        # -------------------------
        if auto_mode:
            if st.session_state.detect_count < 6:
                phase = st.session_state.detect_count  # 0..5
            else:
                phase = 5
        else:
            # manual mode -> sử dụng current_phase (mặc định 5)
            phase = st.session_state.current_phase if isinstance(st.session_state.current_phase, int) else 5

        # Hiển thị nhãn phase hiện tại
        phase_label = PHASE_LABELS.get(phase, "Trạng thái không xác định")
        status_placeholder.info(f"🔎 Phase {phase}: {phase_label}")

        # -------------------------
        # GỌI GEMINI (hoặc agent)
        # -------------------------
        api_key = get_gemini_api_key()
        if not api_key:
            suggestion_placeholder.warning(
                "⚠️ **Chưa tìm thấy Gemini API key!**\n\n"
                "Vui lòng: tạo file `.env` với GEMINI_API_KEY=your_key_here hoặc nhập ở sidebar."
            )
            return

        # Khởi tạo model 1 lần / session
        if "gemini_model" not in st.session_state:
            try:
                with st.spinner("Đang khởi tạo Gemini model..."):
                    model, model_info = init_gemini(api_key)
                if model is None:
                    suggestion_placeholder.error(f"❌ Lỗi khởi tạo Gemini: {model_info}")
                    return
                st.session_state.gemini_model = model
                st.session_state.gemini_model_name = model_info
            except Exception as e:
                suggestion_placeholder.error(f"❌ Lỗi khi khởi tạo Gemini: {e}")
                return

        model = st.session_state.get("gemini_model")

        # Quyết định có gọi AI hay không
        previous_emotion = st.session_state.previous_emotion
        # track first call per phase
        if "phase_called" not in st.session_state:
            st.session_state.phase_called = set()

        need_call_ai = False
        if previous_emotion is None or previous_emotion != dominant_emotion:
            need_call_ai = True
        if phase not in st.session_state.phase_called:
            need_call_ai = True

        if need_call_ai:
            # set flags
            st.session_state.is_gemini_processing = True
            st.session_state.waiting_for_ai = True

            with st.spinner(f"🤔 AI trợ lý nhà hàng đang phân tích cảm xúc '{dominant_emotion}' (phase {phase})..."):
                try:
                    suggestion_text = generate_advice_with_memory_from_result(
                        model=model,
                        dominant_emotion=dominant_emotion,
                        emotions=emotions,
                        phase=phase,
                        user_id="default_user",
                    )
                except Exception as e:
                    suggestion_text = f"⚠️ Lỗi khi gọi Gemini: {e}"

            # clear flags
            st.session_state.is_gemini_processing = False
            st.session_state.waiting_for_ai = False

            # lưu kết quả
            st.session_state.previous_emotion = dominant_emotion
            st.session_state.last_gemini_suggestion = suggestion_text
            st.session_state.phase_called.add(phase)
            st.session_state.current_phase = phase

            # hiển thị
            if suggestion_text and suggestion_text.strip():
                if suggestion_text.startswith("⚠️"):
                    suggestion_placeholder.warning(suggestion_text)
                    status_placeholder.warning("⚠️ Có lỗi xảy ra khi gọi AI")
                    # Dù lỗi AI vẫn tăng phase để không bị kẹt
                    if auto_mode and phase < 5 and st.session_state.detect_count == phase:
                        st.session_state.detect_count += 1
                        st.session_state.last_detection_time = 0
                        st.session_state.force_detect = True
                        st.rerun()  # <--- RERUN TỰ ĐỘNG
                else:
                    suggestion_placeholder.markdown(
                        f"### 💬 Gợi ý từ trợ lý nhà hàng (Phase {phase})\n\n{suggestion_text}"
                    )

                    # TTS (tùy chọn)
                    if tts_enabled:
                        try:
                            st.session_state.is_playing_audio = True

                            emotion_intro = create_emotion_intro(dominant_emotion)
                            full_text_to_speak = f"{emotion_intro} {suggestion_text}"

                            audio_file = text_to_speech_file(full_text_to_speak, lang="vi", speed=1.5)
                            
                            if audio_file:
                                st.session_state.current_audio_file = audio_file
                                audio_placeholder.audio(audio_file, format="audio/mp3", autoplay=True)

                                # -----------------------------------------------------------
                                # [FIX] DÙNG AV ĐỂ LẤY THỜI GIAN THỰC CỦA FILE AUDIO
                                # -----------------------------------------------------------
                                try:
                                    # Sử dụng thư viện av để lấy duration chính xác
                                    with av.open(audio_file) as container:
                                        # container.duration tính bằng micro giây -> chia 1.000.000
                                        real_duration = float(container.duration) / 1000000
                                    
                                    # Buffer thêm 1 giây an toàn
                                    time_to_wait = real_duration + 1.0
                                    
                                except Exception as e:
                                    # Fallback: Nếu không đọc được file, dùng lại hàm ước lượng + buffer lớn (5s)
                                    print(f"Lỗi đọc duration: {e}")
                                    try:
                                        est_duration = estimate_speech_duration(full_text_to_speak, speed=1.5)
                                        time_to_wait = est_duration + 5.0
                                    except:
                                        time_to_wait = 10.0 # Mặc định an toàn

                                status_placeholder.info(f"🔊 Đang phát audio... (thời gian thực: {time_to_wait:.1f}s)")
                                time.sleep(time_to_wait)

                                cleanup_audio_file(audio_file)
                                st.session_state.is_playing_audio = False
                                st.session_state.current_audio_file = None
                                status_placeholder.success("✅ Đã đọc xong! Sẵn sàng detect tiếp theo.")
                                
                                # --- CHUYỂN PHASE TỰ ĐỘNG ---
                                if auto_mode and phase < 5 and st.session_state.detect_count == phase:
                                    st.session_state.detect_count += 1
                                    st.session_state.last_detection_time = 0
                                    st.session_state.force_detect = True
                                    st.rerun()  # <--- RERUN TỰ ĐỘNG
                                # ----------------------------
                            else:
                                st.session_state.is_playing_audio = False
                                status_placeholder.warning("⚠️ Không thể tạo audio. Tiếp tục detect...")
                                
                                # Vẫn chuyển phase dù lỗi Audio
                                if auto_mode and phase < 5 and st.session_state.detect_count == phase:
                                    st.session_state.detect_count += 1
                                    st.session_state.last_detection_time = 0
                                    st.session_state.force_detect = True
                                    st.rerun()

                        except Exception as e:
                            st.warning(f"Lỗi TTS: {e}")
                            st.session_state.is_playing_audio = False
                            
                            # Vẫn chuyển phase dù lỗi TTS Exception
                            if auto_mode and phase < 5 and st.session_state.detect_count == phase:
                                st.session_state.detect_count += 1
                                st.session_state.last_detection_time = 0
                                st.session_state.force_detect = True
                                st.rerun()
                    else:
                        # Case không bật TTS
                        status_placeholder.success("✅ AI đã trả lời xong! Sẵn sàng detect tiếp theo.")
                        
                        if auto_mode and phase < 5 and st.session_state.detect_count == phase:
                            st.session_state.detect_count += 1
                            st.session_state.last_detection_time = 0
                            st.session_state.force_detect = True
                            st.rerun()  # <--- RERUN TỰ ĐỘNG
            else:
                suggestion_placeholder.error("❌ Không nhận được phản hồi từ AI.")
        else:
            # không cần gọi AI
            if st.session_state.last_gemini_suggestion:
                suggestion_placeholder.markdown(
                    f"### 💬 Gợi ý từ trợ lý nhà hàng (cũ)\n\n{st.session_state.last_gemini_suggestion}"
                )
            status_placeholder.info("ℹ️ Cảm xúc không thay đổi và phase đã được xử lý trước đó. Tiếp tục detect...")

        # Nếu auto_mode bật nhưng đã hoàn tất 6 phase
        if auto_mode and st.session_state.detect_count >= 6:
            st.session_state.current_phase = 5