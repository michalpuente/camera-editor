"""The virtual camera device other apps see, per operating system.

Windows: the Unity Capture DirectShow filter (MIT), registered under the name the
user picks. Linux: v4l2loopback with that name as its label. macOS: OBS Virtual
Camera (its name is fixed; a custom name needs a signed camera extension).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DLLS = ("UnityCaptureFilter32.dll", "UnityCaptureFilter64.dll")


def driver_dir() -> Path:
    # Inside a PyInstaller build the files sit next to the bundled code.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "drivers" / "windows"


def backend(camera_name: str) -> tuple[str | None, str | None]:
    """pyvirtualcam backend and device for this OS."""
    if sys.platform == "win32":
        return "unitycapture", camera_name
    if sys.platform == "darwin":
        return "obs", None
    return "v4l2loopback", None


def regsvr32_commands(directory: Path, camera_name: str, uninstall: bool = False) -> list[list[str]]:
    if uninstall:
        return [["regsvr32", "/s", "/u", str(directory / dll)] for dll in DLLS]
    return [["regsvr32", "/s", str(directory / dll), f"/i:UnityCaptureName={camera_name}"] for dll in DLLS]


def install_windows(camera_name: str) -> None:
    """Registers (or renames) the camera; Windows asks once for administrator rights."""
    import ctypes

    directory = driver_dir()
    missing = [dll for dll in DLLS if not (directory / dll).exists()]
    if missing:
        raise FileNotFoundError(f"Brak plików sterownika w {directory}: {', '.join(missing)}")
    # Unregister first, so a rename never leaves the old name behind.
    steps = regsvr32_commands(directory, camera_name, uninstall=True) + regsvr32_commands(directory, camera_name)
    script = " & ".join(subprocess.list2cmdline(step) for step in steps)
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f"/c {script}", None, 0)
    if result <= 32:
        raise PermissionError("Nie przyznano uprawnień administratora, kamera nie została zainstalowana")


def uninstall_windows() -> None:
    import ctypes

    script = " & ".join(subprocess.list2cmdline(step) for step in regsvr32_commands(driver_dir(), "", uninstall=True))
    if ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f"/c {script}", None, 0) <= 32:
        raise PermissionError("Nie przyznano uprawnień administratora")


def setup_hint(camera_name: str) -> str:
    if sys.platform == "win32":
        return f'Kliknij raz "Zainstaluj / zmień nazwę kamery" (Windows zapyta o uprawnienia administratora). Aplikacje pokażą kamerę "{camera_name}".'
    if sys.platform == "darwin":
        return 'macOS: zainstaluj OBS Studio 30+, raz kliknij w nim "Start Virtual Camera" i zamknij OBS. Aplikacje pokażą "OBS Virtual Camera" (własna nazwa jest możliwa tylko na Windows i Linux).'
    return f'Linux: uruchom raz: sudo modprobe v4l2loopback exclusive_caps=1 card_label="{camera_name}"'
