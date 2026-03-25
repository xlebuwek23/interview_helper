import asyncio
import logging
import queue
import threading

from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class SpeechSignals(QObject):
    """Сигналы для передачи результатов распознавания в UI-поток."""
    # Промежуточный результат (показывается серым)
    interim_result = pyqtSignal(str)
    # Окончательный результат (добавляется в буфер)
    final_result = pyqtSignal(str)
    # Ошибка соединения
    error = pyqtSignal(str)


class SpeechProcessor(threading.Thread):
    """Отправляет аудио-чанки в Deepgram WebSocket, получает транскрипцию.

    Читает из audio_queue (bytes, linear16, 16kHz, mono),
    отправляет в Deepgram streaming API.
    Результаты передаёт через Qt-сигналы (interim_result, final_result).
    """

    def __init__(
        self,
        audio_queue: queue.Queue,
        api_key: str,
        language: str = "ru",
    ):
        super().__init__(daemon=True, name="SpeechProcessor")
        self.audio_queue = audio_queue
        self.api_key = api_key
        self.language = language
        self.signals = SpeechSignals()
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def stop(self) -> None:
        """Сигнализирует потоку остановиться."""
        self._stop_event.set()

    def run(self) -> None:
        logger.info("SpeechProcessor: запуск")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._process())
        except Exception as e:
            logger.error("SpeechProcessor: критическая ошибка: %s", e)
            self.signals.error.emit(str(e))
        finally:
            self._loop.close()
            logger.info("SpeechProcessor: остановлен")

    async def _process(self) -> None:
        """Основной цикл: подключение к Deepgram и отправка аудио."""
        deepgram = DeepgramClient(self.api_key)

        dg_connection = deepgram.listen.websocket.v("1")

        # Обработчик транскрипции
        def on_message(_self, result, **kwargs):
            try:
                transcript = result.channel.alternatives[0].transcript
                if not transcript:
                    return

                if result.is_final:
                    logger.debug("Финальный текст: %s", transcript)
                    self.signals.final_result.emit(transcript)
                else:
                    self.signals.interim_result.emit(transcript)
            except (IndexError, AttributeError) as e:
                logger.warning("Ошибка парсинга результата Deepgram: %s", e)

        def on_error(_self, error, **kwargs):
            logger.error("Deepgram ошибка: %s", error)
            self.signals.error.emit(str(error))

        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)

        options = LiveOptions(
            model="nova-3",
            language=self.language,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            interim_results=True,
            punctuate=True,
        )

        if not dg_connection.start(options):
            logger.error("Не удалось подключиться к Deepgram")
            self.signals.error.emit("Не удалось подключиться к Deepgram")
            return

        logger.info("SpeechProcessor: подключён к Deepgram")

        try:
            while not self._stop_event.is_set():
                try:
                    chunk = self.audio_queue.get(timeout=0.1)
                    dg_connection.send(chunk)
                except queue.Empty:
                    continue
        finally:
            dg_connection.finish()
            logger.info("SpeechProcessor: соединение с Deepgram закрыто")
