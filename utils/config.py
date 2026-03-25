import json
import logging
import os
import copy

logger = logging.getLogger(__name__)

# Путь к settings.json рядом с корнем проекта
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(_BASE_DIR, "settings.json")
SETTINGS_EXAMPLE_PATH = os.path.join(_BASE_DIR, "settings.example.json")

DEFAULT_SETTINGS = {
    "api": {
        "anthropic_key": "",
        "anthropic_model": "claude-haiku-4-5-20251001",
        "deepgram_key": "",
        "deepgram_language": "ru",
    },
    "audio": {
        "device": "default",
        "silence_timeout_sec": 3,
    },
    "hotkeys": {
        "mute_toggle": "ctrl+alt+shift+m",
        "send_to_llm": "ctrl+alt+shift+s",
        "mode_toggle": "ctrl+alt+shift+a",
        "show_hide": "ctrl+alt+shift+h",
    },
    "ui": {
        "opacity": 0.85,
        "width": 400,
        "height": 300,
        "position_x": None,
        "position_y": None,
        "font_size": 14,
    },
    "llm": {
        "context_history_count": 5,
        "system_prompt": (
            "Ты помощник на собеседовании. Отвечай кратко, структурировано, "
            "в стиле устной речи. Язык — русский. Давай суть за 3-5 предложений."
        ),
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Рекурсивное слияние: override дополняет base, не затирая существующие ключи."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_settings() -> dict:
    """Загружает настройки из settings.json. Если файла нет — создаёт с дефолтами."""
    if not os.path.exists(SETTINGS_PATH):
        logger.info("settings.json не найден, создаю с настройками по умолчанию")
        save_settings(DEFAULT_SETTINGS)
        return copy.deepcopy(DEFAULT_SETTINGS)

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            user_settings = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Ошибка чтения settings.json: %s. Использую настройки по умолчанию", e)
        return copy.deepcopy(DEFAULT_SETTINGS)

    # Дополняем пользовательские настройки дефолтами (если добавились новые ключи)
    merged = _deep_merge(DEFAULT_SETTINGS, user_settings)
    return merged


def save_settings(settings: dict) -> None:
    """Сохраняет настройки в settings.json."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        logger.info("Настройки сохранены в %s", SETTINGS_PATH)
    except OSError as e:
        logger.error("Ошибка записи settings.json: %s", e)
