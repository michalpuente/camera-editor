"""Frame pipeline: camera (or test pattern) -> live / loop -> glitch -> virtual camera + preview."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

SOURCES = ("live", "loop")
GLITCHES = ("none", "freeze", "weak", "choppy")
FADE_SECONDS = 0.6


def pingpong_index(step: int, length: int) -> int:
    """0,1,..,n-1,n-2,..,1,0,1,.. so a loop never jumps back to its first frame."""
    if length <= 1:
        return 0
    period = 2 * (length - 1)
    position = step % period
    return position if position < length else period - position


def crossfade(old: np.ndarray, new: np.ndarray, progress: float) -> np.ndarray:
    if old.shape != new.shape:
        old = cv2.resize(old, (new.shape[1], new.shape[0]))
    return cv2.addWeighted(old, 1.0 - progress, new, progress, 0)


class Glitcher:
    """Turns a clean frame stream into a believable bad call.

    freeze: the picture stops.  weak: blocky, low frame rate, short stalls (poor
    connection).  choppy: normal picture that stalls for 1-4 s every so often.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.mode = "none"
        self.held: np.ndarray | None = None
        self.stall_until = 0.0
        self.next_stall = 0.0
        self.next_frame_at = 0.0

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.stall_until = 0.0
        self.next_stall = 0.0

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
        if self.next_stall == 0.0:
            self.next_stall = now + self.rng.uniform(3, 8)
        if now >= self.next_stall:
            short = self.mode == "weak"
            self.stall_until = now + (self.rng.uniform(0.3, 1.5) if short else self.rng.uniform(1.0, 4.0))
            self.next_stall = self.stall_until + (self.rng.uniform(2, 6) if short else self.rng.uniform(5, 15))
            return self.held if self.held is not None else frame

        if self.mode == "weak":
            # ~8 fps, blocky and over-compressed, like a video call on a bad link.
            if now < self.next_frame_at and self.held is not None:
                return self.held
            self.next_frame_at = now + 1 / 8
            height, width = frame.shape[:2]
            factor = self.rng.choice((6, 8, 10))
            small = cv2.resize(frame, (max(1, width // factor), max(1, height // factor)), interpolation=cv2.INTER_AREA)
            blocky = cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)
            ok, encoded = cv2.imencode(".jpg", blocky, [cv2.IMWRITE_JPEG_QUALITY, 8])
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR) if ok else blocky

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
    camera: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    clips_dir: Path = Path("clips")
    virtual_camera: bool = True


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
        self.preview_jpeg = b""
        self.loop_step = 0
        self.fade_from: np.ndarray | None = None
        self.fade_started = 0.0
        self.last_output: np.ndarray | None = None
        self.stopped = threading.Event()
        self.config.clips_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.config.clips_dir.glob("*.mp4")):
            self._load_clip(path)

    # --- control (called from the web panel) ---

    def set_source(self, source: str, clip: str | None = None) -> None:
        with self.lock:
            if source not in SOURCES:
                raise ValueError(f"source must be one of {SOURCES}")
            if clip is not None:
                if clip not in self.clips:
                    raise ValueError(f"unknown clip {clip!r}")
                self.active_clip = clip
            if source == "loop" and self.active_clip is None:
                raise ValueError("record or pick a clip first")
            if source != self.source or clip is not None:
                # Cross-fade from what viewers see now, so the switch has no visible cut.
                self.fade_from = self.last_output
                self.fade_started = time.monotonic()
                self.loop_step = 0
            self.source = source

    def set_glitch(self, glitch: str) -> None:
        with self.lock:
            if glitch not in GLITCHES:
                raise ValueError(f"glitch must be one of {GLITCHES}")
            self.glitch = glitch
            self.glitcher.set_mode(glitch)

    def start_recording(self, seconds: float) -> str:
        with self.lock:
            if self.recording is not None:
                raise ValueError("already recording")
            if not 3 <= seconds <= 300:
                raise ValueError("seconds must be between 3 and 300")
            name = datetime.now().strftime("clip-%Y%m%d-%H%M%S")
            path = self.config.clips_dir / f"{name}.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), self.config.fps, (self.config.width, self.config.height))
            self.recording = Recording(Clip(name, []), time.monotonic() + seconds, writer)
            return name

    def delete_clip(self, name: str) -> None:
        with self.lock:
            if name not in self.clips:
                raise ValueError(f"unknown clip {name!r}")
            if self.source == "loop" and self.active_clip == name:
                raise ValueError("switch back to live before deleting the clip on air")
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

    def _open_camera(self) -> cv2.VideoCapture | None:
        capture = cv2.VideoCapture(self.config.camera)
        if not capture.isOpened():
            return None
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        capture.set(cv2.CAP_PROP_FPS, self.config.fps)
        return capture

    def _open_virtual_camera(self):
        if not self.config.virtual_camera:
            self.status["virtualCamera"] = "off (--no-virtual)"
            return None
        try:
            import pyvirtualcam

            camera = pyvirtualcam.Camera(self.config.width, self.config.height, self.config.fps, fmt=pyvirtualcam.PixelFormat.BGR)
            self.status["virtualCamera"] = f"on: {camera.device}"
            return camera
        except Exception as error:  # noqa: BLE001 - any backend failure means preview-only mode
            self.status["virtualCamera"] = f"unavailable: {error}"
            return None

    def _frame(self, now: float, live: np.ndarray) -> np.ndarray:
        """Picks what goes out (live or loop), fades between them, then glitches it."""
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

            frame = live
            if self.source == "loop" and self.active_clip in self.clips:
                frames = self.clips[self.active_clip].frames
                encoded = frames[pingpong_index(self.loop_step, len(frames))]
                frame = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
                self.loop_step += 1

            if self.fade_from is not None:
                progress = (time.monotonic() - self.fade_started) / FADE_SECONDS
                if progress >= 1:
                    self.fade_from = None
                else:
                    frame = crossfade(self.fade_from, frame, progress)

            frame = self.glitcher.apply(frame, now)
            self.last_output = frame
            return frame

    def run(self) -> None:
        camera = self._open_camera()
        self.status["camera"] = f"camera {self.config.camera}" if camera else "no camera found: showing a test pattern"
        virtual = self._open_virtual_camera()
        interval = 1 / self.config.fps
        next_preview = 0.0
        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                live = None
                if camera is not None:
                    ok, live = camera.read()
                    if not ok:
                        live = None
                if live is None:
                    live = test_pattern(self.config.width, self.config.height, started)
                elif live.shape[:2] != (self.config.height, self.config.width):
                    live = cv2.resize(live, (self.config.width, self.config.height))

                frame = self._frame(started, live)
                if virtual is not None:
                    virtual.send(frame)
                if started >= next_preview:
                    # ~10 fps preview is plenty for the panel and keeps CPU low.
                    next_preview = started + 0.1
                    preview = cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1])))
                    self.preview_jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
                if virtual is not None:
                    virtual.sleep_until_next_frame()
                elif camera is None:
                    time.sleep(max(0.0, interval - (time.monotonic() - started)))
        finally:
            if camera is not None:
                camera.release()
            if virtual is not None:
                virtual.close()
