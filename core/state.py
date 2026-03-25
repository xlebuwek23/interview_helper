import enum


class AppState(enum.Enum):
    """Состояния приложения."""
    LISTENING = "listening"      # Слушает и распознаёт речь
    MUTED = "muted"              # Аудио отбрасывается
    PROCESSING = "processing"    # Ожидает ответа LLM


class AppMode(enum.Enum):
    """Режим отправки в LLM."""
    AUTO = "auto"        # Автоматически после паузы в речи
    MANUAL = "manual"    # Вручную по хоткею
