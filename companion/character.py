"""The animated character: expressions, blinking, glancing and talking."""

from __future__ import annotations

import random

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import QBitmap, QColor, QGuiApplication, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QLabel, QWidget

from .chat import MOODS
from .config import ASSETS

FOLDER = ASSETS / "character"
FILES = {
    "neutral": "capri-neutral.png",
    **{mood: f"capri-idle-{mood}.png" for mood in MOODS if mood != "neutral"},
    **{
        frame: f"capri-idle-{frame}.png"
        for frame in (
            "blink_half",
            "blink_closed",
            "mouth_small",
            "mouth_round",
            "mouth_wide",
            "look_left",
            "look_right",
            "look_up",
            "look_down",
            "wink_half",
            "wink_closed",
        )
    },
}
TALK_FRAMES = ("mouth_small", "mouth_round", "mouth_wide", "neutral")
GLANCES = ("look_left", "look_right", "look_up", "look_down")


class CharacterView(QLabel):
    def __init__(self, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._height = height
        self._ratio = self._screen_ratio()
        self._images: dict[str, QImage] = {}
        self._pixmaps: dict[str, QPixmap] = {}
        self._crop: QRect | None = None
        self._to_preload = [name for name in FILES if name != "neutral"]
        self.state = "idle"
        self.current = ""
        self._queue: list[tuple[str, int]] = []

        self._sequence = QTimer(self, singleShot=True)
        self._sequence.timeout.connect(self._next_in_sequence)
        self._blink = QTimer(self, singleShot=True)
        self._blink.timeout.connect(self._do_blink)
        self._glance = QTimer(self, singleShot=True)
        self._glance.timeout.connect(self._do_glance)
        self._talk = QTimer(self, interval=120)
        self._talk.timeout.connect(self._talk_step)
        self._back_to_idle = QTimer(self, singleShot=True)
        self._back_to_idle.timeout.connect(self.idle)
        self._preloader = QTimer(self, interval=40)
        self._preloader.timeout.connect(self._preload_next)

        self._show("neutral")
        self.setFixedSize(self.frame("neutral").deviceIndependentSize().toSize())

    # Loading -------------------------------------------------------------

    @staticmethod
    def _screen_ratio() -> float:
        screen = QGuiApplication.primaryScreen()
        return screen.devicePixelRatio() if screen else 1.0

    @staticmethod
    def _figure_bounds(image: QImage) -> QRect:
        """The area that isn't transparent, plus a small margin."""
        step = 4
        small = image.scaled(
            max(1, image.width() // step),
            max(1, image.height() // step),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        box = QRegion(QBitmap.fromImage(small.createAlphaMask())).boundingRect()
        if box.isEmpty():
            return image.rect()
        box = QRect(box.x() * step, box.y() * step, box.width() * step, box.height() * step)
        margin = max(8, image.height() // 60)
        return box.adjusted(-margin, -margin, margin, margin).intersected(image.rect())

    def _image(self, name: str) -> QImage:
        if name not in self._images:
            path = FOLDER / FILES.get(name, "")
            self._images[name] = QImage(str(path)) if path.is_file() else QImage()
        return self._images[name]

    def frame(self, name: str) -> QPixmap:
        if name in self._pixmaps:
            return self._pixmaps[name]
        image = self._image(name)
        if image.isNull():
            pixmap = self._placeholder() if name == "neutral" else self.frame("neutral")
            self._pixmaps[name] = pixmap
            return pixmap
        if self._crop is None or not image.rect().contains(self._crop):
            neutral = self._image("neutral")
            source = neutral if not neutral.isNull() and neutral.size() == image.size() else image
            self._crop = self._figure_bounds(source)
        scaled = image.copy(self._crop).scaledToHeight(
            round(self._height * self._ratio), Qt.TransformationMode.SmoothTransformation
        )
        pixmap = QPixmap.fromImage(scaled)
        pixmap.setDevicePixelRatio(self._ratio)
        self._pixmaps[name] = pixmap
        return pixmap

    def _placeholder(self) -> QPixmap:
        size = QSize(round(self._height * 0.6), self._height)
        pixmap = QPixmap(size * self._ratio)
        pixmap.setDevicePixelRatio(self._ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#ff2d55"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(10, 10, size.width() - 20, size.width() - 20)
        painter.end()
        return pixmap

    def preload(self) -> None:
        """Prepares the other frames in small steps after the window is visible."""
        self._preloader.start()

    def set_height(self, height: int) -> None:
        """Shows her at a new size, keeping the current frame."""
        if height == self._height:
            return
        self._height = height
        self._pixmaps.clear()
        self._to_preload = [name for name in FILES if name != "neutral"]
        shown, self.current = self.current or "neutral", ""
        self._show(shown)
        self.setFixedSize(self.frame("neutral").deviceIndependentSize().toSize())
        self.preload()

    def _preload_next(self) -> None:
        if not self._to_preload:
            self._preloader.stop()
            return
        self.frame(self._to_preload.pop(0))

    # Showing -------------------------------------------------------------

    def _show(self, name: str) -> None:
        if name != self.current:
            self.current = name
            self.setPixmap(self.frame(name))

    def _play(self, frames: list[tuple[str, int]]) -> None:
        self._queue = list(frames)
        self._next_in_sequence()

    def _next_in_sequence(self) -> None:
        if not self._queue:
            return
        name, milliseconds = self._queue.pop(0)
        self._show(name)
        if self._queue:
            self._sequence.start(milliseconds)

    def _enter(self, state: str) -> None:
        self.state = state
        self._queue.clear()
        for timer in (self._sequence, self._blink, self._glance, self._talk, self._back_to_idle):
            timer.stop()

    # States --------------------------------------------------------------

    def idle(self) -> None:
        self._enter("idle")
        self._show("neutral")
        self._blink.start(random.randint(2500, 6000))
        self._glance.start(random.randint(15000, 40000))

    def sleeping(self) -> None:
        self._enter("sleeping")
        self._show("sleepy")

    def thinking(self) -> None:
        self._enter("thinking")
        self._show("thinking")

    def talking(self) -> None:
        if self.state == "talking":
            return
        self._enter("talking")
        self._talk_step()
        self._talk.start()

    def lip_sync(self, level: float) -> None:
        """Moves the mouth with her voice: closed, small, round or wide open."""
        if self.state != "speaking":
            self._enter("speaking")
        if level < 0.15:
            self._show("neutral")
        elif level < 0.45:
            self._show("mouth_small")
        elif level < 0.75:
            self._show("mouth_round")
        else:
            self._show("mouth_wide")

    def hold(self, mood: str | None) -> None:
        """Shows an expression until something else happens."""
        self._enter("holding")
        self._show(mood if mood in FILES else "neutral")

    def react(self, mood: str | None, seconds: float = 8) -> None:
        """Shows an expression for a while, then goes back to idle."""
        if not mood or mood == "neutral":
            self.idle()
            return
        self._enter("reacting")
        self._show(mood)
        self._back_to_idle.start(round(seconds * 1000))

    def poke(self) -> None:
        if self.state == "idle":
            self.react(random.choice(("attentive", "surprised", "smirk")), seconds=1.6)

    # Idle details ----------------------------------------------------------

    def _do_blink(self) -> None:
        if self.state != "idle":
            return
        if random.random() < 0.06:
            frames = [("wink_half", 60), ("wink_closed", 140), ("wink_half", 60), ("neutral", 0)]
        else:
            frames = [("blink_half", 45), ("blink_closed", 90), ("blink_half", 45), ("neutral", 0)]
        self._play(frames)
        self._blink.start(random.randint(2500, 6500))

    def _do_glance(self) -> None:
        if self.state != "idle":
            return
        self._play([(random.choice(GLANCES), random.randint(900, 1600)), ("neutral", 0)])
        self._glance.start(random.randint(15000, 40000))

    def _talk_step(self) -> None:
        choices = [frame for frame in TALK_FRAMES if frame != self.current]
        self._show(random.choice(choices))
