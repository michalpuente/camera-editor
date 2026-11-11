"""Settings kept in a JSON file in the user's config folder (editable by hand too)."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# Shown to every app as the camera's name; it goes into a regsvr32 argument on Windows.
NAME_PATTERN = re.compile(r"^[\w .()\-]{1,48}$", re.UNICODE)


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "PresenceCam"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PresenceCam"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "presencecam"


@dataclass
class Config:
    camera_name: str = "PresenceCam"
    # The real camera, by the name the system shows; empty = the first one.
    input_device: str = ""
    width: int = 1280
    height: int = 720
    fps: int = 30
    clips_dir: str = ""

    def validate(self) -> Config:
        if not NAME_PATTERN.match(self.camera_name):
            raise ValueError("Nazwa kamery: 1-48 znaków, tylko litery, cyfry, spacje i . ( ) - _")
        if not (160 <= self.width <= 3840 and 120 <= self.height <= 2160 and 5 <= self.fps <= 60):
            raise ValueError("Rozdzielczość albo liczba klatek poza zakresem")
        return self

    def clips_path(self, base: Path | None = None) -> Path:
        return Path(self.clips_dir) if self.clips_dir else (base or config_dir()) / "clips"


def load(path: Path | None = None) -> Config:
    path = path or config_dir() / "config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Config()
    known = {field.name for field in fields(Config)}
    try:
        return Config(**{key: value for key, value in raw.items() if key in known}).validate()
    except (TypeError, ValueError):
        # A hand-edited file with a bad value must not stop the app from starting.
        return Config()


def save(config: Config, path: Path | None = None) -> None:
    path = path or config_dir() / "config.json"
    config.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
