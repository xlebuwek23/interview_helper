import ctypes
import logging

from PyQt6.QtCore import Qt, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QMouseEvent, QWheelEvent, QPainter, QColor, QPolygon
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
)

logger = logging.getLogger(__name__)

# Win32: скрытие окна от screen capture (Windows 10 2004+)
WDA_EXCLUDEFROMCAPTURE = 0x00000011


class ResizeHandle(QWidget):
    """Треугольная ручка для изменения размера окна (правый нижний угол)."""

    HANDLE_SIZE = 16

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.HANDLE_SIZE, self.HANDLE_SIZE)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._drag_start: QPoint | None = None
        self._initial_size: QSize | None = None

    def paintEvent(self, event) -> None:
        """Рисует треугольник-индикатор."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(100, 100, 100, 180))
        size = self.HANDLE_SIZE
        painter.drawPolygon(QPolygon([
            QPoint(size, 0),
            QPoint(size, size),
            QPoint(0, size),
        ]))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._initial_size = self.parent().size()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_start
            parent = self.parent()
            new_w = max(self._initial_size.width() + delta.x(), parent.minimumWidth())
            new_h = max(self._initial_size.height() + delta.y(), parent.minimumHeight())
            parent.resize(new_w, new_h)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None:
            self._drag_start = None
            self._initial_size = None
            parent = self.parent()
            parent._set_exclude_from_capture()
            parent.size_changed.emit(parent.width(), parent.height())


class QueryInput(QTextEdit):
    """Поле ввода текста для ручного запроса. Enter отправляет, Shift+Enter — перенос строки."""

    submitted = pyqtSignal(str)

    def __init__(self, font: QFont, parent=None):
        super().__init__(parent)
        self.setFont(font)
        self.setPlaceholderText("Введите запрос...")
        self.setAcceptRichText(False)
        self.setStyleSheet("""
            QTextEdit {
                background-color: #2a2a2a;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 4px 6px;
            }
        """)
        self.document().contentsChanged.connect(self.updateGeometry)

    def sizeHint(self) -> QSize:
        """Автоподстройка высоты: от 1 до 3 строк."""
        line_height = self.fontMetrics().lineSpacing()
        margins = int(self.document().documentMargin()) * 2
        line_count = min(max(self.document().lineCount(), 1), 3)
        h = line_height * line_count + margins + 8
        return QSize(self.width(), h)

    def minimumSizeHint(self) -> QSize:
        """Минимальная высота — 1 строка."""
        line_height = self.fontMetrics().lineSpacing()
        margins = int(self.document().documentMargin()) * 2
        return QSize(50, line_height + margins + 8)

    def keyPressEvent(self, event) -> None:
        """Enter без Shift — отправить. Shift+Enter — перенос строки."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                text = self.toPlainText().strip()
                if text:
                    self.submitted.emit(text)
                    self.clear()
        else:
            super().keyPressEvent(event)


