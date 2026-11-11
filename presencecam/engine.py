"""Frame pipeline: camera (or test pattern) -> live / loop -> glitch -> virtual camera + preview."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

SOURCES = ("live", "loop")
GLITCHES = ("none", "freeze", "weak", "choppy")
# Resolution steps (fraction of the full size) a sender moves between on a weak link.
WEAK_LEVELS = (0.07, 0.1, 0.15, 0.22, 0.32, 0.45, 0.65)


def pingpong_index(step: int, length: int) -> int:
    """0,1,..,n-1,n-2,..,1,0,1,.. so a loop never jumps back to its first frame."""
    if length <= 1:
        return 0
    period = 2 * (length - 1)
    position = step % period
    return position if position < length else period - position


def low_bandwidth(frame: np.ndarray, scale: float, quality: int) -> np.ndarray:
    """The picture a video call shows when it sends at a fraction of the resolution.

    Compressed at the low resolution and scaled up smoothly, as a real decoder does:
    blur and smudges, not pixel squares.
    """
    height, width = frame.shape[:2]
    small = cv2.resize(frame, (max(16, int(width * scale)), max(9, int(height * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        small = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


class SwitchCover:
    """Hides a live <-> loop switch behind what looks like a short connection hiccup.

    A cut or a cross-fade between two takes is easy to spot. A call that freezes, comes
    back blurry with a stutter and then sharpens is something everyone has seen, and the
    jump in position between the two takes disappears inside it.
    """

    def __init__(self, held: np.ndarray | None, start: float, rng: random.Random) -> None:
        self.held = held
        self.rng = rng
        self.freeze_until = start + rng.uniform(1.2, 2.5)
        self.recover_until = self.freeze_until + rng.uniform(1.0, 2.0)
        stutter_at = rng.uniform(self.freeze_until + 0.2, self.recover_until - 0.4)
        self.stutter = (stutter_at, stutter_at + rng.uniform(0.2, 0.5))
        self.next_frame_at = 0.0

    def frozen(self, now: float) -> bool:
        return now < self.freeze_until

    def done(self, now: float) -> bool:
        return now >= self.recover_until

    def apply(self, frame: np.ndarray, now: float) -> np.ndarray:
        if self.held is None:
            self.held = frame
        stuttering = self.stutter[0] <= now < self.stutter[1]
        if self.frozen(now) or stuttering or now < self.next_frame_at:
            return self.held
        # Back from the freeze: low resolution first, stepping up as the link "recovers".
        progress = (now - self.freeze_until) / (self.recover_until - self.freeze_until)
        level = min(len(WEAK_LEVELS) - 1, int(progress * len(WEAK_LEVELS)))
        self.held = low_bandwidth(frame, WEAK_LEVELS[level], 25 if progress < 0.5 else 45)
        self.next_frame_at = now + 1 / self.rng.uniform(8, 15)
        return self.held


class Glitcher:
    """Turns a clean frame stream into a believable bad call.

    freeze: the picture stops.  weak: quality jumps up and down with freezes, like a
    sender fighting a bad link.  choppy: normal picture that stalls for 1-4 s every so often.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.mode = "none"
        self.held: np.ndarray | None = None
        self.stall_until = 0.0
        self.next_stall = 0.0
        self.next_frame_at = 0.0
        self.set_mode("none")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.stall_until = 0.0
        self.next_stall = 0.0
        # weak: start mid-way, as if the link just got worse.
        self.level = 2
        self.capacity = 2.5
        self.next_capacity_at = 0.0
        self.next_probe_at = 0.0

    def apply(self, frame: np.ndarray, now: float) -> np.ndarray:
        if self.mode == "none":
            self.held = frame
            return frame
        if self.mode == "freeze":
            if self.held is None:
                self.held = frame
            return self.held

        if now < self.stall_until and self.held is not None:
            return self.held
        if self.mode == "weak":
            return self._weak(frame, now)
        if self.next_stall == 0.0:
            self.next_stall = now + self.rng.uniform(3, 8)
        if now >= self.next_stall:
            self.stall_until = now + self.rng.uniform(1.0, 4.0)
            self.next_stall = self.stall_until + self.rng.uniform(5, 15)
            return self.held if self.held is not None else frame

        self.held = frame
        return frame

    def _weak(self, frame: np.ndarray, now: float) -> np.ndarray:
        """A call on a bad link, modelled on how video calls adapt to bandwidth.

        The sender keeps stepping its resolution up to probe for more bandwidth. When it
        overshoots what the link carries right now, the picture freezes while the queue
        drains, then comes back much worse and climbs again. The link itself also changes
        in jumps, so a good stretch can collapse at any moment. Quality changes are steps,
        never smooth: that is what makes it read as a connection and not as a filter.
        """
        if now >= self.next_capacity_at:
            self.capacity = self.rng.uniform(1.0, len(WEAK_LEVELS) - 0.3)
            self.next_capacity_at = now + self.rng.uniform(1.5, 6)
        if now >= self.next_probe_at:
            self.level = min(self.level + 1, len(WEAK_LEVELS) - 1)
            self.next_probe_at = now + self.rng.uniform(0.4, 1.2)
        if self.level > self.capacity:
            # Congestion: the picture stops, then the sender backs off hard.
            self.stall_until = now + self.rng.uniform(0.4, 2.5)
            self.level = max(0, self.level - self.rng.choice((2, 2, 3, 4)))
            self.next_probe_at = self.stall_until + self.rng.uniform(0.5, 1.5)
            return self.held if self.held is not None else frame

        # Near the limit frames come late and unevenly; with headroom they flow better.
        near_limit = self.capacity - self.level < 1
        if now < self.next_frame_at and self.held is not None:
            return self.held
        self.next_frame_at = now + 1 / (self.rng.uniform(4, 10) if near_limit else self.rng.uniform(10, 20))

        frame = low_bandwidth(frame, WEAK_LEVELS[self.level], 20 if near_limit else 45)
        self.held = frame
        return frame


