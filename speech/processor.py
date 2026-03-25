import logging
import queue
import threading

from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types import ListenV1Results
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
    отправляет в Deepgram streaming API v1.
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

    def stop(self) -> None:
        """Сигнализирует потоку остановиться."""
        self._stop_event.set()

    def run(self) -> None:
        logger.info("SpeechProcessor: запуск")
        try:
            self._process()
        except Exception as e:
            logger.error("SpeechProcessor: критическая ошибка: %s", e)
            self.signals.error.emit(str(e))
        finally:
            logger.info("SpeechProcessor: остановлен")

    def _process(self) -> None:
        """Основной цикл: подключение к Deepgram v1 (streaming STT) и отправка аудио."""
        client = DeepgramClient(api_key=self.api_key)

        with client.listen.v1.connect(
            model="nova-3",
            language=self.language,
            encoding="linear16",
            sample_rate="16000",
            interim_results="true",
            punctuate="true",
            endpointing="300",
        ) as connection:

            # Обработчик результатов транскрипции
            def on_message(message):
                # V1 отправляет разные типы (Metadata, Results, SpeechStarted и др.)
                if not isinstance(message, ListenV1Results):
                    return

                try:
                    transcript = message.channel.alternatives[0].transcript
                    if not transcript or not transcript.strip():
                        return

                    if message.is_final:
                        logger.debug("Финальный текст: %s", transcript)
                        self.signals.final_result.emit(transcript)
                    else:
                        self.signals.interim_result.emit(transcript)

                except (AttributeError, IndexError) as e:
                    logger.warning("Ошибка парсинга результата Deepgram: %s", e)

            def on_error(error):
                logger.error("Deepgram ошибка: %s", error)
                self.signals.error.emit(str(error))

            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, on_error)

            # start_listening() блокирует поток (recv-loop),
            # поэтому запускаем его в фоне, а аудио отправляем в текущем потоке
            recv_thread = threading.Thread(
                target=connection.start_listening, daemon=True
            )
            recv_thread.start()
            logger.info("SpeechProcessor: подключён к Deepgram")

            try:
                while not self._stop_event.is_set():
                    try:
                        chunk = self.audio_queue.get(timeout=0.1)
                        connection.send_media(chunk)
                    except queue.Empty:
                        continue
            finally:
                connection.send_close_stream()
                recv_thread.join(timeout=3)
                logger.info("SpeechProcessor: соединение с Deepgram закрыто")
