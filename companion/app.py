"""The desktop window: the character, a speech bubble and a text box."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import replace
from typing import Callable, Iterator

from PySide6.QtCore import QEvent, QLockFile, QObject, QPoint, QProcess, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import cloud, storage, theme, web
from .character import CharacterView
from .chat import (
    LOCAL_SITUATION,
    MAX_USER_CHARACTERS,
    Conversation,
    ReplyStats,
    leading_mood,
    stream_reply,
    visible_text,
)
from .config import (
    ASSETS,
    CLAUDE_RELOAD,
    DATA,
    LOCAL_RELOAD,
    PERSONALITY_FILE,
    ROOT,
    VOICE_RELOAD,
    Settings,
    fit_history,
    load_personality,
    load_settings,
    save_settings,
)
from .effects import EFFECTS, PITCHES
from .engine import Engine
from .settings_window import BRAVE_KEY, CLAUDE_KEY, CLAUDE_MODELS, SettingsChange, SettingsWindow
from .theme import ACCENT, MUTED, TEXT
from .voice import PREVIEW, VOICES, Speaker, SpeechChunker, reveal_position

NAME = "Capri"
PANEL = QColor(18, 16, 26, 238)
BUBBLE_WIDTH = 330
BUBBLE_AREA = 250  # space kept free above the character for the speech bubble
INPUT_WIDTH = 290
INPUT_HEIGHT = 36
CONNECTING = "Connecting to Claude…"
WAKING = "Waking up…"
LOCAL_ON = "Okay, I'm using my local brain now. Nothing leaves this PC unless you use 🌐."


class EngineStarter(QThread):
    """Loads the model, then sends one tiny hidden request so the first real reply is fast."""

    status = Signal(str)
    ready = Signal(float, str)
    failed = Signal(str)

    def __init__(self, engine: Engine, warmup: list[dict], settings: Settings) -> None:
        super().__init__()
        self.engine, self.warmup, self.settings = engine, warmup, settings
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _attempt(self) -> None:
        self.engine.start()
        self.engine.wait_until_ready(cancelled=lambda: self._stop)
        self.status.emit("Almost awake…")
        # Read the prompt once and discard the one-token answer.
        for _piece in stream_reply(
            self.engine, self.warmup, self.settings, ReplyStats(), lambda: self._stop, 1
        ):
            if self._stop:
                break

    def run(self) -> None:
        started = time.monotonic()
        try:
            try:
                self._attempt()
            except Exception:
                if self._stop or self.engine.device != "gpu":
                    raise
                self.status.emit("The graphics chip didn't cooperate, so I'm using the CPU instead…")
                self.engine.device = "cpu"
                self._attempt()
            if not self._stop:
                self.ready.emit(time.monotonic() - started, self.engine.device)
        except Exception as error:  # every failure must reach the window
            if not self._stop:
                self.failed.emit(str(error) or type(error).__name__)


class CloudStarter(QThread):
    """Connects to Claude in the background and checks the API key (a free request)."""

    ready = Signal(object, float)  # the cloud.Claude connection, and the seconds it took
    failed = Signal(str)

    def __init__(self, key: str, model: str) -> None:
        super().__init__()
        self.key, self.model = key, model
        self.claude: cloud.Claude | None = None
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        if self.claude is not None:
            self.claude.close()  # ends the check if it's still waiting

    def run(self) -> None:
        started = time.monotonic()
        try:
            self.claude = cloud.Claude(self.key)
            self.claude.check(self.model)
            if not self._stop:
                self.ready.emit(self.claude, time.monotonic() - started)
        except Exception as error:  # every failure must reach the window
            if not self._stop:
                self.failed.emit(str(error) or type(error).__name__)


# Writes a reply with one of the brains: (worker, prompt, stats) -> pieces of text.
Reply = Callable[["ReplyWorker", list[dict], ReplyStats], Iterator[str]]


class ReplyWorker(QThread):
    chunk = Signal(str)
    done = Signal(str, object)
    failed = Signal(str, str)
    looked_up = Signal(object)  # a web.Found, before the reply starts
    status = Signal(str)  # what Claude is doing, like "Searching the web: …"

    def __init__(self, reply: Reply, messages: list[dict], abort: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.reply, self.messages, self.abort = reply, messages, abort
        self.failure: Exception | None = None
        self._stop = False

    @property
    def stopped(self) -> bool:
        return self._stop

    def stop(self) -> None:
        self._stop = True
        if self.abort is not None:
            self.abort()  # ends a request that is waiting on the network

    def look_up(self, messages: list[dict], lookup: Callable[[], web.Found | None]) -> list[dict]:
        try:
            found = lookup()
        except web.LookupFailed as problem:
            found = web.failure_context(str(problem))
        return messages if found is None else self.attach(messages, found)

    def attach(self, messages: list[dict], found: web.Found) -> list[dict]:
        """Adds what was found online to the user's message, for this reply only."""
        self.looked_up.emit(found)
        last = messages[-1]
        return [*messages[:-1], {**last, "content": web.attach(last["content"], found)}]

    def run(self) -> None:
        stats = ReplyStats()
        raw = ""
        try:
            for piece in self.reply(self, self.messages, stats):
                raw += piece
                self.chunk.emit(piece)
            self.done.emit(raw, stats)  # also when stopped: what she wrote so far
        except Exception as error:  # every failure must reach the window
            if self._stop:
                self.done.emit(raw, stats)
                return
            self.failure = error
            self.failed.emit(str(error) or type(error).__name__, raw)


class SpeechBubble(QWidget):
    TAIL = 12
    RADIUS = 14
    MAX_TEXT_HEIGHT = 190

    def __init__(self, font: QFont, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(BUBBLE_WIDTH)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)  # never render model output as HTML
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setFont(font)
        self.label.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { width: 6px; background: transparent; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {ACCENT}; border-radius: 3px; min-height: 20px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
        )
        self.stats = QLabel()
        self.stats.setTextFormat(Qt.TextFormat.PlainText)
        self.stats.setWordWrap(True)
        small = QFont(font)
        small.setPointSizeF(max(7.5, font.pointSizeF() - 2))
        self.stats.setFont(small)
        self.stats.setStyleSheet(f"color: {MUTED}; background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 11, 12, 11 + self.TAIL)
        layout.setSpacing(4)
        layout.addWidget(self.scroll)
        layout.addWidget(self.stats)
        self.hide()

    def show_text(self, text: str, stats: str = "") -> None:
        self.label.setText(text)
        self.stats.setText(stats)
        self.stats.setVisible(bool(stats))
        inner = BUBBLE_WIDTH - 16 - 12 - 8
        needed = max(self.label.heightForWidth(inner), self.label.fontMetrics().height())
        # The space allows one line of notes; each extra line comes out of the text area.
        extra = self.stats.heightForWidth(inner) - self.stats.fontMetrics().height() if stats else 0
        self.scroll.setFixedHeight(min(needed + 2, max(60, self.MAX_TEXT_HEIGHT - max(0, extra))))
        self.adjustSize()
        self.show()
        bar = self.scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def set_note(self, note: str) -> None:
        """Replaces the small line under the text, keeping the text."""
        self.show_text(self.label.text(), note)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5 - self.TAIL)
        shape = QPainterPath()
        shape.addRoundedRect(body, self.RADIUS, self.RADIUS)
        tail = QPainterPath()
        middle = body.center().x()
        tail.moveTo(middle - 10, body.bottom() - 1)
        tail.lineTo(middle, body.bottom() + self.TAIL)
        tail.lineTo(middle + 10, body.bottom() - 1)
        tail.closeSubpath()
        shape = shape.united(tail)
        painter.fillPath(shape, PANEL)
        painter.setPen(QPen(QColor(ACCENT), 1.6))
        painter.drawPath(shape)