def test_pattern(width: int, height: int, now: float) -> np.ndarray:
    """Moving gradient with a clock, used when no camera can be opened."""
    x = np.linspace(0, 255, width, dtype=np.float32)
    shift = (now * 40) % 255
    row = ((x + shift) % 255).astype(np.uint8)
    frame = np.dstack([np.tile(row, (height, 1)), np.full((height, width), 90, np.uint8), np.tile(row[::-1], (height, 1))])
    label = datetime.now().strftime("PresenceCam test  %H:%M:%S.%f")[:-3]
    cv2.putText(frame, label, (30, height // 2), cv2.FONT_HERSHEY_SIMPLEX, width / 900, (255, 255, 255), 2, cv2.LINE_AA)
    return frame


@dataclass
class Clip:
    name: str
    frames: list[bytes]  # JPEG, so a minute of 720p fits in ~100 MB of memory


@dataclass
class Recording:
    clip: Clip
    until: float
    writer: cv2.VideoWriter


@dataclass
class EngineConfig:
    width: int = 1280
    height: int = 720
    fps: int = 30
    clips_dir: Path = Path("clips")
    # Latest frame from the real camera (BGR), or None when there is none yet.
    read_frame: Callable[[], np.ndarray | None] = lambda: None
    # pyvirtualcam backend and device name; backend None = preview only.
    virtual_backend: str | None = None
    virtual_device: str | None = None


@dataclass
class Engine:
    config: EngineConfig
    source: str = "live"
    glitch: str = "none"
    status: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.lock = threading.Lock()
        self.glitcher = Glitcher()
        self.clips: dict[str, Clip] = {}
        self.active_clip: str | None = None
        self.recording: Recording | None = None
        self.loop_step = 0
        self.cover: SwitchCover | None = None
        self.last_output: np.ndarray | None = None
        self.output_frame: np.ndarray | None = None
        self.stopped = threading.Event()
        self.reopen_virtual = threading.Event()
        self.config.clips_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.config.clips_dir.glob("*.mp4")):
            self._load_clip(path)

    # --- control (called from the web panel) ---

    def set_source(self, source: str, clip: str | None = None) -> None:
        with self.lock:
            if source not in SOURCES:
                raise ValueError(f"źródło musi być jednym z {SOURCES}")
            if clip is not None:
                if clip not in self.clips:
                    raise ValueError(f"nie ma nagrania {clip!r}")
                self.active_clip = clip
            if source == "loop" and self.active_clip is None:
                raise ValueError("Najpierw nagraj albo wybierz nagranie do pętli")
            if source != self.source or clip is not None:
                self.cover = SwitchCover(self.last_output, time.monotonic(), self.glitcher.rng)
                self.loop_step = 0
            self.source = source

    def set_glitch(self, glitch: str) -> None:
        with self.lock:
            if glitch not in GLITCHES:
                raise ValueError(f"efekt musi być jednym z {GLITCHES}")
            self.glitch = glitch
            self.glitcher.set_mode(glitch)

    def start_recording(self, seconds: float) -> str:
        with self.lock:
            if self.recording is not None:
                raise ValueError("Nagrywanie już trwa")
            if not 3 <= seconds <= 300:
                raise ValueError("Długość nagrania: od 3 do 300 s")
            name = datetime.now().strftime("clip-%Y%m%d-%H%M%S")
            path = self.config.clips_dir / f"{name}.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), self.config.fps, (self.config.width, self.config.height))
            self.recording = Recording(Clip(name, []), time.monotonic() + seconds, writer)
            return name

    def delete_clip(self, name: str) -> None:
        with self.lock:
            if name not in self.clips:
                raise ValueError(f"nie ma nagrania {name!r}")
            if self.source == "loop" and self.active_clip == name:
                raise ValueError("Najpierw wróć na żywo, to nagranie jest teraz w pętli")
            del self.clips[name]
            if self.active_clip == name:
                self.active_clip = None
            (self.config.clips_dir / f"{name}.mp4").unlink(missing_ok=True)

    def state(self) -> dict:
        with self.lock:
            recording = None
            if self.recording is not None:
                recording = {"clip": self.recording.clip.name, "remaining": max(0.0, round(self.recording.until - time.monotonic(), 1))}
            return {
                "source": self.source,
                "glitch": self.glitch,
                "activeClip": self.active_clip,
                "clips": [{"name": clip.name, "seconds": round(len(clip.frames) / self.config.fps, 1)} for clip in self.clips.values()],
                "recording": recording,
                **self.status,
            }

    # --- pipeline ---

    def _load_clip(self, path: Path) -> None:
        capture = cv2.VideoCapture(str(path))
        frames = []
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
        capture.release()
        if frames:
            self.clips[path.stem] = Clip(path.stem, frames)
            self.active_clip = self.active_clip or path.stem

    def set_virtual_device(self, backend: str | None, device: str | None) -> None:
        """Switches the virtual camera (e.g. after a rename); the pipeline reopens it."""
        self.config.virtual_backend = backend
        self.config.virtual_device = device
        self.reopen_virtual.set()

    def _open_virtual_camera(self):
        if self.config.virtual_backend is None:
            self.status["virtualCamera"] = "wyłączona"
            return None
        try:
            import pyvirtualcam

            camera = pyvirtualcam.Camera(
                self.config.width,
                self.config.height,
                self.config.fps,
                fmt=pyvirtualcam.PixelFormat.BGR,
                backend=self.config.virtual_backend,
                device=self.config.virtual_device,
            )
            self.status["virtualCamera"] = f"działa: {camera.device}"
            return camera
        except Exception as error:  # noqa: BLE001 - any backend failure means preview-only mode
            self.status["virtualCamera"] = f"niedostępna: {error}"
            return None

    def _frame(self, now: float, live: np.ndarray) -> np.ndarray:
        """Picks what goes out (live or loop), covers a switch between them, then glitches it."""
        with self.lock:
            if self.recording is not None:
                self.recording.writer.write(live)
                self.recording.clip.frames.append(cv2.imencode(".jpg", live, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
                if now >= self.recording.until:
                    self.recording.writer.release()
                    clip = self.recording.clip
                    self.clips[clip.name] = clip
                    self.active_clip = clip.name
                    self.recording = None

            if self.cover is not None and self.cover.done(now):
                self.cover = None

            frame = live
            # While frozen the loop waits, so viewers see it from its first frame.
            loop_paused = self.cover is not None and self.cover.frozen(now)
            if self.source == "loop" and self.active_clip in self.clips and not loop_paused:
                frames = self.clips[self.active_clip].frames
                encoded = frames[pingpong_index(self.loop_step, len(frames))]
                frame = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
                self.loop_step += 1

            if self.cover is not None:
                frame = self.cover.apply(frame, now)

            frame = self.glitcher.apply(frame, now)
            self.last_output = frame
            return frame

    def run(self) -> None:
        virtual = self._open_virtual_camera()
        interval = 1 / self.config.fps
        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                if self.reopen_virtual.is_set():
                    self.reopen_virtual.clear()
                    if virtual is not None:
                        virtual.close()
                    virtual = self._open_virtual_camera()

                live = self.config.read_frame()
                self.status["camera"] = "kamera działa" if live is not None else "brak kamery, obraz testowy"
                if live is None:
                    live = test_pattern(self.config.width, self.config.height, started)
                elif live.shape[:2] != (self.config.height, self.config.width):
                    live = cv2.resize(live, (self.config.width, self.config.height))

                frame = self._frame(started, live)
                self.output_frame = frame
                if virtual is not None:
                    try:
                        virtual.send(frame)
                    except Exception as error:  # noqa: BLE001 - e.g. the driver was removed; keep previewing
                        self.status["virtualCamera"] = f"utracona: {error}"
                        virtual = None
                time.sleep(max(0.0, interval - (time.monotonic() - started)))
        finally:
            if virtual is not None:
                virtual.close()
