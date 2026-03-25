import asyncio
import logging
import queue
import threading

from anthropic import (
    AsyncAnthropic,
    AuthenticationError,
    RateLimitError,
    APIStatusError,
)
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class LLMSignals(QObject):
    """Сигналы для передачи ответов LLM в UI-поток."""
    # Частичный текст (streaming delta)
    stream_delta = pyqtSignal(str)
    # Стриминг завершён, полный ответ
    response_complete = pyqtSignal(str)
    # Ошибка
    error = pyqtSignal(str)


class LLMHandler(threading.Thread):
    """Отправляет текст в Claude API, стримит ответ через Qt-сигналы.

    Получает запросы через request_queue (строки с текстом интервьюера).
    Хранит историю последних N обменов для контекста.
    """

    def __init__(
        self,
        request_queue: queue.Queue,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
        system_prompt: str = "",
        context_history_count: int = 5,
    ):
        super().__init__(daemon=True, name="LLMHandler")
        self.request_queue = request_queue
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.context_history_count = context_history_count
        self.signals = LLMSignals()
        self._stop_event = threading.Event()
        self._history: list[dict] = []

    def stop(self) -> None:
        """Сигнализирует потоку остановиться."""
        self._stop_event.set()

    def run(self) -> None:
        logger.info("LLMHandler: запуск (модель: %s)", self.model)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._process())
        except Exception as e:
            logger.error("LLMHandler: критическая ошибка: %s", e)
            self.signals.error.emit(str(e))
        finally:
            loop.close()
            logger.info("LLMHandler: остановлен")

    async def _process(self) -> None:
        """Основной цикл: ждёт запросы и отправляет в Claude API."""
        client = AsyncAnthropic(api_key=self.api_key)

        while not self._stop_event.is_set():
            try:
                text = self.request_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            await self._handle_request(client, text)

    async def _handle_request(self, client: AsyncAnthropic, user_text: str) -> None:
        """Отправляет запрос в Claude API и стримит ответ."""
        # Добавляем сообщение пользователя в историю
        self._history.append({"role": "user", "content": user_text})

        # Обрезаем историю до последних N обменов (N * 2 сообщений: user + assistant)
        max_messages = self.context_history_count * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

        logger.debug("Отправка в LLM: %s", user_text[:100])

        full_response = ""

        try:
            async with client.messages.stream(
                model=self.model,
                max_tokens=1024,
                system=self.system_prompt,
                messages=self._history,
            ) as stream:
                async for text in stream.text_stream:
                    full_response += text
                    self.signals.stream_delta.emit(text)

            # Добавляем ответ ассистента в историю
            self._history.append({"role": "assistant", "content": full_response})
            self.signals.response_complete.emit(full_response)
            logger.debug("LLM ответ получен (%d символов)", len(full_response))

        except AuthenticationError:
            logger.error("Невалидный API-ключ Anthropic")
            self._rollback_last_user_message()
            self.signals.error.emit("INVALID KEY")

        except RateLimitError:
            logger.error("Rate limit Claude API")
            self._rollback_last_user_message()
            self.signals.error.emit("RATE LIMIT")

        except APIStatusError as e:
            # 5xx ошибки сервиса
            logger.error("Ошибка Claude API (status %d): %s", e.status_code, e)
            self._rollback_last_user_message()
            if e.status_code >= 500:
                self.signals.error.emit("SERVICE ERROR")
            else:
                self.signals.error.emit(str(e))

        except Exception as e:
            logger.error("Неожиданная ошибка Claude API: %s", e)
            self._rollback_last_user_message()
            self.signals.error.emit(str(e))

    def _rollback_last_user_message(self) -> None:
        """Убирает последнее сообщение пользователя из истории при ошибке."""
        if self._history and self._history[-1]["role"] == "user":
            self._history.pop()