class OverlayWindow(QWidget):
    """Overlay-окно поверх экрана, невидимое при screen share.

    Содержимое:
    - Статус-бар: индикатор записи (REC/MUTE), режим (AUTO/MANUAL), кнопка настроек
    - Область интервьюера: последний распознанный текст (серый)
    - Область ответа: ответ LLM (белый), прокрутка
    """

    size_changed = pyqtSignal(int, int)
    manual_query_submitted = pyqtSignal(str)

    def __init__(
        self,
        width: int = 400,
        height: int = 300,
        opacity: float = 0.85,
        font_size: int = 14,
        position_x: int | None = None,
        position_y: int | None = None,
    ):
        super().__init__()
        self._font_size = font_size
        self._drag_pos: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # Не показывать в панели задач
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(opacity)
        self.resize(width, height)
        self.setMinimumSize(250, 200)

        if position_x is not None and position_y is not None:
            self.move(position_x, position_y)

        self._init_ui()

    def _init_ui(self) -> None:
        font = QFont("Segoe UI", self._font_size)

        # Основной контейнер с тёмным фоном
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 8)
        main_layout.setSpacing(4)

        # Фон — через stylesheet всего виджета
        self.setStyleSheet("""
            OverlayWindow {
                background-color: rgba(30, 30, 30, 230);
                border-radius: 8px;
            }
        """)

        # === Статус-бар ===
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)

        self._status_label = QLabel("REC")
        self._status_label.setFont(QFont("Segoe UI", self._font_size - 2, QFont.Weight.Bold))
        self._status_label.setStyleSheet("color: #ff4444; padding: 2px 6px;")

        self._mode_label = QLabel("AUTO")
        self._mode_label.setFont(QFont("Segoe UI", self._font_size - 2))
        self._mode_label.setStyleSheet("color: #888888; padding: 2px 6px;")

        self._error_label = QLabel("")
        self._error_label.setFont(QFont("Segoe UI", self._font_size - 3))
        self._error_label.setStyleSheet("color: #ff8800; padding: 2px 6px;")
        self._error_label.setMaximumWidth(150)
        self._error_label.hide()

        self._settings_btn = QPushButton("\u2699")
        self._settings_btn.setFixedSize(28, 28)
        self._settings_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #888888;
                border: none;
                font-size: 16px;
            }
            QPushButton:hover {
                color: #ffffff;
            }
        """)

        self._close_btn = QPushButton("\u2715")
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #888888;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                color: #ff4444;
            }
        """)
        self._close_btn.clicked.connect(self._on_close)

        status_layout.addWidget(self._status_label)
        status_layout.addWidget(self._mode_label)
        status_layout.addWidget(self._error_label)
        status_layout.addStretch()
        status_layout.addWidget(self._settings_btn)
        status_layout.addWidget(self._close_btn)

        main_layout.addLayout(status_layout)

        # === Область интервьюера (распознанный текст) ===
        self._interviewer_label = QLabel("Ожидание речи...")
        self._interviewer_label.setFont(font)
        self._interviewer_label.setStyleSheet("color: #888888; padding: 4px;")
        self._interviewer_label.setWordWrap(True)
        self._interviewer_label.setMaximumHeight(60)
        main_layout.addWidget(self._interviewer_label)

        # Разделитель
        separator = QWidget()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #444444;")
        main_layout.addWidget(separator)

        # === Область ответа LLM ===
        self._response_text = QTextEdit()
        self._response_text.setFont(font)
        self._response_text.setReadOnly(True)
        self._response_text.setStyleSheet("""
            QTextEdit {
                background: transparent;
                color: #ffffff;
                border: none;
                padding: 4px;
            }
            QScrollBar:vertical {
                width: 6px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #555555;
                border-radius: 3px;
            }
        """)
        main_layout.addWidget(self._response_text, stretch=1)
        self._response_text.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

        # === Поле ввода запроса ===
        input_separator = QWidget()
        input_separator.setFixedHeight(1)
        input_separator.setStyleSheet("background-color: #444444;")
        main_layout.addWidget(input_separator)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(4)

        self._query_input = QueryInput(font, self)
        self._query_input.submitted.connect(self._on_query_submitted)
        input_row.addWidget(self._query_input, stretch=1)

        self._send_btn = QPushButton("\u2192")
        self._send_btn.setFixedSize(32, 32)
        self._send_btn.setStyleSheet("""
            QPushButton {
                background-color: #444444;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
        """)
        self._send_btn.clicked.connect(self._on_send_btn_clicked)
        input_row.addWidget(self._send_btn)

        main_layout.addLayout(input_row)

        # === Resize handle (правый нижний угол) ===
        resize_row = QHBoxLayout()
        resize_row.setContentsMargins(0, 0, 0, 0)
        resize_row.addStretch()
        resize_row.addWidget(ResizeHandle(self))
        main_layout.addLayout(resize_row)

    def showEvent(self, event) -> None:
        """После показа окна — скрываем от screen capture."""
        super().showEvent(event)
        self._set_exclude_from_capture()

    def _set_exclude_from_capture(self) -> None:
        """Устанавливает WDA_EXCLUDEFROMCAPTURE через Win32 API."""
        try:
            hwnd = int(self.winId())
            result = ctypes.windll.user32.SetWindowDisplayAffinity(
                hwnd, WDA_EXCLUDEFROMCAPTURE
            )
            if result:
                logger.info("Окно скрыто от screen capture")
            else:
                logger.warning(
                    "Не удалось скрыть окно от screen capture "
                    "(требуется Windows 10 2004+)"
                )
        except Exception as e:
            logger.error("Ошибка SetWindowDisplayAffinity: %s", e)

    # --- Перетаскивание окна ---

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Перенаправляет скролл в область ответа, если курсор над ней.

        Допущение: _response_text — прямой дочерний виджет OverlayWindow
        (не обёрнут в промежуточный контейнер). Если обёртка появится —
        нужно использовать mapTo/mapFrom для перевода координат.
        """
        if self._response_text.geometry().contains(event.position().toPoint()):
            QApplication.sendEvent(self._response_text.viewport(), event)
        else:
            super().wheelEvent(event)

    # --- Публичные методы обновления UI ---

    def set_status(self, text: str, color: str = "#ff4444") -> None:
        """Обновляет индикатор статуса (REC/MUTE/PROCESSING)."""
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color}; padding: 2px 6px;")

    def set_mode(self, mode: str) -> None:
        """Обновляет индикатор режима (AUTO/MANUAL)."""
        self._mode_label.setText(mode)

    def set_error(self, text: str) -> None:
        """Показывает сообщение об ошибке в статус-баре (обрезает с …, тултип — полный текст)."""
        if text:
            self._error_label.setToolTip(text)
            elided = self._error_label.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, self._error_label.maximumWidth()
            )
            self._error_label.setText(elided)
            self._error_label.show()
        else:
            self._error_label.setToolTip("")
            self._error_label.hide()

    def set_interviewer_text(self, text: str) -> None:
        """Обновляет текст интервьюера (распознанная речь)."""
        self._interviewer_label.setText(text)

    def set_response_text(self, text: str) -> None:
        """Устанавливает полный текст ответа LLM."""
        self._response_text.setPlainText(text)
        # Прокрутка вниз
        scrollbar = self._response_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def append_response_text(self, delta: str) -> None:
        """Добавляет текст к ответу (streaming)."""
        cursor = self._response_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(delta)
        # Прокрутка вниз
        scrollbar = self._response_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_response(self) -> None:
        """Очищает область ответа для нового запроса."""
        self._response_text.clear()

    def get_position(self) -> tuple[int, int]:
        """Возвращает текущую позицию окна для сохранения."""
        pos = self.pos()
        return pos.x(), pos.y()

    def _on_query_submitted(self, text: str) -> None:
        """Вызывается из QueryInput при Enter."""
        self.manual_query_submitted.emit(text)

    def _on_send_btn_clicked(self) -> None:
        """Кнопка → — отправить текст из поля ввода."""
        text = self._query_input.toPlainText().strip()
        if text:
            self.manual_query_submitted.emit(text)
            self._query_input.clear()

    def _on_close(self) -> None:
        """Закрытие приложения через кнопку ✕."""
        QApplication.instance().quit()

    @property
    def settings_button(self) -> QPushButton:
        """Кнопка настроек для подключения сигнала извне."""
        return self._settings_btn
