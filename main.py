"""Interview Helper — точка входа.

Запуск: python main.py
"""

import logging
import signal
import sys
from logging.handlers import RotatingFileHandler

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from utils.config import load_settings, save_settings
from ui.overlay import OverlayWindow
from ui.settings_dialog import SettingsDialog
from core.controller import Controller


def setup_logging(debug: bool = False) -> None:
    """Настраивает логирование: файл + консоль."""
    level = logging.DEBUG if debug else logging.INFO

    # Форматтер
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Файловый handler с ротацией (5 MB, 3 бэкапа)
    file_handler = RotatingFileHandler(
        "interview-helper.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Консольный handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # Корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def check_api_keys(settings: dict) -> bool:
    """Проверяет наличие API-ключей. Если нет — открывает диалог настроек."""
    api = settings.get("api", {})
    return bool(api.get("anthropic_key")) and bool(api.get("deepgram_key"))


def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Interview Helper: запуск")

    app = QApplication(sys.argv)

    settings = load_settings()

    # Если API-ключи не заданы — показываем диалог настроек
    if not check_api_keys(settings):
        logger.info("API-ключи не найдены, открываю настройки")
        dialog = SettingsDialog(settings)
        if dialog.exec():
            new_settings = dialog.get_settings()
            if new_settings:
                settings.update(new_settings)
                save_settings(settings)
        else:
            logger.info("Пользователь отменил ввод ключей, выход")
            sys.exit(0)

    # Проверяем ещё раз после диалога
    if not check_api_keys(settings):
        logger.error("API-ключи не указаны, выход")
        sys.exit(1)

    # Создаём overlay-окно
    ui_settings = settings.get("ui", {})
    overlay = OverlayWindow(
        width=ui_settings.get("width", 400),
        height=ui_settings.get("height", 300),
        opacity=ui_settings.get("opacity", 0.85),
        font_size=ui_settings.get("font_size", 14),
        position_x=ui_settings.get("position_x"),
        position_y=ui_settings.get("position_y"),
    )

    # Создаём контроллер
    controller = Controller(settings, overlay)

    # Подключаем кнопку настроек
    overlay.settings_button.clicked.connect(controller.open_settings)

    # Показываем окно и запускаем модули
    overlay.show()
    controller.start()

    logger.info("Interview Helper: готов к работе")

    # Ctrl+C — корректное завершение из консоли.
    # Qt event loop блокирует обработку SIGINT в Python,
    # поэтому нужен таймер, который периодически отдаёт управление интерпретатору.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sigint_timer = QTimer()
    sigint_timer.timeout.connect(lambda: None)  # даёт Python обработать сигнал
    sigint_timer.start(200)

    # Запускаем Qt event loop
    exit_code = app.exec()

    # Корректное завершение
    controller.stop()
    logger.info("Interview Helper: завершён")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