class CompanionWindow(QWidget):
    def __init__(self, settings: Settings, notes: list[str]) -> None:
        super().__init__(None, self._flags_for(settings))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(NAME)
        self.settings = settings
        self.engine = Engine(settings)
        self.starter: EngineStarter | None = None
        self.worker: ReplyWorker | None = None
        self.cloud: cloud.Claude | None = None  # the online brain, once connected
        self.cloud_starter: CloudStarter | None = None
        self.ready = False  # she can take a message
        self.local_ready = False  # the local model is loaded and warmed up
        self.greeted = False
        self.reply_online = False  # the current reply comes from Claude
        self.reply_look_up = False
        self.waiting_for_local = False  # Claude couldn't answer; the local brain will
        self.fallback_note = ""
        self.wake_note = ""  # what she says once awake again after saving settings
        self.settings_window: SettingsWindow | None = None
        self.quitting = False
        self.reply_raw = ""
        self.pending_text = ""
        self._press: QPoint | None = None
        self._origin = QPoint()
        self._dragging = False

        messages, history_note = storage.load_history()
        if history_note:
            notes.append(history_note)
        self.conversation = Conversation(messages)
        self.personality = load_personality()

        self.speaker = Speaker(settings)
        self.speaker.speaking.connect(self.on_speaking)
        self.speaker.idle.connect(self.on_speech_idle)
        self.speaker.problem.connect(self.on_voice_problem)
        self.chunker: SpeechChunker | None = None  # set while a reply is being spoken
        self.speech_generation = 0
        self.reply_complete = True
        self.reply_mood: str | None = None
        # Text that appears as she speaks: the reply so far, and how much is shown.
        self.synced = False
        self.reply_text = ""
        self.revealed = 0
        self.queued_end = 0  # end of the last part of the reply given to her voice
        self.pending_stats = ""
        self.last_reveal = 0.0
        self.reply_sources: list[str] = []  # where a looked-up answer came from
        self.reply_note = ""
        self.user_stopped = False

        font = self._load_font()
        self.bubble = SpeechBubble(font)
        self.character = CharacterView(settings.character_height)
        self.character.installEventFilter(self)
        self.input = QLineEdit()
        self.input.setFont(font)
        self.input.setMaxLength(MAX_USER_CHARACTERS)
        self.input.setFixedSize(INPUT_WIDTH - INPUT_HEIGHT - 6, INPUT_HEIGHT)
        self.input.installEventFilter(self)  # Ctrl+Enter: send with a lookup
        self.web_button = QToolButton()
        self.web_button.setText("🌐")
        self.web_button.setCheckable(True)
        self.web_button.setFixedSize(INPUT_HEIGHT, INPUT_HEIGHT)
        self.web_button.setStyleSheet(
            f"QToolButton {{ background: rgba(18,16,26,235); color: {TEXT}; font-size: 15px;"
            f" border: 1.5px solid #5a2a36; border-radius: 12px; }}"
            f"QToolButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}"
        )
        self.web_button.toggled.connect(self._on_web_toggled)
        self.input.setStyleSheet(
            f"QLineEdit {{ background: rgba(18,16,26,235); color: {TEXT}; border: 1.5px solid {ACCENT};"
            f" border-radius: 12px; padding: 4px 10px; selection-background-color: {ACCENT}; }}"
            f"QLineEdit:disabled {{ color: {MUTED}; border-color: #5a2a36; }}"
        )
        self.input.returnPressed.connect(self.send)

        # In window mode: a title bar to drag, minimize and close her window.
        self.title_bar = theme.TitleBar(NAME, minimizable=True)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.close_clicked.connect(self._close_window)
        self.title_bar.close_button.setToolTip(
            "Hide (find me in the system tray)" if QSystemTrayIcon.isSystemTrayAvailable() else "Quit"
        )
        self.title_bar.moved.connect(lambda: storage.save_window_position(self.x(), self.y()))
        bubble_area = QWidget()
        bubble_area.setFixedHeight(BUBBLE_AREA)
        bubble_layout = QVBoxLayout(bubble_area)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.addStretch(1)
        bubble_layout.addWidget(self.bubble, 0, Qt.AlignmentFlag.AlignHCenter)
        self.content = QVBoxLayout()
        self.content.setSpacing(2)
        self.content.addWidget(bubble_area)
        self.content.addWidget(self.character, 0, Qt.AlignmentFlag.AlignHCenter)
        input_row = QWidget()
        row_layout = QHBoxLayout(input_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addWidget(self.input)
        row_layout.addWidget(self.web_button)
        self.content.addWidget(input_row, 0, Qt.AlignmentFlag.AlignHCenter)
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addLayout(self.content)
        self._layout_for_mode()
        self._fit_size()
        self._place()

        self.hide_bubble = QTimer(self, singleShot=True)
        self.hide_bubble.timeout.connect(self.bubble.hide)
        self.lip_timer = QTimer(self, interval=50)
        self.lip_timer.timeout.connect(self._on_speech_tick)
        self.dots = QTimer(self, interval=400)  # "she's about to speak"
        self.dots.timeout.connect(self._dots_step)
        self._dot_count = 0
        self.sync_watchdog = QTimer(self, interval=1000)
        self.sync_watchdog.timeout.connect(self._check_sync)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.stop_reply)
        self.tray = self._build_tray()
        self.menu = self._build_menu()

        self.character.sleeping()
        self.input.setEnabled(False)
        self.input.setPlaceholderText(WAKING)
        key = storage.load_claude_key() if settings.brain == "claude" else None
        if settings.brain == "claude" and not key:
            settings.brain = "local"
            save_settings(settings)
            notes.append("No Claude API key is saved, so I'm using my local brain.")
        self._update_web_tooltip()
        self.say("\n".join(["Waking up… one moment while I load my brain.", *notes]))
        if key:
            self.start_cloud(key)
        else:
            self.start_engine()

    # Setup -----------------------------------------------------------------

    @staticmethod
    def _load_font() -> QFont:
        return theme.font(10.5)

    @staticmethod
    def _flags_for(settings: Settings) -> Qt.WindowType:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        if settings.window_mode == "window":  # a taskbar button, so she can be minimized
            flags |= Qt.WindowType.Window | Qt.WindowType.WindowMinimizeButtonHint
        else:
            flags |= Qt.WindowType.Tool
        if settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _windowed(self) -> bool:
        return self.settings.window_mode == "window"

    def _layout_for_mode(self) -> None:
        windowed = self._windowed()
        self.title_bar.setVisible(windowed)
        self.layout().setContentsMargins(*((2, 2, 2, 2) if windowed else (4, 4, 4, 4)))
        self.content.setContentsMargins(*((14, 4, 14, 16) if windowed else (0, 0, 0, 0)))

    def _fit_size(self) -> None:
        outer, inner = self.layout().contentsMargins(), self.content.contentsMargins()
        width = max(self.character.width(), BUBBLE_WIDTH, INPUT_WIDTH)
        width += outer.left() + outer.right() + inner.left() + inner.right()
        height = BUBBLE_AREA + self.character.height() + INPUT_HEIGHT + 2 * self.content.spacing()
        height += outer.top() + outer.bottom() + inner.top() + inner.bottom()
        if self._windowed():
            height += theme.TitleBar.HEIGHT
        self.setFixedSize(width, height)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._windowed():
            theme.paint_frame(self, theme.TitleBar.HEIGHT)

    def _resize_character(self, height: int) -> None:
        """A new size for her, keeping her feet where they were."""
        self._refit(lambda: self.character.set_height(height))

    def _refit(self, change: Callable[[], None]) -> None:
        """Changes her size or look, keeping her feet where they were."""
        bottom, middle = self.geometry().bottom(), self.geometry().center().x()
        change()
        self._fit_size()
        x, y = middle - self.width() // 2, bottom - self.height() + 1
        screen = QGuiApplication.screenAt(QPoint(middle, bottom)) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(x, area.right() - self.width() + 1))
        y = max(area.top(), min(y, area.bottom() - self.height() + 1))
        self.move(x, y)
        storage.save_window_position(x, y)

    def _place(self) -> None:
        saved = storage.load_window_position()
        if saved is not None:
            x, y = saved
            if QGuiApplication.screenAt(QPoint(x + self.width() // 2, y + self.height() // 2)):
                self.move(x, y)
                return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 8)

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("New conversation", self.new_conversation)
        menu.addAction("Settings…", self.open_settings)
        menu.addAction("Edit personality…", lambda: self._open_file(PERSONALITY_FILE))
        menu.addMenu(self._build_brain_menu(menu))
        menu.addMenu(self._build_voice_menu(menu))
        menu.addMenu(self._build_internet_menu(menu))
        menu.addSeparator()
        self.on_top_action = QAction("Always on top", menu, checkable=True)
        self.on_top_action.setChecked(self.settings.always_on_top)
        self.on_top_action.toggled.connect(self.set_always_on_top)
        menu.addAction(self.on_top_action)
        self.window_action = QAction("Show in a window", menu, checkable=True)
        self.window_action.setChecked(self._windowed())
        self.window_action.toggled.connect(self.set_window_mode)
        menu.addAction(self.window_action)
        if self.tray is not None:
            menu.addAction("Hide (find me in the system tray)", self.hide)
        menu.addAction("Restart", self.restart)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        return menu

    def _build_internet_menu(self, parent: QMenu) -> QMenu:
        menu = QMenu("Internet", parent)
        self.web_search_status = menu.addAction("")
        self.web_weather_status = menu.addAction("")
        for status in (self.web_search_status, self.web_weather_status):
            status.setEnabled(False)  # information only
        menu.addSeparator()
        menu.addAction("Set web search key (Brave)…", self.set_brave_key)
        menu.addAction("Remove web search key", self.remove_brave_key)
        menu.addAction("Weather location…", self.set_weather_location)
        menu.aboutToShow.connect(self._update_internet_status)
        return menu

    def _update_internet_status(self) -> None:
        has_key = storage.BRAVE_KEY_FILE.is_file()
        if self._online():
            text = "Web search: Claude's own search (Claude brain)"
        elif has_key:
            text = "Web search: Brave (key saved)"
        else:
            text = "Web search: Wikipedia only (no Brave key)"
        self.web_search_status.setText(text)
        self.web_weather_status.setText(f"Weather location: {self.settings.web_location or 'not set'}")

    def _build_voice_menu(self, parent: QMenu) -> QMenu:
        menu = QMenu("Voice", parent)
        self.voice_action = QAction("Speak replies", menu, checkable=True)
        self.voice_action.setChecked(self.settings.voice_enabled)
        self.voice_action.toggled.connect(self.set_voice_enabled)
        menu.addAction(self.voice_action)
        self.sync_action = QAction("Show text as she speaks", menu, checkable=True)
        self.sync_action.setChecked(self.settings.text_with_voice)
        self.sync_action.toggled.connect(self.set_text_with_voice)
        menu.addAction(self.sync_action)
        menu.addSeparator()
        self.voice_choices = self._add_choices(menu, VOICES, self.settings.voice, self.choose_voice)
        menu.addSeparator()
        effect_menu = menu.addMenu("Robot effect")
        labels = {label: value for value, label in EFFECTS.items()}
        self.effect_choices = self._add_choices(
            effect_menu, labels, self.settings.voice_effect, self.choose_effect
        )
        pitch_menu = menu.addMenu("Pitch")
        self.pitch_choices = self._add_choices(
            pitch_menu, PITCHES, self.settings.voice_pitch, self.choose_pitch
        )
        menu.aboutToShow.connect(self._update_voice_menu)
        return menu

    def _update_voice_menu(self) -> None:
        for action, on in ((self.voice_action, self.settings.voice_enabled), (self.sync_action, self.settings.text_with_voice)):
            action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(False)
        for group, current in (
            (self.voice_choices, self.settings.voice),
            (self.effect_choices, self.settings.voice_effect),
            (self.pitch_choices, self.settings.voice_pitch),
        ):
            self._tick(group, current)

    @staticmethod
    def _tick(group: QActionGroup, current) -> None:
        """Ticks the option that matches the setting, or none when it's a custom value."""
        group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        for action in group.actions():
            action.setChecked(action.data() == current)
        group.setExclusionPolicy(QActionGroup.ExclusionPolicy.Exclusive)

    def _build_brain_menu(self, parent: QMenu) -> QMenu:
        menu = QMenu("Brain", parent)
        self.brain_status = menu.addAction("")
        self.brain_status.setEnabled(False)  # information only
        menu.addSeparator()
        brains = {"Local (private, on this PC)": "local", "Claude (online, your API key)": "claude"}
        self.brain_choices = self._add_choices(menu, brains, self.settings.brain, self.choose_brain)
        model_menu = menu.addMenu("Claude model")
        self.model_choices = self._add_choices(
            model_menu, CLAUDE_MODELS, self.settings.claude_model, self.choose_claude_model
        )
        search_menu = menu.addMenu("Claude searches the web")
        modes = {"Only when I ask (🌐 or Ctrl+Enter)": "globe", "Whenever it helps": "auto"}
        self.search_choices = self._add_choices(
            search_menu, modes, self.settings.claude_search, self.choose_claude_search
        )
        menu.addSeparator()
        menu.addAction("Set Claude API key…", self.set_claude_key)
        menu.addAction("Remove Claude API key", self.remove_claude_key)
        menu.aboutToShow.connect(self._update_brain_menu)
        return menu

    def _update_brain_menu(self) -> None:
        for group, current in (
            (self.brain_choices, self.settings.brain),
            (self.model_choices, self.settings.claude_model),
            (self.search_choices, self.settings.claude_search),
        ):
            self._tick(group, current)
        if self._online():
            label = cloud.MODELS[self.settings.claude_model].label
            text = f"Using {label} · this session {cloud.dollars(self.cloud.session_cost)}"
        elif self.cloud_starter is not None:
            text = CONNECTING
        elif self.settings.brain == "claude":
            text = "Claude isn't connected; using the local brain"
        else:
            text = "Using the local brain (nothing leaves this PC)"
        self.brain_status.setText(text)

    @staticmethod
    def _add_choices(menu: QMenu, choices: dict, current, choose) -> QActionGroup:
        """Adds a group of options where exactly one is ticked."""
        group = QActionGroup(menu)
        for label, value in choices.items():
            action = QAction(label, menu, checkable=True)
            action.setData(value)
            action.setChecked(value == current)
            action.triggered.connect(lambda _checked=False, v=value: choose(v))
            group.addAction(action)
            menu.addAction(action)
        return group

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(QIcon(_icon_file()), self)
        tray.setToolTip(NAME)
        menu = QMenu()
        menu.addAction("Show / hide", self.toggle_visible)
        menu.addAction("Settings…", self.open_settings)
        menu.addAction("New conversation", self.new_conversation)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self.toggle_visible()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        tray.show()
        self._tray_menu = menu
        return tray

    # Engine ----------------------------------------------------------------

    def start_engine(self) -> None:
        warmup = self.conversation.prompt(self.personality, self.settings.history_tokens)
        warmup.append({"role": "user", "content": "Hi"})
        starter = self.starter = EngineStarter(self.engine, warmup, self.settings)
        # Only the current starter may report: one cancelled for new settings stays quiet.
        starter.status.connect(lambda text: starter is self.starter and self.say(text))
        starter.ready.connect(lambda *args: starter is self.starter and self.on_ready(*args))
        starter.failed.connect(lambda message: starter is self.starter and self.on_engine_failed(message))
        starter.start()

    def _release_starter(self) -> None:
        # Wait for the thread to fully end before dropping it; Qt crashes otherwise.
        starter, self.starter = self.starter, None
        if starter is not None:
            starter.wait(5000)

    def _release_worker(self) -> None:
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.wait(5000)

    def on_ready(self, seconds: float, device: str) -> None:
        self._release_starter()
        self.local_ready = True
        if self.waiting_for_local:  # Claude couldn't answer the last message
            self.waiting_for_local = False
            self.ready = True
            self.input.setEnabled(True)
            self._start_reply(self.reply_look_up, online=False)
            return
        if self._online():
            self._unload_local()  # switched to Claude while this was starting
            return
        where = "graphics chip" if device == "gpu" else "CPU"
        self._awake(
            f"ready in {seconds:.0f} s · running on the {where}",
            LOCAL_ON,
        )

    def _awake(self, stats: str, switched: str) -> None:
        """She can talk: enables the text box and says hello, or what changed."""
        self.ready = True
        self.input.setEnabled(True)
        self.input.setPlaceholderText(self._idle_placeholder())
        self._update_web_tooltip()
        if self.greeted:
            text = self.wake_note or switched
        elif self.conversation.messages:
            text = "Welcome back! I'm awake."
        else:
            text = (
                f"Hi! I'm {NAME}. Type below to talk to me.\n"
                "Drag me to move me around, and right-click me for options."
            )
        self.greeted = True
        self.wake_note = ""
        self.say(text, stats)
        self.character.react("happy", seconds=4)
        self.hide_bubble.start(self.settings.bubble_seconds * 1000)
        self.input.setFocus()
        if self.settings.voice_enabled:
            self.speaker.preload()  # after the chat model, so they don't compete while loading

    def on_engine_failed(self, message: str) -> None:
        self._release_starter()
        self.local_ready = False
        self.wake_note = ""
        if self.waiting_for_local:
            self.waiting_for_local = False
            self._undo_last_message()
        if self._online():  # Claude still works
            self.ready = True
            self.input.setEnabled(True)
            self.input.setPlaceholderText(self._idle_placeholder())
            self.character.react("concerned", seconds=6)
            self.say(f"My local brain couldn't start.\n{message}")
            return
        self.ready = False
        self.character.react("concerned", seconds=3600)
        self.input.setPlaceholderText("My AI engine isn't running")
        self.say(f"I couldn't start my AI engine.\n{message}\nRight-click me and choose Restart to try again.")

    def _unload_local(self) -> None:
        """Stops the local model to free its memory (about 4 GB) while Claude is her brain."""
        if self.starter is None and self.worker is None and not self.waiting_for_local:
            self.engine.stop()
            self.local_ready = False

    # Claude ----------------------------------------------------------------

    def _online(self) -> bool:
        """Claude is her brain and connected."""
        return self.settings.brain == "claude" and self.cloud is not None

    def start_cloud(self, key: str) -> None:
        if self.cloud_starter is not None:
            return
        starter = self.cloud_starter = CloudStarter(key, self.settings.claude_model)
        starter.ready.connect(lambda *args: self.on_cloud_ready(*args) if starter is self.cloud_starter else args[0].close())
        starter.failed.connect(lambda message: starter is self.cloud_starter and self.on_cloud_failed(message))
        starter.start()

    def _release_cloud_starter(self) -> None:
        starter, self.cloud_starter = self.cloud_starter, None
        if starter is not None:
            starter.wait(5000)

    def on_cloud_ready(self, claude: cloud.Claude, seconds: float) -> None:
        self._release_cloud_starter()
        if self.cloud is not None and self.worker is None:
            self.cloud.close()
        self.cloud = claude
        if self.settings.brain != "claude":
            return  # switched back to the local brain meanwhile
        label = cloud.MODELS[self.settings.claude_model].label
        self._awake(
            f"ready in {seconds:.1f} s · {label}, online",
            "Okay, Claude is my brain now. Your messages, my personality and our recent "
            "conversation go to Anthropic while it's on.",
        )
        self._unload_local()

    def on_cloud_failed(self, message: str) -> None:
        self._release_cloud_starter()
        self.wake_note = ""
        self.character.react("concerned", seconds=6)
        if self.local_ready:
            self.say(f"I couldn't connect to Claude.\n{message}\nI'll keep using my local brain for now.")
            return
        self.say(
            f"I couldn't connect to Claude.\n{message}\n"
            "I'll use my local brain instead. It needs about half a minute to wake up…"
        )
        if self.starter is None:
            self.start_engine()

    def _answer_locally(self, problem: str) -> None:
        """Claude couldn't answer, so the local brain does, waking up first if needed."""
        self.fallback_note = f"My local brain answered. {problem}"
        if self.local_ready:
            self._start_reply(self.reply_look_up, online=False)
            return
        self.waiting_for_local = True
        self.ready = False
        self.input.setEnabled(False)
        self.input.setPlaceholderText("Waking up my local brain…")
        self.character.thinking()
        self.say(f"{problem}\nI'll answer with my local brain instead. It needs about half a minute to wake up…")
        if self.starter is None:
            self.start_engine()

    # Talking ---------------------------------------------------------------

    def say(self, text: str, stats: str = "") -> None:
        self.hide_bubble.stop()
        self.bubble.show_text(text, stats)

    def send(self) -> None:
        text = self.input.text().strip()
        if not text or not self.ready or self.worker is not None:
            return
        online = self._online()
        if not online and not self.local_ready:
            return
        self.input.clear()
        look_up = self.web_button.isChecked()
        self.web_button.setChecked(False)  # a lookup is for this one message only
        self.pending_text = text
        self.conversation.add("user", text)
        self.personality = load_personality()  # edits to personality.txt apply right away
        self.fallback_note = ""
        self._start_reply(look_up, online)

    def _start_reply(self, look_up: bool, online: bool) -> None:
        """Asks a brain to reply to the conversation, which ends with the user's message."""
        self.reply_look_up, self.reply_online = look_up, online
        situation = cloud.situation(self.settings.claude_search) if online else LOCAL_SITUATION
        messages = self.conversation.prompt(
            self.personality, self.settings.history_tokens, situation, lookups=online
        )
        self.reply_raw = ""
        self._silence()
        if self.settings.voice_enabled:
            self.chunker = SpeechChunker()
        self.synced = self.settings.voice_enabled and self.settings.text_with_voice
        self.reply_text, self.revealed, self.queued_end, self.pending_stats = "", 0, 0, ""
        self.reply_sources, self.reply_note, self.user_stopped = [], "", False
        self.reply_complete = False
        self.reply_mood = None
        self.character.thinking()
        self.say("Looking that up online…" if look_up else "…")
        if self.synced:
            self.last_reveal = time.monotonic()
            self.sync_watchdog.start()
            if not look_up:
                self.dots.start()  # after a lookup, the dots start once results arrive
        self.input.setPlaceholderText(f"{NAME} is replying… (Esc to stop)")
        if online:
            reply, abort = self._claude_reply(look_up, self.pending_text), self.cloud.abort
        else:
            reply, abort = self._local_reply(look_up, self.pending_text), None
        self.worker = ReplyWorker(reply, messages, abort)
        self.worker.chunk.connect(self.on_chunk)
        self.worker.looked_up.connect(self.on_looked_up)
        self.worker.status.connect(self.on_status)
        self.worker.done.connect(self.on_reply)
        self.worker.failed.connect(self.on_reply_failed)
        self.worker.start()

    def _local_reply(self, look_up: bool, text: str) -> Reply:
        engine, settings = self.engine, self.settings
        if look_up:
            lookup = self._lookup_for(text)
        else:  # the weather needs no globe: only the place name goes to Open-Meteo
            query = web.weather_query(text, self._previous_user_text(), settings.web_location)
            lookup = self._weather_for(query) if query else None

        def reply(worker: ReplyWorker, messages: list[dict], stats: ReplyStats) -> Iterator[str]:
            if lookup is not None:
                if not look_up:
                    worker.status.emit("Checking the weather…")
                messages = worker.look_up(messages, lookup)
            if not worker.stopped:
                yield from stream_reply(engine, messages, settings, stats, lambda: worker.stopped)

        return reply

    def _claude_reply(self, look_up: bool, text: str) -> Reply:
        claude, settings = self.cloud, self.settings
        place, anytime = settings.web_location, settings.claude_search == "auto"
        model, most = settings.claude_model, settings.claude_max_searches

        def reply(worker: ReplyWorker, messages: list[dict], stats: ReplyStats) -> Iterator[str]:
            private = web.looks_private(text)  # such a message is never searched
            if look_up:
                messages = (
                    worker.attach(messages, cloud.private_refusal())
                    if private
                    else cloud.with_note(messages, cloud.SEARCH_NOTE)
                )
            search = not private and (look_up or anytime)
            if not worker.stopped:  # weather needs no lookup: Claude has a weather tool
                yield from claude.stream_reply(
                    messages,
                    cloud.Request(model, search, most, home=place),
                    stats,
                    lambda: worker.stopped,
                    worker.status.emit,
                )

        return reply

    def _previous_user_text(self) -> str:
        """What the user said before their newest message (for weather follow-ups)."""
        earlier = [m["content"] for m in self.conversation.messages[:-1] if m["role"] == "user"]
        return earlier[-1] if earlier else ""

    def _weather_for(self, query: str) -> Callable[[], web.Found | None]:
        place = self.settings.web_location
        return lambda: None if web.looks_private(query) else web.weather_lookup(query, place)

    def _lookup_for(self, text: str) -> Callable[[], web.Found]:
        """The lookup the worker runs in the background: only this message is used."""
        key, settings = storage.load_brave_key(), self.settings
        return lambda: web.lookup(
            text,
            brave_key=key,
            default_place=settings.web_location,
            count=settings.web_results,
            safesearch=settings.web_safesearch,
        )

    def on_looked_up(self, found: web.Found) -> None:
        self.reply_sources, self.reply_note = found.sources, found.note
        self.last_reveal = time.monotonic()  # the watchdog counts from here
        if self.synced:
            self.dots.start()
        else:
            self.say("…")

    def on_status(self, note: str) -> None:
        self.last_reveal = time.monotonic()  # a search takes a few seconds
        self.bubble.set_note(note)

    def on_chunk(self, piece: str) -> None:
        self.reply_raw += piece
        text = visible_text(self.reply_raw, finished=False)
        if not text:
            return
        self.reply_text = text
        if self.chunker is None:
            self.bubble.show_text(text)
            self.character.talking()  # no voice: animate the mouth while text appears
            return
        if not self.synced:
            self.bubble.show_text(text)
        self.reply_mood = leading_mood(self.reply_raw)
        if self.character.state not in ("holding", "speaking"):
            self.character.hold(self.reply_mood)  # her voice follows in a moment
        self._speak(self.chunker.feed(text))

    def _speak(self, pieces) -> None:
        for piece in pieces:
            self.speaker.say(piece.text, (piece.start, piece.end))
            self.queued_end = piece.end

    def on_reply(self, raw: str, stats: ReplyStats) -> None:
        self._release_worker()
        self.input.setPlaceholderText(self._idle_placeholder())
        text = visible_text(raw)
        if not text:
            self._silence()
            self._undo_last_message()
            self.character.idle()
            self.say(
                "Okay, never mind."
                if self.user_stopped
                else "…I lost my train of thought. Could you say that again?"
            )
            return
        lines, lookups = self._reply_notes(stats)
        # The looked-up text itself is never saved; only a short record of what was looked up.
        self.conversation.add("assistant", raw, lookups)
        if self.reply_online:
            self._unload_local()  # woken while Claude was unreachable; Claude is back
        try:
            storage.save_history(self.conversation.messages)
        except OSError:
            lines.append("I couldn't save our conversation to disk.")
        self.pending_stats = "\n".join(lines)
        self.reply_text = text
        self.reply_mood = leading_mood(raw)
        self.reply_complete = True
        if self.chunker is not None:
            self._speak(self.chunker.finish(text))
        if self.synced and self.speaker.busy:
            return  # the words keep appearing as she speaks; on_speech_idle finishes up
        self._finish_reply()

    def _reply_notes(self, stats: ReplyStats) -> tuple[list[str], str]:
        """The small lines under a reply, and a record of what it looked up."""
        lines = []
        lookups = stats.extra.get("lookups", "")
        if stats.extra.get("brain") == "claude":
            self.reply_sources = self.reply_sources or stats.extra["sources"]
            self.reply_note = self.reply_note or stats.extra["note"]
            if self.settings.show_stats:
                lines.append(cloud.summary(stats))
        elif self.settings.show_stats and stats.reply_tokens:
            lines.append(
                f"first word {stats.first_token_seconds:.1f} s · "
                f"{stats.tokens_per_second:.1f} tokens/s"
            )
        if self.reply_sources:
            lines.append("Looked up: " + ", ".join(self.reply_sources))
            lookups = lookups or "an online lookup (" + ", ".join(self.reply_sources) + ")"
        lines += [note for note in (self.reply_note, self.fallback_note) if note]
        return lines, lookups

    def _finish_reply(self) -> None:
        """Shows the whole reply with its stats, and lets her react once she's quiet."""
        self._stop_sync()
        self.revealed = len(self.reply_text)
        self.say(self.reply_text, self.pending_stats)
        if not self.speaker.busy:
            self.character.react(self.reply_mood, seconds=8)  # otherwise when she stops talking
        self.hide_bubble.start(self.settings.bubble_seconds * 1000)

    # Text that appears as she speaks -----------------------------------------

    def _on_speech_tick(self) -> None:
        self.character.lip_sync(self.speaker.level)
        progress = self.speaker.progress
        if not self.synced or progress is None or progress[0] != self.speech_generation:
            return
        _generation, span, fraction = progress
        if isinstance(span, tuple):
            self._reveal_to(reveal_position(self.reply_text, span[0], span[1], fraction))

    def _reveal_to(self, position: int) -> None:
        if position <= self.revealed:
            return
        self.dots.stop()
        self.revealed = position
        self.last_reveal = time.monotonic()
        self.bubble.show_text(self.reply_text[:position].rstrip())

    def _dots_step(self) -> None:
        self._dot_count = self._dot_count % 3 + 1
        self.bubble.show_text("." * self._dot_count)

    def _check_sync(self) -> None:
        """Never leave the text hidden: after 20 s without new words, show it all."""
        if self.synced and self.reply_text and time.monotonic() - self.last_reveal > 20:
            self._stop_sync()
            if self.reply_complete:
                self._finish_reply()
            else:
                self.bubble.show_text(self.reply_text)  # the rest appears as it's written

    def _stop_sync(self) -> None:
        """From here on, the text of this reply appears without waiting for her voice."""
        self.synced = False
        self.dots.stop()
        self.sync_watchdog.stop()

    def on_reply_failed(self, message: str, raw: str) -> None:
        failure = self.worker.failure if self.worker is not None else None
        self._release_worker()
        self._silence()
        if self.reply_online and not visible_text(raw) and getattr(failure, "local_can_answer", False):
            self._answer_locally(message)
            return
        if visible_text(raw):
            self.conversation.add("assistant", raw)
        else:
            self._undo_last_message()
        if self.reply_online:
            self.input.setPlaceholderText(self._idle_placeholder())
            self.character.react("concerned", seconds=6)
            self.say(f"Something went wrong with Claude.\n{message}")
            return
        if not self.engine.running:
            self.ready = False
            self.input.setPlaceholderText("My AI engine stopped")
        else:
            self.input.setPlaceholderText(self._idle_placeholder())
        self.character.react("concerned", seconds=6)
        self.say(f"Something went wrong with my AI engine.\n{message}\nIf it keeps happening, right-click me and choose Restart.")

    def _undo_last_message(self) -> None:
        if self.conversation.messages and self.conversation.messages[-1]["role"] == "user":
            self.conversation.messages.pop()
            if not self.input.text():
                self.input.setText(self.pending_text)

    def stop_reply(self) -> None:
        """Esc: stop writing and stop talking, and show the whole reply."""
        if self.worker is not None:
            self.user_stopped = True
            self.worker.stop()  # on_reply follows with what she wrote so far
        if self.speaker.busy or self.character.state in ("holding", "speaking"):
            self._silence()
            if self.reply_complete:
                self._finish_reply()
            else:
                self.character.react(self.reply_mood, seconds=4)

    def _silence(self) -> None:
        """Stops speech at once; the rest of the current reply stays unspoken."""
        self.speech_generation = self.speaker.stop()
        self.chunker = None
        self.lip_timer.stop()
        if self.synced:
            self._stop_sync()
            if self.reply_text:
                self.bubble.show_text(self.reply_text)  # nothing is left hidden

    # Voice -----------------------------------------------------------------

    def on_speaking(self, generation: int) -> None:
        if generation == self.speech_generation:
            self.last_reveal = time.monotonic()
            self.lip_timer.start()

    def on_speech_idle(self, generation: int) -> None:
        if generation != self.speech_generation:
            return
        self.lip_timer.stop()
        if self.synced:
            self._reveal_to(self.queued_end)  # everything she has said so far
            if self.reply_complete:
                self._finish_reply()
                return
        if self.reply_complete:
            self.character.react(self.reply_mood, seconds=8)
        else:
            self.character.hold(self.reply_mood)  # caught up with the text; more is coming

    def on_voice_problem(self, message: str) -> None:
        self._silence()
        self.settings.voice_enabled = False  # for this session; the setting file is unchanged
        self.voice_action.blockSignals(True)
        self.voice_action.setChecked(False)
        self.voice_action.blockSignals(False)
        if self.character.state in ("holding", "speaking"):
            self.character.react(self.reply_mood, seconds=4)
        self.bubble.set_note(message)

    def set_voice_enabled(self, enabled: bool) -> None:
        self.settings.voice_enabled = enabled
        save_settings(self.settings)
        if enabled:
            self.speaker.preload()
        elif self.worker is None:
            self.stop_reply()  # quiet now; she's not writing anything
        else:
            self._silence()  # the rest of the reply appears as text only

    def set_text_with_voice(self, enabled: bool) -> None:
        """Applies from the next reply; turning it off also shows the current one."""
        self.settings.text_with_voice = enabled
        save_settings(self.settings)
        if not enabled and self.synced:
            self._stop_sync()
            if self.reply_complete:
                self._finish_reply()
            else:
                self.bubble.show_text(self.reply_text)

    def choose_voice(self, voice: str) -> None:
        self.settings.voice = voice
        self.speaker.voice = voice
        save_settings(self.settings)
        self._preview()

    def choose_effect(self, effect: str) -> None:
        self.settings.voice_effect = effect
        save_settings(self.settings)
        self._preview()

    def choose_pitch(self, pitch: float) -> None:
        self.settings.voice_pitch = pitch
        save_settings(self.settings)
        self._preview()

    def _preview(self) -> None:
        """Says a short sample with the current voice settings."""
        if self.settings.voice_enabled and self.worker is None and self.ready:
            self._silence()
            self.reply_complete = True
            self.reply_mood = "happy"
            self.say(PREVIEW)
            self.hide_bubble.start(self.settings.bubble_seconds * 1000)
            self.speaker.say(PREVIEW)

    def new_conversation(self) -> None:
        if self.worker is not None:
            return
        if not theme.confirm(self, "New conversation", f"Start a new conversation? {NAME} will forget this one."):
            return
        self._silence()
        self.conversation.clear()
        storage.clear_history()
        self.character.react("happy", seconds=3)
        self.say("Okay, fresh start! What's up?")
        self.hide_bubble.start(self.settings.bubble_seconds * 1000)

    # Window behavior -------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.input and self._is_ctrl_enter(event):
            self.web_button.setChecked(True)
            self.send()
            return True
        if watched is self.character:
            handler = {
                QEvent.Type.MouseButtonPress: self._on_press,
                QEvent.Type.MouseMove: self._on_move,
                QEvent.Type.MouseButtonRelease: self._on_release,
                QEvent.Type.ContextMenu: self._on_context_menu,
            }.get(event.type())
            if handler is not None and handler(event):
                return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _is_ctrl_enter(event: QEvent) -> bool:
        return (
            event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        )

    def _on_web_toggled(self, _checked: bool) -> None:
        if self.worker is None and self.ready:
            self.input.setPlaceholderText(self._idle_placeholder())

    def _idle_placeholder(self) -> str:
        if self.web_button.isChecked():
            return "What should I look up?"
        return f"Talk to {NAME}… (Enter to send)"

    def _update_web_tooltip(self) -> None:
        if self.settings.brain == "claude":
            text = (
                "Let Claude search the web for my next message (or press Ctrl+Enter).\n"
                "Weather works without it: Claude checks Open-Meteo by itself."
            )
        else:
            text = (
                "Look up my next message online (or press Ctrl+Enter). Only that message is sent.\n"
                "Weather works without it: only the place name goes to Open-Meteo."
            )
        self.web_button.setToolTip(text)

    # Brain settings ----------------------------------------------------------

    CONSENT = (
        "With Claude as my brain, each message you send goes to Anthropic over the internet, "
        "together with my personality and our recent conversation. It's paid from your Claude "
        "API credit.\n\nOur saved conversation stays encrypted on this PC, and you can switch "
        "back to the local brain at any time.\n\nUse Claude?"
    )

    def _busy(self) -> bool:
        if self.worker is None and self.starter is None and self.cloud_starter is None and not self.waiting_for_local:
            return False
        self.say("Give me a moment, I'm still busy. Try again when I'm done.")
        return True

    def choose_brain(self, brain: str) -> None:
        if brain == self.settings.brain and (brain == "local" or self.cloud is not None):
            return
        if self._busy():
            return
        if brain == "claude":
            self._switch_to_claude()
        else:
            self._switch_to_local()

    def _switch_to_claude(self) -> None:
        if self.settings.brain != "claude":
            if not theme.confirm(self, "Use Claude", self.CONSENT):
                return
        key = storage.load_claude_key() or self._ask_claude_key()
        if not key:
            return
        self.settings.brain = "claude"
        save_settings(self.settings)
        self._update_web_tooltip()
        self.say(CONNECTING)
        self.start_cloud(key)  # on_cloud_ready takes it from there

    def _switch_to_local(self, intro: str = "") -> None:
        self.settings.brain = "local"
        save_settings(self.settings)
        self._update_web_tooltip()
        if self.local_ready:
            self.character.react("happy", seconds=3)
            self.say(intro + LOCAL_ON)
            return
        self.ready = False
        self.input.setEnabled(False)
        self.input.setPlaceholderText(WAKING)
        self.character.sleeping()
        self.say(intro + "Okay, switching to my local brain. It needs about half a minute to wake up…")
        if self.starter is None:
            self.start_engine()

    def choose_claude_model(self, model: str) -> None:
        self.settings.claude_model = model
        save_settings(self.settings)
        label = cloud.MODELS[model].label
        if self.settings.brain == "claude":
            self.say(f"Okay, {label} from your next message.")
        else:
            self.say(f"Okay, I'll use {label} when Claude is my brain.")

    def choose_claude_search(self, mode: str) -> None:
        self.settings.claude_search = mode
        save_settings(self.settings)
        self.say(
            "Okay, Claude can search the web whenever it helps. Each search costs about a cent."
            if mode == "auto"
            else "Okay, Claude only searches the web when you use 🌐 or Ctrl+Enter."
        )

    def _ask_claude_key(self) -> str | None:
        key = theme.ask_text(
            self,
            "Claude API key",
            "Paste your Claude API key (from the Claude Console; it starts with sk-ant-).\n"
            "It's stored encrypted for your Windows account and only ever sent to Anthropic.",
            password=True,
        )
        key = (key or "").strip()
        if not key:
            return None
        if not CLAUDE_KEY.fullmatch(key):
            self.say("That doesn't look like a Claude API key (they start with sk-ant-), so I didn't save it.")
            return None
        storage.save_claude_key(key)
        return key

    def set_claude_key(self) -> None:
        if self._busy():
            return
        key = self._ask_claude_key()
        if not key:
            return
        if self.cloud is not None:  # it still uses the old key
            self.cloud.close()
            self.cloud = None
        if self.settings.brain != "claude":
            self.say("Got it! Choose Brain → Claude when you want to use it.")
            return
        if not self.local_ready:
            self.ready = False
            self.input.setEnabled(False)
            self.input.setPlaceholderText(CONNECTING)
        self.say("Got it! Connecting to Claude with the new key…")
        self.start_cloud(key)

    def remove_claude_key(self) -> None:
        if not storage.CLAUDE_KEY_FILE.is_file():
            self.say("There's no Claude API key saved.")
            return
        if self._busy():
            return
        if not theme.confirm(self, "Claude API key", "Remove the saved Claude API key?"):
            return
        storage.clear_claude_key()
        if self.cloud is not None:
            self.cloud.close()
            self.cloud = None
        if self.settings.brain == "claude":
            self._switch_to_local("Removed the key. ")
        else:
            self.say("Removed the Claude API key.")

    # Internet settings -------------------------------------------------------

    def set_brave_key(self) -> None:
        key = theme.ask_text(
            self,
            "Web search key",
            "Paste your Brave Search API key.\n"
            "It's stored encrypted for your Windows account and only ever sent to Brave.",
            password=True,
        )
        key = (key or "").strip()
        if not key:
            return
        if not BRAVE_KEY.fullmatch(key):
            self.say("That doesn't look like a Brave Search API key, so I didn't save it.")
            return
        storage.save_brave_key(key)
        self.say("Got it! When you ask me to look something up, I'll use Brave Search.")
        self.hide_bubble.start(self.settings.bubble_seconds * 1000)

    def remove_brave_key(self) -> None:
        if not storage.BRAVE_KEY_FILE.is_file():
            self.say("There's no web search key saved.")
            return
        if theme.confirm(self, "Web search key", "Remove the saved Brave Search key?"):
            storage.clear_brave_key()
            self.say("Removed. For lookups I'll use Wikipedia now.")

    def set_weather_location(self) -> None:
        place = theme.ask_text(
            self,
            "Weather location",
            "Your city, for weather questions that don't name a place (leave empty to clear):",
            self.settings.web_location,
            max_length=80,
        )
        if place is None:
            return
        self.settings.web_location = " ".join(place.split())[:80]
        save_settings(self.settings)
        self.say(
            f"Okay, I'll use {self.settings.web_location} for the weather."
            if self.settings.web_location
            else "Okay, I'll ask where you mean when you ask about the weather."
        )

    def _on_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self._press = event.globalPosition().toPoint()
        self._origin = self.pos()
        self._dragging = False
        return True

    def _on_move(self, event) -> bool:
        if self._press is None:
            return False
        delta = event.globalPosition().toPoint() - self._press
        if not self._dragging and delta.manhattanLength() > 4:
            self._dragging = True
            self.character.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self.move(self._origin + delta)
        return True

    def _on_release(self, event) -> bool:
        if self._press is None:
            return False
        if self._dragging:
            storage.save_window_position(self.x(), self.y())
        else:
            self.character.poke()
            self.activateWindow()
            self.input.setFocus()
        self._press = None
        self._dragging = False
        self.character.setCursor(Qt.CursorShape.OpenHandCursor)
        return True

    def _on_context_menu(self, event) -> bool:
        self.menu.exec(event.globalPos())
        return True

    def set_always_on_top(self, enabled: bool) -> None:
        self.settings.always_on_top = enabled
        save_settings(self.settings)
        self._apply_flags()

    def set_window_mode(self, windowed: bool) -> None:
        """Shows her in a framed window, or right on the desktop."""
        self.settings.window_mode = "window" if windowed else "overlay"
        save_settings(self.settings)
        self._refit(self._layout_for_mode)
        self._apply_flags()
        self.update()

    def _apply_flags(self) -> None:
        visible = self.isVisible()
        self.setWindowFlags(self._flags_for(self.settings))  # this hides the window
        if visible:
            self.show()

    def _close_window(self) -> None:
        """The close button of her window: off to the system tray, or quit without a tray."""
        if self.tray is not None:
            self.hide()
        else:
            self.quit()

    def toggle_visible(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _open_file(self, path) -> None:
        try:
            os.startfile(path)  # opens in your default editor
        except OSError as error:
            self.say(f"I couldn't open {path.name}: {error}")

    # Settings window -----------------------------------------------------------

    def open_settings(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(
                self.settings,
                storage.CLAUDE_KEY_FILE.is_file(),
                storage.BRAVE_KEY_FILE.is_file(),
            )
            self.settings_window.saved.connect(self.apply_settings)
            self.settings_window.finished.connect(self._settings_closed)
        theme.present(self.settings_window, self)

    def _settings_closed(self) -> None:
        window, self.settings_window = self.settings_window, None
        if window is not None:
            window.deleteLater()

    def apply_settings(self, change: SettingsChange) -> None:
        """Applies what was saved in the settings window, reloading what needs it."""
        if self.quitting:
            return
        old = replace(self.settings)
        for name in change.changed:
            setattr(self.settings, name, getattr(change.settings, name))
        fit_history(self.settings)
        problems = []
        try:
            save_settings(self.settings)
        except OSError as error:
            problems.append(f"I couldn't write settings.json ({error}), so they only last until I restart.")
        try:
            self._save_keys(change)
        except OSError as error:
            problems.append(f"I couldn't save the API key: {error}")
        self._apply_now(old)
        if change.changed & VOICE_RELOAD:
            self.speaker.reload()
            self._silence()
            if self.settings.voice_enabled:
                self.speaker.preload()
        # Reload only the brain that's in use (or loaded); the other picks the settings up when it starts.
        on_claude = self.settings.brain == "claude"
        local_loaded = self.local_ready or self.starter is not None
        local = bool(change.changed & LOCAL_RELOAD) and (not on_claude or local_loaded)
        claude = "brain" in change.changed or (
            on_claude and (bool(change.changed & CLAUDE_RELOAD) or change.claude_key is not None)
        )
        if self.starter is None:
            self.engine.device = self.settings.device  # it may have fallen back to the CPU before
        if not local and not claude:
            if self.worker is None:  # otherwise her reply stays in the bubble
                self.character.react("happy", seconds=3)
                self.say("\n".join(["Saved! Your new settings are on.", *problems]))
                self.hide_bubble.start(self.settings.bubble_seconds * 1000)
            return
        if problems:
            self.bubble.set_note(" ".join(problems))
        if self.worker is not None:
            self.stop_reply()  # she reloads once this reply has wrapped up
        self._reload_when_idle(local, claude, "brain" in change.changed)

    @staticmethod
    def _save_keys(change: SettingsChange) -> None:
        if change.claude_key is not None:
            if change.claude_key:
                storage.save_claude_key(change.claude_key)
            else:
                storage.clear_claude_key()
        if change.brave_key is not None:
            if change.brave_key:
                storage.save_brave_key(change.brave_key)
            else:
                storage.clear_brave_key()

    def _apply_now(self, old: Settings) -> None:
        """Settings that take effect without loading anything again."""
        new = self.settings
        self.speaker.voice = new.voice
        if new.voice_enabled != old.voice_enabled:
            self.set_voice_enabled(new.voice_enabled)
        if new.text_with_voice != old.text_with_voice:
            self.set_text_with_voice(new.text_with_voice)
        if new.always_on_top != old.always_on_top:
            self.on_top_action.setChecked(new.always_on_top)  # also moves the window to or from the top
        if new.window_mode != old.window_mode:
            self.window_action.setChecked(new.window_mode == "window")  # also reframes her
        if new.character_height != old.character_height:
            self._resize_character(new.character_height)
        self._update_web_tooltip()

    def _reload_when_idle(self, local: bool, claude: bool, switched: bool) -> None:
        if self.quitting:
            return
        if self.worker is not None:  # a stopped reply is still wrapping up
            QTimer.singleShot(100, lambda: self._reload_when_idle(local, claude, switched))
            return
        if local:
            self._stop_local()
        if claude:
            self._stop_cloud()
        if self.settings.brain == "claude":
            self._reload_claude(switched)
        else:
            self._reload_local(switched)

    def _reload_local(self, switched: bool) -> None:
        if self.local_ready:
            self.character.react("happy", seconds=3)
            self.say(LOCAL_ON if switched else "Saved! Your new settings are on.")
            self.hide_bubble.start(self.settings.bubble_seconds * 1000)
            return
        self._sleep(WAKING)
        if switched:
            self.say("Okay, switching to my local brain. It needs about half a minute to wake up…")
        else:
            self.wake_note = "New settings loaded. I'm awake again!"
            self.say("Reloading my brain with the new settings. It needs about half a minute…")
        if self.starter is None:
            self.start_engine()

    def _reload_claude(self, switched: bool) -> None:
        if self.waiting_for_local and self.starter is None:
            self.start_engine()  # a message is still waiting for the local brain
        if self._online() or self.cloud_starter is not None:
            self.say("Saved! My local brain uses the new settings next time it starts.")
            self.hide_bubble.start(self.settings.bubble_seconds * 1000)
            return
        key = storage.load_claude_key()
        if not key:
            self._switch_to_local("There's no Claude API key saved. ")
            return
        if not self.local_ready:
            self._sleep(CONNECTING)
        if not switched:
            label = cloud.MODELS[self.settings.claude_model].label
            self.wake_note = f"New settings loaded. I'm thinking with {label} now."
        self.say(CONNECTING)
        self.start_cloud(key)  # on_cloud_ready takes it from there

    def _sleep(self, placeholder: str) -> None:
        """She can't take messages until her brain is ready again."""
        self.ready = False
        self.input.setEnabled(False)
        self.input.setPlaceholderText(placeholder)
        self.character.sleeping()

    def _stop_local(self) -> None:
        """Stops the local brain, or its loading, so it starts again with the new settings."""
        starter, self.starter = self.starter, None
        if starter is not None:
            starter.stop()
        self.engine.stop()  # also ends the loading it may be waiting on
        if starter is not None:
            starter.wait(5000)
            self.engine.stop()  # in case it had just started the engine
        self.local_ready = False
        self.engine.device = self.settings.device

    def _stop_cloud(self) -> None:
        """Drops the Claude connection (and any connecting), so it reconnects with the new settings."""
        starter, self.cloud_starter = self.cloud_starter, None
        if starter is not None:
            starter.stop()
            starter.wait(5000)
        if self.cloud is not None:
            self.cloud.close()
            self.cloud = None

    def restart(self) -> None:
        self.shutdown()
        QProcess.startDetached(sys.executable, ["-m", "companion"], str(ROOT))
        QApplication.quit()

    def quit(self) -> None:
        self.shutdown()
        QApplication.quit()

    def shutdown(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        if self.settings_window is not None:
            self.settings_window.done(0)  # closes it without asking about unsaved changes
        self.speaker.close()
        threads = (self.worker, self.starter, self.cloud_starter)
        for thread in threads:
            if thread is not None:
                thread.stop()  # a Claude reply or check ends when its connection closes
        self.engine.stop()  # also ends a local request that is still running
        for thread in threads:
            if thread is not None:
                thread.wait(5000)
        if self.cloud is not None:
            self.cloud.close()
        if self.tray is not None:
            self.tray.hide()

    def closeEvent(self, event) -> None:  # noqa: N802 (Alt+F4 quits)
        self.shutdown()
        QApplication.quit()
        event.accept()


def _icon_file() -> str:
    """Her face (capri.ico), or the original app icon if that's missing."""
    face = ASSETS / "icon" / "capri.ico"
    return str(face if face.is_file() else ASSETS / "icon" / "app.ico")


def main() -> int:
    # Our own app ID, so Windows shows her icon on taskbar buttons instead of Python's.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Capri.DesktopCompanion")
    except (AttributeError, OSError):
        pass
    app = QApplication(sys.argv)
    app.setApplicationName(f"{NAME} companion")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(_icon_file()))
    DATA.mkdir(exist_ok=True)
    lock = QLockFile(str(DATA / "companion.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(10000):
        theme.inform(None, NAME, f"{NAME} is already running. Look on your desktop or in the system tray.")
        return 0
    settings, notes = load_settings()
    window = CompanionWindow(settings, notes)
    window.show()
    window.character.preload()
    code = app.exec()
    window.shutdown()
    lock.unlock()
    return code
