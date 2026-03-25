import logging
import threading
import queue

import numpy as np
import pyaudiowpatch as pyaudio

logger = logging.getLogger(__name__)

# Целевой формат для Deepgram: linear16, 16kHz, mono
TARGET_RATE = 16000
TARGET_CHANNELS = 1


class AudioCapture(threading.Thread):
    """Захватывает системный звук через WASAPI Loopback и отдаёт чанки в очередь.

    Выходной формат: bytes (int16, 16kHz, mono) — оптимально для Deepgram STT.
    """

    def __init__(self, audio_queue: queue.Queue, chunk_duration_ms: int = 100):
        super().__init__(daemon=True, name="AudioCapture")
        self.audio_queue = audio_queue
        self.chunk_duration_ms = chunk_duration_ms
        self._muted = False
        self._stop_event = threading.Event()
        self._pa: pyaudio.PyAudio | None = None

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self._muted = value
        logger.info("Аудио %s", "заглушено" if value else "включено")

    def stop(self) -> None:
        """Сигнализирует потоку остановиться."""
        self._stop_event.set()

    def run(self) -> None:
        logger.info("AudioCapture: запуск")
        self._pa = pyaudio.PyAudio()

        try:
            loopback_device = self._pa.get_default_wasapi_loopback()
            logger.info(
                "Loopback-устройство: %s (rate=%d, channels=%d)",
                loopback_device["name"],
                int(loopback_device["defaultSampleRate"]),
                loopback_device["maxInputChannels"],
            )
        except Exception as e:
            logger.error("Не удалось найти WASAPI Loopback устройство: %s", e)
            self._cleanup()
            return

        device_rate = int(loopback_device["defaultSampleRate"])
        device_channels = loopback_device["maxInputChannels"]
        # Размер чанка в фреймах для нативного формата устройства
        frames_per_chunk = int(device_rate * self.chunk_duration_ms / 1000)

        try:
            stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=device_channels,
                rate=device_rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=frames_per_chunk,
            )
        except Exception as e:
            logger.error("Не удалось открыть аудио-поток: %s", e)
            self._cleanup()
            return

        logger.info("AudioCapture: запись начата")

        try:
            while not self._stop_event.is_set():
                try:
                    raw_data = stream.read(frames_per_chunk, exception_on_overflow=False)
                except Exception as e:
                    logger.warning("Ошибка чтения аудио: %s", e)
                    continue

                if self._muted:
                    continue

                converted = self._convert_audio(
                    raw_data, device_channels, device_rate
                )
                self.audio_queue.put(converted)
        finally:
            stream.stop_stream()
            stream.close()
            self._cleanup()
            logger.info("AudioCapture: остановлен")

    def _convert_audio(
        self, raw_data: bytes, source_channels: int, source_rate: int
    ) -> bytes:
        """Конвертирует аудио: float32 → int16, stereo → mono, resample до 16kHz."""
        # float32 массив
        audio = np.frombuffer(raw_data, dtype=np.float32)

        # Stereo → mono: усреднение каналов
        if source_channels > 1:
            # Reshape в [frames, channels] и усредняем по оси каналов
            audio = audio.reshape(-1, source_channels).mean(axis=1)

        # Resample если нужно (простая децимация с усреднением)
        if source_rate != TARGET_RATE:
            # Коэффициент децимации (например, 48000/16000 = 3)
            ratio = source_rate / TARGET_RATE
            new_length = int(len(audio) / ratio)
            # Используем линейную интерполяцию для ресемплинга
            indices = np.linspace(0, len(audio) - 1, new_length)
            audio = np.interp(indices, np.arange(len(audio)), audio)

        # float32 → int16
        audio = np.clip(audio, -1.0, 1.0)
        audio_int16 = (audio * 32767).astype(np.int16)

        return audio_int16.tobytes()

    def _cleanup(self) -> None:
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
