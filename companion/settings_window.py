"""The settings window: change several settings, then save them all at once."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .config import (
    CLAUDE_RELOAD,
    LIMITS,
    LOCAL_RELOAD,
    SETTINGS_FILE,
    VOICE_RELOAD,
    Settings,
    changed_settings,
    history_limit,
)
from .voice import VOICES

CLAUDE_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,400}")
BRAVE_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")

CLAUDE_MODELS = {  # label -> model
    "Haiku 5.5 (cheapest, fastest)": "claude-haiku-5-5",
    "Sonnet 5.5 (smarter)": "claude-sonnet-5-5",
    "Opus 5.5 (smartest, priciest)": "claude-opus-5-5",
}
CONTEXT_SIZES = (2048, 4096, 8192, 16384, 32768, 65536)
CLAUDE_NOTICE = (
    "With Claude as her brain, each message you send goes to Anthropic over the internet, "
    "together with her personality and your recent conversation. It's paid from your Claude "
    "API credit. Your saved conversation stays encrypted on this PC."
)
NAV_WIDTH = 180
CONTROL_WIDTH = 250


@dataclass
class SettingsChange:
    """What the window saved: all values, which ones changed, and the API keys."""

    settings: Settings
    changed: set[str] = field(default_factory=set)
    claude_key: str | None = None  # None: unchanged; "": remove; otherwise the new key
    brave_key: str | None = None

    def reloads(self) -> list[str]:
        """What saving loads again, in words."""
        brain = self.settings.brain
        parts = []
        if "brain" in self.changed:
            parts.append("switches her to Claude" if brain == "claude" else "switches her to the local brain")
        elif brain == "local" and self.changed & LOCAL_RELOAD:
            parts.append("reloads her local brain (about half a minute)")
        elif brain == "claude" and (self.changed & CLAUDE_RELOAD or self.claude_key is not None):
            parts.append("reconnects to Claude")
        if self.changed & VOICE_RELOAD:
            parts.append("reloads her voice")
        return parts


@dataclass
class _Field:
    get: Callable[[], object]
    set: Callable[[object], None]


class ValueSlider(QWidget):
    """A slider with its value shown next to it."""

    def __init__(self, low: float, high: float, step: float, shown: Callable[[float], str]) -> None:
        super().__init__()
        self.step, self.shown = step, shown
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(low / step), round(high / step))
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label = QLabel()
        self.label.setFixedWidth(52)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.label.setFont(theme.font(10, bold=True))
        self.label.setStyleSheet(f"color: {theme.CYAN};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.label)
        self.slider.valueChanged.connect(lambda _v: self.label.setText(self.shown(self.value())))

    def value(self) -> float:
        return round(self.slider.value() * self.step, 4)

    def set_value(self, value: float) -> None:
        self.slider.setValue(round(value / self.step))
        self.label.setText(self.shown(self.value()))


class KeyField(QWidget):
    """A password box for an API key, with a button to forget the saved one."""

    def __init__(self, saved: bool, empty_hint: str) -> None:
        super().__init__()
        self.saved = saved
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setMaxLength(400)
        self.edit.setPlaceholderText("Saved · paste a new key to replace it" if saved else empty_hint)
        self.forget = QPushButton("Forget")
        self.forget.setObjectName("segment")
        self.forget.setCheckable(True)
        self.forget.setAutoDefault(False)
        self.forget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forget.setToolTip("Remove the saved key when you save")
        self.forget.setVisible(saved)
        self.forget.toggled.connect(self._on_forget)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.forget)

    def _on_forget(self, forget: bool) -> None:
        self.edit.setEnabled(not forget)
        if forget:
            self.edit.clear()
        self.edit.setPlaceholderText("Will be removed when you save" if forget else "Saved · paste a new key to replace it")

    def text(self) -> str:
        return self.edit.text().strip()

    def change(self) -> str | None:
        """None: keep what's saved; "": remove it; otherwise the new key."""
        if self.forget.isChecked():
            return ""
        return self.text() or None

    def available(self) -> bool:
        """A key will be there after saving."""
        return bool(self.text()) or (self.saved and not self.forget.isChecked())

    def mark(self, invalid: bool) -> None:
        self.edit.setProperty("invalid", invalid)
        self.edit.style().unpolish(self.edit)
        self.edit.style().polish(self.edit)


