"""Capri's neon look for her windows: colors, the font, framed dialogs and their controls.

Every window opened from Capri goes through `place()`, so it appears in the middle of
the screen she's on. Her small dialogs stay on top; the settings window doesn't.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .config import ASSETS

ACCENT = "#ff2d55"  # her neon pink-red
CYAN = "#00e5ff"
YELLOW = "#f3e600"
TEXT = "#f4f4f8"
MUTED = "#8b8b9a"
BACKGROUND = "#0b0a11"
FIELD = "#16131f"
LINE = "#3b2735"
DARK_TEXT = "#0b0a11"
CUT = 16  # size of the cut-off corners
_UI = (ASSETS / "ui").as_posix()  # arrow images for the stylesheet

_family: str | None = None


def font_family() -> str:
    """Exo 2 when it's installed with the app, otherwise Segoe UI."""
    global _family
    if _family is None:
        _family = "Segoe UI"
        font_file = ASSETS / "fonts" / "exo2" / "Exo2-Variable.ttf"
        if font_file.is_file():
            loaded = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font_file)))
            if loaded:
                _family = loaded[0]
    return _family


def font(size: float = 10.0, bold: bool = False, spacing: float = 0.0) -> QFont:
    result = QFont(font_family())
    result.setPointSizeF(size)
    if bold:
        result.setWeight(QFont.Weight.Bold)
    if spacing:
        result.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return result


def cut_corners(rect: QRectF, cut: float) -> QPolygonF:
    """A rectangle with its top-left and bottom-right corners cut off."""
    return QPolygonF(
        [
            QPointF(rect.left() + cut, rect.top()),
            QPointF(rect.right(), rect.top()),
            QPointF(rect.right(), rect.bottom() - cut),
            QPointF(rect.right() - cut, rect.bottom()),
            QPointF(rect.left(), rect.bottom()),
            QPointF(rect.left(), rect.top() + cut),
        ]
    )


STYLE = f"""
QWidget {{ color: {TEXT}; }}
QLabel {{ background: transparent; }}
QLabel#hint {{ color: {MUTED}; }}
QLabel#error {{ color: {ACCENT}; }}
QLabel#warning {{ color: {YELLOW}; background: rgba(243, 230, 0, 0.07);
    border-left: 2px solid {YELLOW}; padding: 6px 8px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {FIELD}; border: 1px solid {LINE}; border-radius: 0;
    padding: 4px 8px; min-height: 22px; selection-background-color: {ACCENT};
    selection-color: {DARK_TEXT};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: #6b3a52; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QComboBox:on {{ border-color: {CYAN}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {MUTED}; background: #110f18; }}
QLineEdit[invalid="true"] {{ border-color: {ACCENT}; }}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; background: #201a2c; border-left: 1px solid {LINE};
}}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: #3a1f2d; }}
QSpinBox::up-arrow {{ image: url({_UI}/arrow-up.svg); width: 8px; height: 5px; }}
QSpinBox::down-arrow {{ image: url({_UI}/arrow-down.svg); width: 8px; height: 5px; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: center right;
    width: 24px; border: none; border-left: 1px solid {LINE}; }}
QComboBox::down-arrow {{ image: url({_UI}/arrow-down-accent.svg); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{
    background: {FIELD}; border: 1px solid {ACCENT}; outline: 0; padding: 2px;
    selection-background-color: {ACCENT}; selection-color: {DARK_TEXT};
}}
QSlider {{ min-height: 22px; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; }}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {CYAN});
}}
QSlider::handle:horizontal {{
    width: 8px; margin: -8px 0; background: {TEXT}; border: 1px solid {ACCENT};
}}
QSlider::handle:horizontal:hover {{ background: {CYAN}; }}
QPushButton#segment {{
    background: {FIELD}; border: 1px solid {LINE}; padding: 5px 12px; min-height: 20px;
}}
QPushButton#segment:hover {{ border-color: {CYAN}; }}
QPushButton#segment:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {DARK_TEXT}; }}
QListWidget#nav {{ background: transparent; border: none; outline: 0; }}
QListWidget#nav::item {{ padding: 9px 12px; margin: 1px 0; border-left: 3px solid transparent;
    color: {MUTED}; }}
QListWidget#nav::item:hover {{ color: {TEXT}; background: rgba(255, 255, 255, 0.04); }}
QListWidget#nav::item:selected {{ color: {TEXT}; border-left: 3px solid {ACCENT};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 45, 85, 0.28),
        stop:1 rgba(255, 45, 85, 0)); }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ width: 4px; background: rgba(255, 255, 255, 0.04); margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255, 45, 85, 0.6); min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {FIELD}; color: {TEXT}; border: 1px solid {CYAN}; padding: 4px; }}
"""


