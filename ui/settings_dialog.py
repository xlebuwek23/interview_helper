import ctypes
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QTextEdit,
    QPushButton,
    QTabWidget,
    QWidget,
    QMessageBox,
)

logger = logging.getLogger(__name__)

# WDA_EXCLUDEFROMCAPTURE — скрытие окна от screen capture.
# Константа дублируется локально: импорт из overlay.py создал бы
# циклическую зависимость (overlay → controller → settings_dialog → overlay).
_WDA_EXCLUDEFROMCAPTURE = 0x00000011


class SettingsDialog(QDialog):
    """Диалог настроек приложения.

    Позволяет изменить:
    - API-ключи (Anthropic, Deepgram)
    - Модель LLM, язык распознавания
    - Хоткеи
    - Параметры UI (прозрачность, размер шрифта)
    - Системный промпт
    """

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._result_settings: dict | None = None

        self.setWindowTitle("Настройки")
        self.setMinimumSize(450, 400)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        self._init_ui()
        self._load_from_settings()

    def showEvent(self, event) -> None:
        """Скрывает диалог от screen capture при показе."""
        super().showEvent(event)
        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowDisplayAffinity(
                hwnd, _WDA_EXCLUDEFROMCAPTURE
            )
            logger.info("Диалог настроек скрыт от screen capture")
        except Exception as e:
            # Некритично: диалог откроется, просто будет виден на записи
            logger.warning("Не удалось скрыть диалог настроек: %s", e)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._create_api_tab(), "API")
        tabs.addTab(self._create_hotkeys_tab(), "Хоткеи")
        tabs.addTab(self._create_ui_tab(), "Интерфейс")
        tabs.addTab(self._create_llm_tab(), "LLM")
        layout.addWidget(tabs)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self._on_save)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _create_api_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self._anthropic_key = QLineEdit()
        self._anthropic_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._anthropic_key.setPlaceholderText("sk-ant-...")
        form.addRow("Anthropic API Key:", self._anthropic_key)

        self._anthropic_model = QLineEdit()
        self._anthropic_model.setPlaceholderText("claude-haiku-4-5-20251001")
        form.addRow("Модель:", self._anthropic_model)

        self._deepgram_key = QLineEdit()
        self._deepgram_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._deepgram_key.setPlaceholderText("ключ Deepgram")
        form.addRow("Deepgram API Key:", self._deepgram_key)

        self._deepgram_language = QComboBox()
        self._deepgram_language.addItems(["ru", "en", "de", "fr", "es", "zh", "ja", "ko"])
        form.addRow("Язык распознавания:", self._deepgram_language)

        return widget

    def _create_hotkeys_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self._hk_mute = QLineEdit()
        form.addRow("Mute/Unmute:", self._hk_mute)

        self._hk_send = QLineEdit()
        form.addRow("Отправить в LLM:", self._hk_send)

        self._hk_mode = QLineEdit()
        form.addRow("AUTO/MANUAL:", self._hk_mode)

        self._hk_show = QLineEdit()
        form.addRow("Показать/скрыть:", self._hk_show)

        hint = QLabel("Формат: ctrl+alt+shift+клавиша")
        hint.setStyleSheet("color: #888888; font-size: 11px;")
        form.addRow("", hint)

        return widget

    def _create_ui_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self._opacity = QDoubleSpinBox()
        self._opacity.setRange(0.1, 1.0)
        self._opacity.setSingleStep(0.05)
        self._opacity.setDecimals(2)
        form.addRow("Прозрачность:", self._opacity)

        self._font_size = QSpinBox()
        self._font_size.setRange(8, 32)
        form.addRow("Размер шрифта:", self._font_size)

        self._silence_timeout = QSpinBox()
        self._silence_timeout.setRange(1, 30)
        self._silence_timeout.setSuffix(" сек")
        form.addRow("Таймаут тишины (авто-отправка):", self._silence_timeout)

        return widget

    def _create_llm_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._context_count = QSpinBox()
        self._context_count.setRange(0, 20)

        count_layout = QHBoxLayout()
        count_layout.addWidget(QLabel("История контекста (обменов):"))
        count_layout.addWidget(self._context_count)
        count_layout.addStretch()
        layout.addLayout(count_layout)

        layout.addWidget(QLabel("Системный промпт:"))
        self._system_prompt = QTextEdit()
        self._system_prompt.setMaximumHeight(150)
        layout.addWidget(self._system_prompt)

        layout.addStretch()
        return widget

    def _load_from_settings(self) -> None:
        """Заполняет поля из текущих настроек."""
        api = self.settings.get("api", {})
        self._anthropic_key.setText(api.get("anthropic_key", ""))
        self._anthropic_model.setText(api.get("anthropic_model", ""))
        self._deepgram_key.setText(api.get("deepgram_key", ""))

        lang = api.get("deepgram_language", "ru")
        idx = self._deepgram_language.findText(lang)
        if idx >= 0:
            self._deepgram_language.setCurrentIndex(idx)

        hotkeys = self.settings.get("hotkeys", {})
        self._hk_mute.setText(hotkeys.get("mute_toggle", ""))
        self._hk_send.setText(hotkeys.get("send_to_llm", ""))
        self._hk_mode.setText(hotkeys.get("mode_toggle", ""))
        self._hk_show.setText(hotkeys.get("show_hide", ""))

        ui = self.settings.get("ui", {})
        self._opacity.setValue(ui.get("opacity", 0.85))
        self._font_size.setValue(ui.get("font_size", 14))

        audio = self.settings.get("audio", {})
        self._silence_timeout.setValue(audio.get("silence_timeout_sec", 3))

        llm = self.settings.get("llm", {})
        self._context_count.setValue(llm.get("context_history_count", 5))
        self._system_prompt.setPlainText(llm.get("system_prompt", ""))

    def _on_save(self) -> None:
        """Валидация и сохранение настроек."""
        anthropic_key = self._anthropic_key.text().strip()
        deepgram_key = self._deepgram_key.text().strip()

        if not anthropic_key or not deepgram_key:
            QMessageBox.warning(
                self, "Ошибка", "Необходимо указать оба API-ключа."
            )
            return

        self._result_settings = {
            "api": {
                "anthropic_key": anthropic_key,
                "anthropic_model": self._anthropic_model.text().strip()
                    or "claude-haiku-4-5-20251001",
                "deepgram_key": deepgram_key,
                "deepgram_language": self._deepgram_language.currentText(),
            },
            "audio": {
                "device": self.settings.get("audio", {}).get("device", "default"),
                "silence_timeout_sec": self._silence_timeout.value(),
            },
            "hotkeys": {
                "mute_toggle": self._hk_mute.text().strip(),
                "send_to_llm": self._hk_send.text().strip(),
                "mode_toggle": self._hk_mode.text().strip(),
                "show_hide": self._hk_show.text().strip(),
            },
            "ui": {
                "opacity": self._opacity.value(),
                "width": self.settings.get("ui", {}).get("width", 400),
                "height": self.settings.get("ui", {}).get("height", 300),
                "position_x": self.settings.get("ui", {}).get("position_x"),
                "position_y": self.settings.get("ui", {}).get("position_y"),
                "font_size": self._font_size.value(),
            },
            "llm": {
                "context_history_count": self._context_count.value(),
                "system_prompt": self._system_prompt.toPlainText(),
            },
        }

        self.accept()

    def get_settings(self) -> dict | None:
        """Возвращает обновлённые настройки (None если отменено)."""
        return self._result_settings