class SettingsWindow(theme.NeonDialog):
    saved = Signal(object)  # a SettingsChange

    PAGES = ("Brain", "Local brain", "Voice", "Internet", "Display")

    def __init__(self, settings: Settings, has_claude_key: bool, has_brave_key: bool, on_top: bool) -> None:
        super().__init__("Settings", None, on_top)
        self.setModal(False)
        self.fields: dict[str, _Field] = {}
        self._loading = True
        self.has_claude_key, self.has_brave_key = has_claude_key, has_brave_key

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(NAV_WIDTH)
        self.nav.setFont(theme.font(9.5, bold=True, spacing=2))
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        builders = (
            self._brain_page,
            self._local_page,
            self._voice_page,
            self._internet_page,
            self._display_page,
        )
        for name, build in zip(self.PAGES, builders):
            self.nav.addItem(name.upper())
            self.stack.addWidget(self._scrolling(build()))
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        version = QLabel("CAPRI // SYS.CONFIG")
        version.setFont(theme.font(7.5, spacing=2))
        version.setObjectName("hint")
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(self.nav, 1)
        side.addWidget(version)
        divider = QWidget()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {theme.LINE};")
        columns = QHBoxLayout()
        columns.setSpacing(18)
        columns.addLayout(side)
        columns.addWidget(divider)
        columns.addWidget(self.stack, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setFont(theme.font(9))
        defaults = theme.NeonButton("Defaults")
        defaults.setToolTip("Put every setting back to how Capri came (keys stay as they are)")
        defaults.clicked.connect(self._restore_defaults)
        cancel = theme.NeonButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.save_button = theme.NeonButton("Save", primary=True)
        self.save_button.clicked.connect(self._save)
        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(self.status, 1)
        footer.addWidget(defaults)
        footer.addWidget(cancel)
        footer.addWidget(self.save_button)

        self.body.setSpacing(16)
        self.body.addLayout(columns, 1)
        self.body.addLayout(footer)
        self.setFixedSize(860, 620)

        self.original = replace(settings)
        self._set_values(settings)
        self.baseline = self.values()  # what the window shows before any change
        self._loading = False
        self._refresh()

    # Building ------------------------------------------------------------------

    @staticmethod
    def _scrolling(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return area

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 14, 8)
        layout.setSpacing(14)
        marker = QWidget()
        marker.setFixedSize(4, 20)
        marker.setStyleSheet(f"background: {theme.ACCENT};")
        heading = QLabel(title.upper())
        heading.setFont(theme.font(15, bold=True, spacing=3))
        heading_row = QHBoxLayout()
        heading_row.setSpacing(10)
        heading_row.addWidget(marker)
        heading_row.addWidget(heading, 1)
        about = QLabel(subtitle)
        about.setObjectName("hint")
        about.setWordWrap(True)
        about.setFont(theme.font(9))
        layout.addLayout(heading_row)
        layout.addWidget(about)
        layout.addSpacing(2)
        return page, layout

    def _row(self, layout: QVBoxLayout, label: str, hint: str, control: QWidget) -> QLabel:
        name = QLabel(label)
        name.setFont(theme.font(10, bold=True))
        note = QLabel(hint)
        note.setObjectName("hint")
        note.setWordWrap(True)
        note.setFont(theme.font(8.5))
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(name)
        words.addWidget(note)
        words.addStretch(1)
        holder = QHBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        if isinstance(control, (theme.Toggle, theme.Segmented)):
            holder.addStretch(1)
        holder.addWidget(control)
        right = QWidget()
        right.setFixedWidth(CONTROL_WIDTH)
        right.setLayout(holder)
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addLayout(words, 1)
        row.addWidget(right, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255, 255, 255, 0.05);")
        layout.addWidget(line)
        return note

    def _watch(self, signal) -> None:
        signal.connect(lambda *_args: self._refresh())

    def _toggle(self, name: str) -> theme.Toggle:
        toggle = theme.Toggle()
        self.fields[name] = _Field(toggle.isChecked, lambda v: toggle.setChecked(bool(v)))
        self._watch(toggle.toggled)
        return toggle

    def _segments(self, name: str, choices: dict[str, object]) -> theme.Segmented:
        segments = theme.Segmented(choices)
        self.fields[name] = _Field(segments.value, segments.set_value)
        self._watch(segments.group.idToggled)
        return segments

    def _combo(self, name: str, choices: dict[str, object], custom: Callable[[object], str]) -> QComboBox:
        combo = QComboBox()
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for label, value in choices.items():
            combo.addItem(label, value)

        def choose(value: object) -> None:
            index = combo.findData(value)
            if index < 0:  # a value from settings.json that isn't in the list
                combo.addItem(custom(value), value)
                index = combo.count() - 1
            combo.setCurrentIndex(index)

        self.fields[name] = _Field(combo.currentData, choose)
        self._watch(combo.currentIndexChanged)
        return combo

    def _spin(self, name: str, step: int = 1, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(*LIMITS[name])
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setGroupSeparatorShown(True)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.fields[name] = _Field(spin.value, lambda v: spin.setValue(int(v)))
        self._watch(spin.valueChanged)
        return spin

    def _slider(self, name: str, step: float, shown: Callable[[float], str]) -> ValueSlider:
        low, high = LIMITS[name]
        slider = ValueSlider(low, high, step, shown)
        self.fields[name] = _Field(slider.value, slider.set_value)
        self._watch(slider.slider.valueChanged)
        return slider

    def _brain_page(self) -> QWidget:
        page, layout = self._page(
            "Brain",
            "Who does the thinking. Changing the brain or the Claude model reconnects her when you save.",
        )
        self.brain = self._segments("brain", {"Local · private": "local", "Claude · online": "claude"})
        self._row(layout, "Brain", "Local runs on this PC and nothing leaves it. Claude is smarter, online, with your API key.", self.brain)
        self.claude_notice = QLabel(CLAUDE_NOTICE)
        self.claude_notice.setObjectName("warning")
        self.claude_notice.setWordWrap(True)
        self.claude_notice.setFont(theme.font(8.5))
        layout.addWidget(self.claude_notice)
        self.brain.group.idToggled.connect(lambda *_a: self.claude_notice.setVisible(self.brain.value() == "claude"))
        self._row(layout, "Claude model", "Used when Claude is her brain. Bigger models cost more per message.", self._combo("claude_model", CLAUDE_MODELS, str))
        self._row(
            layout,
            "Claude searches the web",
            "When I ask: only with 🌐 or Ctrl+Enter. Whenever it helps: Claude decides (about a cent per search).",
            self._segments("claude_search", {"When I ask": "globe", "When it helps": "auto"}),
        )
        self._row(layout, "Searches per message", "The most web searches Claude may run for one message.", self._spin("claude_max_searches"))
        self.claude_key = KeyField(self.has_claude_key, "Paste your key (starts with sk-ant-)")
        self._watch(self.claude_key.edit.textChanged)
        self._watch(self.claude_key.forget.toggled)
        self._row(layout, "Claude API key", "From the Claude Console. Stored encrypted for your Windows account and only sent to Anthropic.", self.claude_key)
        layout.addStretch(1)
        return page

    def _local_page(self) -> QWidget:
        page, layout = self._page(
            "Local brain",
            "The language model on this PC. Changing where it runs, its threads or its working memory "
            "reloads it when you save (about half a minute).",
        )
        self._row(
            layout,
            "Runs on",
            "The graphics chip is usually faster. If it doesn't cooperate, she falls back to the CPU.",
            self._segments("device", {"Graphics chip": "gpu", "CPU": "cpu"}),
        )
        sizes = {f"{size:,} tokens": size for size in CONTEXT_SIZES}
        self.context = self._combo("context_tokens", sizes, lambda v: f"{v:,} tokens")
        self._row(layout, "Working memory", "Everything she reads at once. More uses more memory and loads slower.", self.context)
        self.history = self._spin("history_tokens", 250, " tokens")
        self.history_note = self._row(layout, "Conversation memory", "", self.history)
        self.reply_length = self._spin("max_reply_tokens", 32, " tokens")
        self._row(layout, "Longest reply", "Replies stop after this many tokens (about ¾ of a word each).", self.reply_length)
        for widget in (self.context.currentIndexChanged, self.reply_length.valueChanged):
            widget.connect(lambda *_a: self._fit_history())
        self._row(
            layout,
            "Creativity",
            "Low is focused and predictable; high is more surprising and more chaotic.",
            self._slider("temperature", 0.05, lambda v: f"{v:.2f}"),
        )
        self._row(layout, "CPU threads, writing", "Only used when she runs on the CPU.", self._spin("threads"))
        self._row(layout, "CPU threads, reading", "Used to read your message when she runs on the CPU.", self._spin("threads_batch"))
        layout.addStretch(1)
        return page

    def _voice_page(self) -> QWidget:
        page, layout = self._page("Voice", "How she sounds. Voice changes apply from her next sentence.")
        self._row(layout, "Speak replies", "Read her replies out loud.", self._toggle("voice_enabled"))
        self._row(layout, "Show text as she speaks", "Words appear in the bubble as she says them, instead of all at once.", self._toggle("text_with_voice"))
        self._row(layout, "Voice", "All voices speak English.", self._combo("voice", VOICES, lambda v: f"{v} (custom)"))
        effects = {"Off": "off", "Light": "light", "Robotic": "robotic", "Full": "full"}  # as in EFFECTS
        self._row(layout, "Robot effect", "From a clean voice to a full synthetic one.", self._segments("voice_effect", effects))
        self._row(layout, "Pitch", "Higher sounds brighter; her speaking speed stays the same.", self._slider("voice_pitch", 0.01, lambda v: f"{v:.2f}×"))
        self._row(layout, "Speed", "How fast she talks.", self._slider("voice_speed", 0.05, lambda v: f"{v:.2f}×"))
        self._row(layout, "Volume", "Her loudness, on top of the Windows volume.", self._slider("voice_volume", 0.01, lambda v: f"{v:.0%}"))
        self._row(layout, "Voice threads", "CPU threads for her voice. More competes with the local brain. Changing it reloads her voice.", self._spin("voice_threads"))
        layout.addStretch(1)
        return page

    def _internet_page(self) -> QWidget:
        page, layout = self._page(
            "Internet",
            "Lookups only happen when you ask (🌐 or Ctrl+Enter). Weather questions use Open-Meteo, "
            "which only gets the place name.",
        )
        place = QLineEdit()
        place.setMaxLength(80)
        place.setPlaceholderText("Your city")
        self.fields["web_location"] = _Field(lambda: " ".join(place.text().split()), lambda v: place.setText(str(v)))
        self._watch(place.textChanged)
        self._row(layout, "Weather location", "For weather questions that don't name a place. Leave empty to be asked.", place)
        self._row(layout, "Results per lookup", "Search results she reads before answering.", self._spin("web_results"))
        self._row(
            layout,
            "SafeSearch",
            "Filters adult results from Brave Search.",
            self._segments("web_safesearch", {"Off": "off", "Moderate": "moderate", "Strict": "strict"}),
        )
        self.brave_key = KeyField(self.has_brave_key, "Paste your Brave Search key")
        self._watch(self.brave_key.edit.textChanged)
        self._watch(self.brave_key.forget.toggled)
        self._row(layout, "Brave Search key", "Without a key, lookups use Wikipedia. Stored encrypted; only sent to Brave.", self.brave_key)
        layout.addStretch(1)
        return page

    def _display_page(self) -> QWidget:
        page, layout = self._page("Display", "How she sits on your desktop. These apply right away when you save.")
        self._row(layout, "Size", "Her height on the screen.", self._spin("character_height", 10, " px"))
        self._row(layout, "Always on top", "Stay above other windows.", self._toggle("always_on_top"))
        self._row(layout, "Reply details", "Speed, or the cost with Claude, under each reply.", self._toggle("show_stats"))
        self._row(layout, "Speech bubble", "Hide the bubble this long after she's done talking.", self._spin("bubble_seconds", 5, " s"))
        advanced = theme.NeonButton("Open file")
        advanced.clicked.connect(self._open_file)
        self._row(layout, "settings.json", "The file behind this window, with the model and engine paths. Restart Capri after editing it.", advanced)
        layout.addStretch(1)
        return page

    # Values --------------------------------------------------------------------

    def _set_values(self, settings: Settings) -> None:
        loading, self._loading = self._loading, True
        for name, holder in self.fields.items():
            holder.set(getattr(settings, name))
        self._fit_history()
        self.claude_notice.setVisible(settings.brain == "claude")
        self._loading = loading

    def values(self) -> Settings:
        """The settings as the window shows them; hidden ones (like file paths) unchanged."""
        return replace(self.original, **{name: holder.get() for name, holder in self.fields.items()})

    def change(self) -> SettingsChange:
        current = self.values()
        return SettingsChange(
            settings=current,
            changed=changed_settings(self.baseline, current),
            claude_key=self.claude_key.change(),
            brave_key=self.brave_key.change(),
        )

    def _fit_history(self) -> None:
        limit = history_limit(self.context.currentData(), self.reply_length.value())
        self.history.setMaximum(limit)
        self.history_note.setText(
            f"How much of your recent chat goes into each message. At most {limit:,} with this working memory."
        )

    # Reacting ------------------------------------------------------------------

    def _problem(self) -> tuple[str, int] | None:
        """What stops saving, and the page to show it on."""
        self.claude_key.mark(False)
        self.brave_key.mark(False)
        claude_text, brave_text = self.claude_key.text(), self.brave_key.text()
        if claude_text and not CLAUDE_KEY.fullmatch(claude_text):
            self.claude_key.mark(True)
            return "That doesn't look like a Claude API key (they start with sk-ant-).", 0
        if self.brain.value() == "claude" and not self.claude_key.available():
            self.claude_key.mark(True)
            return "Claude needs an API key. Paste yours under Claude API key.", 0
        if brave_text and not BRAVE_KEY.fullmatch(brave_text):
            self.brave_key.mark(True)
            return "That doesn't look like a Brave Search API key.", 3
        return None

    def _refresh(self) -> None:
        if self._loading:
            return
        change = self.change()
        count = len(change.changed) + (change.claude_key is not None) + (change.brave_key is not None)
        self.save_button.setEnabled(count > 0)
        if count == 0:
            self._show_status("No changes yet.", theme.MUTED)
            return
        text = f"{count} unsaved change{'s' if count != 1 else ''}"
        reloads = change.reloads()
        if reloads:
            text += " · Saving " + " and ".join(reloads) + "."
        self._show_status(text, theme.YELLOW)

    def _show_status(self, text: str, color: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def _restore_defaults(self) -> None:
        self._set_values(Settings())  # only the settings shown here
        self._refresh()

    def _open_file(self) -> None:
        try:
            os.startfile(SETTINGS_FILE)  # opens in your default editor
        except OSError as error:
            self._show_status(f"Couldn't open settings.json: {error}", theme.ACCENT)

    def _save(self) -> None:
        problem = self._problem()
        if problem is not None:
            text, page = problem
            self.nav.setCurrentRow(page)
            self._show_status(text, theme.ACCENT)
            return
        change = self.change()
        self._loading = True  # nothing left unsaved: closing needs no question
        self.accept()
        self.saved.emit(change)

    def reject(self) -> None:
        if not self._loading and self.save_button.isEnabled():
            if not theme.confirm(self, "Unsaved changes", "Close the settings without saving your changes?", "Discard", "Keep editing"):
                return
        super().reject()