def place(window: QWidget, near: QWidget | None = None) -> None:
    """Sizes a window and moves it to the middle of the screen `near` is on."""
    window.ensurePolished()
    window.adjustSize()
    screen = None
    if near is not None and near.isVisible():
        screen = QGuiApplication.screenAt(near.frameGeometry().center())
    area = (screen or QGuiApplication.primaryScreen()).availableGeometry()
    size = window.size()
    left = area.left() + (area.width() - size.width()) // 2
    top = area.top() + (area.height() - size.height()) // 2
    window.move(max(area.left(), left), max(area.top(), top))


def present(window: QWidget, near: QWidget | None = None) -> None:
    """Shows a window in front of everything else: in the middle of the screen when it
    opens, or where it was (restored if minimized) when it's already open."""
    if not window.isVisible():
        place(window, near)
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()


class NeonButton(QPushButton):
    """A button with cut-off corners: filled pink ("primary") or outlined ("ghost")."""

    def __init__(self, text: str, primary: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.primary = primary
        self.setAutoDefault(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(font(9.5, bold=True, spacing=1.5))
        self.setMinimumHeight(34)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        width = self.fontMetrics().horizontalAdvance(self.text()) + 44
        return QSize(max(96, width), 34)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = cut_corners(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8)
        hover = self.underMouse() and self.isEnabled()
        if not self.isEnabled():
            fill, edge, color = QColor(FIELD), QColor(LINE), QColor(MUTED)
        elif self.primary:
            fill = QColor("#ff5277" if hover else ACCENT)
            edge, color = QColor(fill), QColor(DARK_TEXT)
        else:
            fill = QColor(255, 255, 255, 14 if hover else 0)
            edge, color = QColor(CYAN if hover else "#6b3a52"), QColor(TEXT)
        painter.setBrush(fill)
        painter.setPen(QPen(edge, 1.2))
        painter.drawPolygon(shape)
        painter.setPen(color)
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class Toggle(QAbstractButton):
    """An on/off switch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 22)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        edge = "#6b3a52" if self.underMouse() else LINE
        painter.setPen(QPen(QColor(ACCENT if on else edge), 1.2))
        painter.setBrush(QColor(255, 45, 85, 60) if on else QColor(FIELD))
        painter.drawPolygon(cut_corners(track, 5))
        knob = QRectF(0, 0, 16, track.height() - 6)
        knob.moveTop(track.top() + 3)
        knob.moveLeft(track.right() - knob.width() - 3 if on else track.left() + 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT if on else MUTED))
        painter.drawRect(knob)


class CloseButton(QAbstractButton):
    """The ✕ in a window's title bar, drawn so it doesn't depend on the font."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Close (Esc)")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        if hover:
            painter.fillRect(self.rect(), QColor(ACCENT))
        painter.setPen(QPen(QColor(DARK_TEXT if hover else MUTED), 1.6))
        middle = QRectF(self.rect()).center()
        for dx, dy in ((5, 5), (5, -5)):
            painter.drawLine(QPointF(middle.x() - dx, middle.y() - dy), QPointF(middle.x() + dx, middle.y() + dy))


class MinimizeButton(QAbstractButton):
    """The – in a window's title bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Minimize")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        if hover:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 24))
        painter.setPen(QPen(QColor(TEXT if hover else MUTED), 1.6))
        middle = QRectF(self.rect()).center()
        painter.drawLine(QPointF(middle.x() - 5, middle.y()), QPointF(middle.x() + 5, middle.y()))


class TitleBar(QWidget):
    """A window's title, its buttons, and dragging the window around by it."""

    HEIGHT = 44
    minimize_clicked = Signal()
    close_clicked = Signal()
    moved = Signal()  # after the window was dragged somewhere else

    def __init__(self, title: str, minimizable: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self._drag: QPoint | None = None
        heading = QLabel(f"// {title.upper()}")
        heading.setFont(font(10.5, bold=True, spacing=2.5))
        heading.setStyleSheet(f"color: {TEXT};")
        row = QHBoxLayout(self)
        row.setContentsMargins(CUT + 10, 0, 10, 0)
        row.setSpacing(2)
        row.addWidget(heading)
        row.addStretch(1)
        if minimizable:
            self.minimize_button = MinimizeButton()
            self.minimize_button.clicked.connect(self.minimize_clicked)
            row.addWidget(self.minimize_button)
        self.close_button = CloseButton()
        self.close_button.clicked.connect(self.close_clicked)
        row.addWidget(self.close_button)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self.window().move(event.globalPosition().toPoint() - self._drag)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag = None
            self.moved.emit()
        super().mouseReleaseEvent(event)


def paint_frame(window: QWidget, title_height: int) -> None:
    """Paints the neon frame of a window: cut corners, scanlines, a glowing title strip."""
    painter = QPainter(window)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(window.rect()).adjusted(1, 1, -1, -1)
    frame = QPainterPath()
    frame.addPolygon(cut_corners(rect, CUT))
    frame.closeSubpath()

    shade = QLinearGradient(rect.topLeft(), rect.bottomRight())
    shade.setColorAt(0, QColor(24, 14, 26, 250))
    shade.setColorAt(0.45, QColor(BACKGROUND))
    shade.setColorAt(1, QColor(8, 16, 22, 250))
    painter.fillPath(frame, shade)

    painter.save()
    painter.setClipPath(frame)
    painter.setPen(QPen(QColor(255, 255, 255, 7), 1))
    for y in range(int(rect.top()), int(rect.bottom()), 3):  # scanlines
        painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
    glow = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + title_height))
    glow.setColorAt(0, QColor(255, 45, 85, 40))
    glow.setColorAt(1, QColor(255, 45, 85, 0))
    painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), title_height), glow)
    painter.setPen(QPen(QColor(LINE), 1))
    painter.drawLine(
        QPointF(rect.left() + CUT + 10, rect.top() + title_height),
        QPointF(rect.right() - 10, rect.top() + title_height),
    )
    painter.restore()

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(ACCENT), 1.4))
    painter.drawPath(frame)
    # Accents: a cyan cut corner, a yellow bar along the bottom and small ticks.
    painter.setPen(QPen(QColor(CYAN), 3))
    painter.drawLine(QPointF(rect.left() + 1, rect.top() + CUT), QPointF(rect.left() + CUT, rect.top() + 1))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(YELLOW))
    painter.drawRect(QRectF(rect.left() + 24, rect.bottom() - 3, 56, 3))
    painter.setBrush(QColor(ACCENT))
    for index in range(3):
        painter.drawRect(QRectF(rect.right() - 60 + index * 8, rect.top() + 4, 4, 4))


