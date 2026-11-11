"""PresenceCam desktop window: camera in, loop/glitch control, named virtual camera out."""

from __future__ import annotations

import sys
import threading

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoFrame, QVideoSink
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import config as config_module
from . import driver
from .engine import Engine, EngineConfig

# Virtual cameras are never offered as the input: picking one would feed the output back into itself.
VIRTUAL_INPUTS = ("obs virtual camera", "unity video capture", "presencecam")

SOURCES = (("live", "Na żywo", "L"), ("loop", "Pętla z nagrania", "P"))
GLITCHES = (
    ("none", "Brak", "N"),
    ("freeze", "Zamrożenie", "F"),
    ("weak", "Słaby internet", "W"),
    ("choppy", "Przycinanie", "C"),
)


class CameraInput:
    """Latest frame of the real camera, captured by Qt (names devices like the OS does)."""

    def __init__(self) -> None:
        self.session = QMediaCaptureSession()
        self.sink = QVideoSink()
        self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.camera: QCamera | None = None
        self.latest: np.ndarray | None = None

    @staticmethod
    def devices(exclude: str) -> list:
        blocked = (*VIRTUAL_INPUTS, exclude.lower())
        return [device for device in QMediaDevices.videoInputs() if not device.description().lower().startswith(blocked)]

    def open(self, name: str, exclude: str) -> str | None:
        if self.camera is not None:
            self.camera.stop()
        self.latest = None
        devices = self.devices(exclude)
        device = next((item for item in devices if item.description() == name), devices[0] if devices else None)
        if device is None:
            self.camera = None
            return None
        self.camera = QCamera(device)
        self.session.setCamera(self.camera)
        self.camera.start()
        return device.description()

    def _on_frame(self, frame: QVideoFrame) -> None:
        image = frame.toImage().convertToFormat(QImage.Format.Format_BGR888)
        if image.isNull():
            return
        width, height = image.width(), image.height()
        data = np.frombuffer(image.constBits(), np.uint8, count=image.bytesPerLine() * height)
        self.latest = data.reshape(height, image.bytesPerLine())[:, : width * 3].reshape(height, width, 3).copy()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = config_module.load()
        self.setWindowTitle("PresenceCam")
        self.camera_input = CameraInput()
        backend, device = driver.backend(self.settings.camera_name)
        self.engine = Engine(
            EngineConfig(
                width=self.settings.width,
                height=self.settings.height,
                fps=self.settings.fps,
                clips_dir=self.settings.clips_path(),
                read_frame=lambda: self.camera_input.latest,
                virtual_backend=backend,
                virtual_device=device,
            )
        )
        self.engine_thread = threading.Thread(target=self.engine.run, daemon=True)
        self.engine_thread.start()

        self.preview = QLabel("…")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480, 270)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet("background: black; border-radius: 6px;")
        self.status = QLabel()
        self.status.setWordWrap(True)

        controls = QVBoxLayout()
        controls.addWidget(self._choice_group("Co widzą inni", SOURCES, self._set_source, "source"))
        controls.addWidget(self._choice_group("Problemy z połączeniem", GLITCHES, self._set_glitch, "glitch"))
        controls.addWidget(self._clips_group())
        controls.addWidget(self._settings_group())
        controls.addStretch()

        left = QVBoxLayout()
        left.addWidget(self.preview, 1)
        left.addWidget(self.status)
        root = QHBoxLayout()
        root.addLayout(left, 3)
        root.addLayout(controls, 2)
        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)
        self.resize(1200, 700)

        self._open_input(self.settings.input_device)
        self.preview_timer = QTimer(self, interval=66, timeout=self._refresh_preview)
        self.preview_timer.start()
        self.state_timer = QTimer(self, interval=500, timeout=self._refresh_state)
        self.state_timer.start()
        self._refresh_state()

    # --- layout ---

    def _choice_group(self, title: str, choices, handler, name: str) -> QGroupBox:
        group = QGroupBox(title)
        layout = QGridLayout(group)
        buttons = QButtonGroup(self)
        buttons.setExclusive(True)
        for index, (value, label, key) in enumerate(choices):
            button = QPushButton(f"{label}  [{key}]")
            button.setCheckable(True)
            button.setMinimumHeight(36)
            button.clicked.connect(lambda _checked=False, v=value: handler(v))
            buttons.addButton(button)
            button.setProperty("value", value)
            layout.addWidget(button, index // 2, index % 2)
            QShortcut(QKeySequence(key), self, activated=lambda v=value: handler(v))
        setattr(self, f"{name}_buttons", buttons)
        return group

    def _clips_group(self) -> QGroupBox:
        group = QGroupBox("Nagrania do pętli")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        for seconds in (15, 30, 60):
            button = QPushButton(f"Nagraj {seconds} s")
            button.clicked.connect(lambda _checked=False, s=seconds: self._guard(lambda: self.engine.start_recording(s)))
            row.addWidget(button)
        layout.addLayout(row)
        self.recording_label = QLabel("Patrz w ekran, czasem kiwnij głową, nie mów.")
        self.recording_label.setWordWrap(True)
        layout.addWidget(self.recording_label)
        self.clips = QListWidget()
        self.clips.setMaximumHeight(120)
        layout.addWidget(self.clips)
        actions = QHBoxLayout()
        use = QPushButton("Użyj w pętli")
        use.clicked.connect(self._use_clip)
        delete = QPushButton("Usuń")
        delete.clicked.connect(self._delete_clip)
        actions.addWidget(use)
        actions.addWidget(delete)
        layout.addLayout(actions)
        return group

    def _settings_group(self) -> QGroupBox:
        group = QGroupBox("Ustawienia")
        layout = QGridLayout(group)
        layout.addWidget(QLabel("Kamera (wejście)"), 0, 0)
        self.input_combo = QComboBox()
        self.input_combo.activated.connect(lambda _index: self._choose_input(self.input_combo.currentText()))
        layout.addWidget(self.input_combo, 0, 1)
        layout.addWidget(QLabel("Nazwa wirtualnej kamery"), 1, 0)
        self.name_edit = QLineEdit(self.settings.camera_name)
        self.name_edit.setMaxLength(48)
        layout.addWidget(self.name_edit, 1, 1)
        self.install_button = QPushButton("Zainstaluj / zmień nazwę kamery" if sys.platform == "win32" else "Zapisz nazwę")
        self.install_button.clicked.connect(self._install_camera)
        layout.addWidget(self.install_button, 2, 0, 1, 2)
        self.hint = QLabel(driver.setup_hint(self.settings.camera_name))
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint, 3, 0, 1, 2)
        return group

    # --- actions ---

    def _guard(self, action) -> None:
        try:
            action()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "PresenceCam", str(error))
        self._refresh_state()

    def _set_source(self, source: str) -> None:
        self._guard(lambda: self.engine.set_source(source))

    def _set_glitch(self, glitch: str) -> None:
        self._guard(lambda: self.engine.set_glitch(glitch))

    def _use_clip(self) -> None:
        item = self.clips.currentItem()
        if item is not None:
            self._guard(lambda: self.engine.set_source("loop", item.data(Qt.ItemDataRole.UserRole)))

    def _delete_clip(self) -> None:
        item = self.clips.currentItem()
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(self, "PresenceCam", f"Usunąć {name}?") == QMessageBox.StandardButton.Yes:
            self._guard(lambda: self.engine.delete_clip(name))

    def _open_input(self, name: str) -> None:
        chosen = self.camera_input.open(name, self.settings.camera_name)
        self.input_combo.clear()
        for device in CameraInput.devices(self.settings.camera_name):
            self.input_combo.addItem(device.description())
        if chosen:
            self.input_combo.setCurrentText(chosen)
        else:
            self.input_combo.addItem("Brak kamery — obraz testowy")

    def _choose_input(self, name: str) -> None:
        self.settings.input_device = name
        self._guard(lambda: config_module.save(self.settings))
        self._open_input(name)

    def _install_camera(self) -> None:
        name = self.name_edit.text().strip()
        previous = self.settings.camera_name
        try:
            self.settings.camera_name = name
            config_module.save(self.settings)
            if sys.platform == "win32":
                driver.install_windows(name)
        except (ValueError, OSError) as error:
            self.settings.camera_name = previous
            QMessageBox.warning(self, "PresenceCam", str(error))
            return
        self.hint.setText(driver.setup_hint(name))
        # regsvr32 runs in its own elevated process: give it a moment, then reconnect under the new name.
        QTimer.singleShot(3000, lambda: self.engine.set_virtual_device(*driver.backend(name)))
        QMessageBox.information(
            self,
            "PresenceCam",
            f'Kamera "{name}" jest zapisana. W Teams/Zoom wybierz ją w ustawieniach urządzeń '
            "(aplikację, która była już otwarta, czasem trzeba uruchomić ponownie).",
        )

    # --- refresh ---

    def _refresh_preview(self) -> None:
        frame = self.engine.output_frame
        if frame is None:
            return
        height, width = frame.shape[:2]
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_BGR888)
        self.preview.setPixmap(
            QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    def _refresh_state(self) -> None:
        state = self.engine.state()
        for group, key in ((self.source_buttons, "source"), (self.glitch_buttons, "glitch")):
            for button in group.buttons():
                button.setChecked(button.property("value") == state[key])
        recording = state["recording"]
        self.recording_label.setText(
            f"● Nagrywam: zostało {recording['remaining']} s" if recording else "Patrz w ekran, czasem kiwnij głową, nie mów."
        )
        names = [f"{'▶ ' if clip['name'] == state['activeClip'] else ''}{clip['name']} · {clip['seconds']} s" for clip in state["clips"]]
        if names != [self.clips.item(i).text() for i in range(self.clips.count())]:
            selected = self.clips.currentRow()
            self.clips.clear()
            for clip, text in zip(state["clips"], names):
                self.clips.addItem(text)
                self.clips.item(self.clips.count() - 1).setData(Qt.ItemDataRole.UserRole, clip["name"])
            self.clips.setCurrentRow(min(selected, self.clips.count() - 1))
        self.status.setText(f"Wejście: {state.get('camera', '…')} · Wirtualna kamera: {state.get('virtualCamera', '…')}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.engine.stopped.set()
        self.engine_thread.join(timeout=2)
        if self.camera_input.camera is not None:
            self.camera_input.camera.stop()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("PresenceCam")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
