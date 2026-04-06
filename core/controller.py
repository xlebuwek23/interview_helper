import logging
import queue

from PyQt6.QtCore import QObject, QTimer

from core.state import AppState, AppMode
from audio.capture import AudioCapture
from speech.processor import SpeechProcessor
from llm.handler import LLMHandler
from utils.hotkeys import HotkeyManager
from utils.config import save_settings

logger = logging.getLogger(__name__)

# Минимальное количество слов для отправки в LLM
MIN_WORDS_THRESHOLD = 3


class Controller(QObject):
    """Координирует все модули приложения.

    Управляет состояниями (LISTENING/MUTED/PROCESSING),
    связывает аудио → STT → LLM → UI через очереди и сигналы.
    """

    def __init__(self, settings: dict, overlay):
        super().__init__()
        self.settings = settings
        self.overlay = overlay

        self._state = AppState.LISTENING
        self._mode = AppMode.AUTO

        # Буфер распознанного текста (копится до отправки в LLM)
        self._text_buffer: list[str] = []

        # Очереди для связи потоков
        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)
        self._llm_queue: queue.Queue = queue.Queue()

        # Таймер паузы в речи для AUTO-режима
        silence_timeout = settings.get("audio", {}).get("silence_timeout_sec", 3)
        self._silence_timer = QTimer()
        self._silence_timer.setSingleShot(True)
        self._silence_timer.setInterval(silence_timeout * 1000)
        self._silence_timer.timeout.connect(self._on_silence_timeout)

        # Создаём модули
        self._audio_capture = AudioCapture(self._audio_queue)

        api = settings.get("api", {})
        self._speech_processor = SpeechProcessor(
            audio_queue=self._audio_queue,
            api_key=api.get("deepgram_key", ""),
            language=api.get("deepgram_language", "ru"),
        )

        llm_settings = settings.get("llm", {})
        self._llm_handler = LLMHandler(
            request_queue=self._llm_queue,
            api_key=api.get("anthropic_key", ""),
            model=api.get("anthropic_model", "claude-haiku-4-5-20251001"),
            system_prompt=llm_settings.get("system_prompt", ""),
            context_history_count=llm_settings.get("context_history_count", 5),
        )

        self._hotkey_manager = HotkeyManager(settings.get("hotkeys", {}))

        # Подключаем сигналы
        self._connect_signals()

    def _connect_signals(self) -> None:
        """Подключает сигналы от всех модулей."""
        # STT → UI
        self._speech_processor.signals.interim_result.connect(self._on_interim_result)
        self._speech_processor.signals.final_result.connect(self._on_final_result)
        self._speech_processor.signals.error.connect(self._on_speech_error)

        # LLM → UI
        self._llm_handler.signals.stream_delta.connect(self._on_llm_delta)
        self._llm_handler.signals.response_complete.connect(self._on_llm_complete)
        self._llm_handler.signals.error.connect(self._on_llm_error)

        # Хоткеи → действия
        self._hotkey_manager.signals.mute_toggle.connect(self._on_mute_toggle)
        self._hotkey_manager.signals.send_to_llm.connect(self._on_send_to_llm)
        self._hotkey_manager.signals.mode_toggle.connect(self._on_mode_toggle)
        self._hotkey_manager.signals.show_hide.connect(self._on_show_hide)

        # Resize handle → сохранение размера
        self.overlay.size_changed.connect(self._on_size_changed)
        self.overlay.manual_query_submitted.connect(self._on_manual_query)

    def start(self) -> None:
        """Запускает все модули."""
        logger.info("Controller: запуск всех модулей")
        self._audio_capture.start()
        self._speech_processor.start()
        self._llm_handler.start()
        self._hotkey_manager.start()

        self._update_ui_state()

    def stop(self) -> None:
        """Останавливает все модули в правильном порядке."""
        logger.info("Controller: остановка")

        # Сохраняем позицию окна
        x, y = self.overlay.get_position()
        self.settings["ui"]["position_x"] = x
        self.settings["ui"]["position_y"] = y
        save_settings(self.settings)

        # Останавливаем в порядке: источник → обработчики
        self._silence_timer.stop()
        self._audio_capture.stop()
        self._speech_processor.stop()
        self._llm_handler.stop()
        self._hotkey_manager.stop()

        # Ожидаем завершения потоков
        for thread in [
            self._audio_capture,
            self._speech_processor,
            self._llm_handler,
            self._hotkey_manager,
        ]:
            thread.join(timeout=3)

        logger.info("Controller: все модули остановлены")

    # --- Обработчики сигналов STT ---

    def _on_interim_result(self, text: str) -> None:
        """Промежуточный результат STT — показываем серым."""
        if self._state == AppState.LISTENING:
            # Показываем буфер + interim
            display = " ".join(self._text_buffer)
            if display:
                display += " " + text
            else:
                display = text
            self.overlay.set_interviewer_text(display)

    def _on_final_result(self, text: str) -> None:
        """Окончательный результат STT — добавляем в буфер."""
        if self._state != AppState.LISTENING:
            return

        self._text_buffer.append(text)
        display = " ".join(self._text_buffer)
        self.overlay.set_interviewer_text(display)

        # В AUTO-режиме перезапускаем таймер тишины
        if self._mode == AppMode.AUTO:
            self._silence_timer.start()

    def _on_speech_error(self, error: str) -> None:
        logger.error("Ошибка STT: %s", error)
        self.overlay.set_error("STT: " + error)

    # --- Обработчики сигналов LLM ---

    def _on_llm_delta(self, delta: str) -> None:
        """Стриминг ответа LLM — добавляем текст."""
        self.overlay.append_response_text(delta)

    def _on_llm_complete(self, full_response: str) -> None:
        """Ответ LLM полностью получен."""
        logger.info("LLM ответ получен")
        self._set_state(AppState.LISTENING)

    def _on_llm_error(self, error: str) -> None:
        logger.error("Ошибка LLM: %s", error)
        self.overlay.set_error(error)
        self._set_state(AppState.LISTENING)

    # --- Обработчики хоткеев ---

    def _on_mute_toggle(self) -> None:
        if self._state == AppState.LISTENING:
            self._set_state(AppState.MUTED)
        elif self._state == AppState.MUTED:
            self._set_state(AppState.LISTENING)

    def _on_send_to_llm(self) -> None:
        """Ручная отправка текста в LLM."""
        if self._state == AppState.LISTENING:
            self._send_buffer_to_llm()

    def _on_mode_toggle(self) -> None:
        if self._mode == AppMode.AUTO:
            self._mode = AppMode.MANUAL
            self._silence_timer.stop()
        else:
            self._mode = AppMode.AUTO
        self._update_ui_state()
        logger.info("Режим переключён на %s", self._mode.value)

    def _on_show_hide(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.show()

    # --- Таймер тишины ---

    def _on_silence_timeout(self) -> None:
        """Вызывается после паузы в речи в AUTO-режиме."""
        if self._state == AppState.LISTENING and self._mode == AppMode.AUTO:
            self._send_buffer_to_llm()

    # --- Логика ---

    def _send_buffer_to_llm(self) -> None:
        """Отправляет накопленный текст в LLM."""
        if not self._text_buffer:
            return

        full_text = " ".join(self._text_buffer)

        # Фильтрация коротких фраз
        word_count = len(full_text.split())
        if word_count < MIN_WORDS_THRESHOLD:
            logger.debug(
                "Текст слишком короткий (%d слов), пропускаем: '%s'",
                word_count, full_text,
            )
            self._text_buffer.clear()
            return

        logger.info("Отправка в LLM (%d слов): %s", word_count, full_text[:100])

        self._text_buffer.clear()
        self.overlay.clear_response()
        self._set_state(AppState.PROCESSING)
        self._llm_queue.put(full_text)

    def _set_state(self, new_state: AppState) -> None:
        old_state = self._state
        self._state = new_state

        if new_state == AppState.MUTED:
            self._audio_capture.muted = True
            self._silence_timer.stop()
        elif new_state == AppState.LISTENING and old_state == AppState.MUTED:
            self._audio_capture.muted = False

        self._update_ui_state()
        logger.debug("Состояние: %s → %s", old_state.value, new_state.value)

    def _update_ui_state(self) -> None:
        """Обновляет статус-бар overlay в соответствии с текущим состоянием."""
        state_display = {
            AppState.LISTENING: ("REC", "#ff4444"),
            AppState.MUTED: ("MUTE", "#888888"),
            AppState.PROCESSING: ("PROCESSING", "#44aaff"),
        }
        text, color = state_display[self._state]
        self.overlay.set_status(text, color)
        self.overlay.set_mode(self._mode.value.upper())
        self.overlay.set_error("")

    def _on_size_changed(self, width: int, height: int) -> None:
        """Сохраняет новый размер окна в settings.json."""
        self.settings["ui"]["width"] = width
        self.settings["ui"]["height"] = height
        save_settings(self.settings)
        logger.info("Размер окна сохранён: %dx%d", width, height)

    def _on_manual_query(self, text: str) -> None:
        """Отправляет вручную введённый текст в LLM."""
        if self._state == AppState.PROCESSING:
            logger.debug("Ручной запрос проигнорирован: состояние PROCESSING")
            return
        logger.info("Ручной запрос (%d симв.): %s", len(text), text[:80])
        self.overlay.clear_response()
        self._set_state(AppState.PROCESSING)
        self._llm_queue.put(text)
        # Буфер речи не очищаем: параллельная речь продолжает копиться.
        # Пока state == PROCESSING, _on_final_result() молча игнорирует
        # новые результаты STT — это ожидаемое поведение.

    def open_settings(self) -> None:
        """Открывает диалог настроек (вызывается из overlay)."""
        from ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.settings, parent=self.overlay)
        if dialog.exec():
            new_settings = dialog.get_settings()
            if new_settings:
                self.settings.update(new_settings)
                save_settings(self.settings)
                logger.info("Настройки обновлены (перезапуск для применения)")