class Segmented(QWidget):
    """A row of buttons where exactly one is chosen."""

    def __init__(self, choices: dict[str, object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.group = QButtonGroup(self)
        self.values: list[object] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for index, (label, value) in enumerate(choices.items()):
            button = QPushButton(label)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(button, index)
            self.values.append(value)
            layout.addWidget(button)
        layout.addStretch(1)

    def value(self) -> object:
        index = self.group.checkedId()
        return self.values[index] if index >= 0 else None

    def set_value(self, value: object) -> None:
        if value in self.values:
            self.group.button(self.values.index(value)).setChecked(True)


class NeonDialog(QDialog):
    """A frameless window with a neon outline, scanlines and its own title bar."""

    TITLE_HEIGHT = TitleBar.HEIGHT

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        on_top: bool = True,
        minimizable: bool = False,
    ) -> None:
        flags = Qt.WindowType.Dialog if parent is not None else Qt.WindowType.Window
        flags |= Qt.WindowType.FramelessWindowHint
        if minimizable:  # lets the taskbar button minimize and restore it too
            flags |= Qt.WindowType.WindowMinimizeButtonHint
        if on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(title)
        self.setStyleSheet(STYLE)
        self.setFont(font(10))

        self.title_bar = TitleBar(title, minimizable)
        self.title_bar.close_clicked.connect(self.reject)
        self.title_bar.minimize_clicked.connect(self.showMinimized)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(22, 14, 22, 20)
        self.body.setSpacing(12)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)
        outer.addWidget(self.title_bar)
        outer.addLayout(self.body, 1)

    def paintEvent(self, event) -> None:  # noqa: N802
        paint_frame(self, self.TITLE_HEIGHT)


class MessageDialog(NeonDialog):
    """A question, a notice, or a request for one line of text."""

    def __init__(
        self,
        title: str,
        text: str,
        parent: QWidget | None = None,
        yes: str = "OK",
        no: str | None = "Cancel",
        field: QLineEdit | None = None,
    ) -> None:
        super().__init__(title, parent)
        message = QLabel(text)
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setWordWrap(True)
        message.setFixedWidth(420)
        message.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.body.addWidget(message)
        self.field = field
        if field is not None:
            self.body.addWidget(field)
            field.returnPressed.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        if no is not None:
            cancel = NeonButton(no)
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
        accept = NeonButton(yes, primary=True)
        accept.clicked.connect(self.accept)
        buttons.addWidget(accept)
        self.body.addSpacing(6)
        self.body.addLayout(buttons)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.reject)
        (field or accept).setFocus()


def confirm(near: QWidget | None, title: str, text: str, yes: str = "Yes", no: str = "No") -> bool:
    dialog = MessageDialog(title, text, near, yes, no)
    place(dialog, near)
    return dialog.exec() == QDialog.DialogCode.Accepted


def inform(near: QWidget | None, title: str, text: str) -> None:
    dialog = MessageDialog(title, text, near, "OK", None)
    place(dialog, near)
    dialog.exec()


def ask_text(
    near: QWidget | None,
    title: str,
    text: str,
    value: str = "",
    password: bool = False,
    max_length: int = 400,
) -> str | None:
    """One line of text, or None when cancelled."""
    field = QLineEdit(value)
    field.setMaxLength(max_length)
    if password:
        field.setEchoMode(QLineEdit.EchoMode.Password)
    dialog = MessageDialog(title, text, near, "Save", "Cancel", field)
    place(dialog, near)
    return field.text() if dialog.exec() == QDialog.DialogCode.Accepted else None
