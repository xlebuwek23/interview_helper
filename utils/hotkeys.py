import ctypes
import ctypes.wintypes
import logging
import threading

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

# Win32 константы
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312

# Маппинг строковых модификаторов → Win32 флаги
_MODIFIER_MAP = {
    "ctrl": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}

# Маппинг строковых клавиш → виртуальные коды (VK_*)
_VK_MAP = {
    **{chr(c): c for c in range(0x41, 0x5B)},  # A-Z → 0x41-0x5A
    **{str(c - 0x30): c for c in range(0x30, 0x3A)},  # 0-9 → 0x30-0x39
    **{f"f{i}": 0x70 + i - 1 for i in range(1, 13)},  # F1-F12 → 0x70-0x7B
    "space": 0x20,
    "enter": 0x0D,
    "escape": 0x1B,
    "tab": 0x09,
}


def parse_hotkey(hotkey_str: str) -> tuple[int, int]:
    """Парсит строку хоткея ('ctrl+alt+shift+m') в (modifiers, vk_code).

    Возвращает кортеж (комбинация модификаторов, виртуальный код клавиши).
    """
    parts = hotkey_str.lower().strip().split("+")
    modifiers = 0
    vk_code = 0

    for part in parts:
        part = part.strip()
        if part in _MODIFIER_MAP:
            modifiers |= _MODIFIER_MAP[part]
        elif part in _VK_MAP:
            vk_code = _VK_MAP[part]
        else:
            raise ValueError(f"Неизвестная клавиша: '{part}' в '{hotkey_str}'")

    if vk_code == 0:
        raise ValueError(f"Не указана основная клавиша в '{hotkey_str}'")

    return modifiers, vk_code


class HotkeySignals(QObject):
    """Сигналы для глобальных хоткеев."""
    mute_toggle = pyqtSignal()
    send_to_llm = pyqtSignal()
    mode_toggle = pyqtSignal()
    show_hide = pyqtSignal()


class HotkeyManager(threading.Thread):
    """Регистрирует глобальные хоткеи через Win32 RegisterHotKey.

    Работает в отдельном потоке с собственным message loop.
    """

    # ID хоткеев (произвольные, уникальные в пределах потока)
    _HOTKEY_MUTE = 1
    _HOTKEY_SEND = 2
    _HOTKEY_MODE = 3
    _HOTKEY_SHOW = 4

    def __init__(self, hotkey_settings: dict):
        super().__init__(daemon=True, name="HotkeyManager")
        self.hotkey_settings = hotkey_settings
        self.signals = HotkeySignals()
        self._stop_event = threading.Event()
        self._thread_id: int | None = None

    def stop(self) -> None:
        """Останавливает message loop, посылая WM_QUIT в поток."""
        self._stop_event.set()
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, 0x0012, 0, 0  # WM_QUIT
            )

    def run(self) -> None:
        logger.info("HotkeyManager: запуск")
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        # Маппинг ID → (строка хоткея, сигнал)
        hotkey_map = {
            self._HOTKEY_MUTE: ("mute_toggle", self.signals.mute_toggle),
            self._HOTKEY_SEND: ("send_to_llm", self.signals.send_to_llm),
            self._HOTKEY_MODE: ("mode_toggle", self.signals.mode_toggle),
            self._HOTKEY_SHOW: ("show_hide", self.signals.show_hide),
        }

        registered_ids = []

        for hotkey_id, (setting_key, _signal) in hotkey_map.items():
            hotkey_str = self.hotkey_settings.get(setting_key, "")
            if not hotkey_str:
                continue

            try:
                modifiers, vk_code = parse_hotkey(hotkey_str)
            except ValueError as e:
                logger.error("Ошибка парсинга хоткея '%s': %s", hotkey_str, e)
                continue

            result = ctypes.windll.user32.RegisterHotKey(
                None, hotkey_id, modifiers, vk_code
            )
            if result:
                logger.info("Хоткей зарегистрирован: %s (id=%d)", hotkey_str, hotkey_id)
                registered_ids.append(hotkey_id)
            else:
                logger.error(
                    "Не удалось зарегистрировать хоткей: %s (возможно, занят другим приложением)",
                    hotkey_str,
                )

        # Message loop — ждём WM_HOTKEY
        msg = ctypes.wintypes.MSG()
        while not self._stop_event.is_set():
            result = ctypes.windll.user32.GetMessageW(
                ctypes.byref(msg), None, 0, 0
            )
            if result <= 0:  # WM_QUIT или ошибка
                break

            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                if hotkey_id in hotkey_map:
                    _setting_key, signal = hotkey_map[hotkey_id]
                    signal.emit()
                    logger.debug("Хоткей нажат: id=%d", hotkey_id)

        # Снимаем регистрацию хоткеев
        for hotkey_id in registered_ids:
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)

        logger.info("HotkeyManager: остановлен")
