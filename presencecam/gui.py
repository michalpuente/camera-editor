"""PresenceCam desktop window: camera in, loop/glitch control, named virtual camera out."""

from __future__ import annotations

import sys
import threading

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoFrame, QVideoSink
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
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


def real_cameras(names: list[str], exclude: str) -> list[str]:
    blocked = (*VIRTUAL_INPUTS, exclude.lower())
    return [name for name in names if not name.lower().startswith(blocked)]


class CameraInput:
    """Latest frame of the real camera, captured by Qt (shared: other apps can open it too)."""

    def __init__(self) -> None:
        self.session = QMediaCaptureSession()
        self.sink = QVideoSink()
        self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.camera: QCamera | None = None
        self.latest: np.ndarray | None = None

    def devices(self, exclude: str) -> list[str]:
        return real_cameras([device.description() for device in QMediaDevices.videoInputs()], exclude)

    def open(self, name: str, exclude: str) -> str | None:
        self.close()
        devices = [device for device in QMediaDevices.videoInputs() if device.description() in self.devices(exclude)]
        device = next((item for item in devices if item.description() == name), devices[0] if devices else None)
        if device is None:
            return None
        self.camera = QCamera(device)
        self.session.setCamera(self.camera)
        self.camera.start()
        return device.description()

    def close(self) -> None:
        if self.camera is not None:
            self.camera.stop()
        self.camera = None
        self.latest = None

    def _on_frame(self, frame: QVideoFrame) -> None:
        image = frame.toImage().convertToFormat(QImage.Format.Format_BGR888)
        if image.isNull():
            return
        width, height = image.width(), image.height()
        data = np.frombuffer(image.constBits(), np.uint8, count=image.bytesPerLine() * height)
        self.latest = data.reshape(height, image.bytesPerLine())[:, : width * 3].reshape(height, width, 3).copy()


class LockedCameraInput:
    """The real camera held through DirectShow (Windows), which opens it exclusively.

    Media Foundation, which Qt uses, shares a camera: Teams could still pick the real one
    next to the virtual one. A camera opened through DirectShow gives other apps no
    frames, so they report it as in use and the virtual camera is the only working choice.
    """

    def __init__(self, width: int, height: int, fps: int) -> None:
        self.size = (width, height, fps)
        self.latest: np.ndarray | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def devices(self, exclude: str) -> list[str]:
        from .dshow import video_inputs

        return real_cameras(video_inputs(), exclude)

    def open(self, name: str, exclude: str) -> str | None:
        from .dshow import video_inputs

        self.close()
        all_names = video_inputs()
        choices = real_cameras(all_names, exclude)
        chosen = name if name in choices else (choices[0] if choices else None)
        if chosen is None:
            return None
        self.stop.clear()
        self.thread = threading.Thread(target=self._capture, args=(all_names.index(chosen),), daemon=True)
        self.thread.start()
        return chosen

    def _capture(self, index: int) -> None:
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        width, height, _fps = self.size
        # MJPG: most USB cameras only reach 720p/1080p at full frame rate compressed. The order
        # matters on DirectShow: a size change after the format falls back to raw YUY2 at ~9 fps,
        # and so does setting the frame rate, so it is left to the camera.
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        try:
            while not self.stop.is_set():
                ok, frame = capture.read()
                self.latest = frame if ok else None
                if not ok:
                    self.stop.wait(0.5)
        finally:
            capture.release()

    def close(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.thread = None
        self.latest = None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = config_module.load()
        self.setWindowTitle("PresenceCam")
        self.camera_input = self._make_input()
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
        try:
            self.engine.set_queue([name for name in self.settings.loop_queue if name in self.engine.clips])
        except ValueError:
            pass
        self.engine_thread = threading.Thread(target=self.engine.run, daemon=True)
        self.engine_thread.start()

        self.preview = QLabel("…")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480, 270)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet("background: black; border-radius: 6px;")
        # While others see the loop or a glitch, the real camera stays visible here (to you only),
        # so you know what you are switching back to.
        self.live_inset = QLabel(self.preview)
        self.live_inset.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_inset.setStyleSheet("background: black; border: 2px solid #e04040; border-radius: 4px;")
        self.live_inset.setToolTip("Prawdziwa kamera na żywo: widzisz ją tylko Ty")
        self.live_inset.hide()
        self.live_caption = QLabel("NA ŻYWO · tylko Ty", self.preview)
        self.live_caption.setStyleSheet("background: #e04040; color: white; padding: 1px 6px; border-radius: 3px; font-weight: bold;")
        self.live_caption.hide()
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
        self.clips.setMaximumHeight(100)
        layout.addWidget(self.clips)
        actions = QHBoxLayout()
        use = QPushButton("Użyj w pętli")
        use.clicked.connect(self._use_clip)
        delete = QPushButton("Usuń")
        delete.clicked.connect(self._delete_clip)
        add = QPushButton("Do kolejki")
        add.clicked.connect(self._queue_clip)
        actions.addWidget(use)
        actions.addWidget(add)
        actions.addWidget(delete)
        layout.addLayout(actions)

        queue_hint = QLabel("Kolejka: nagrania lecą po kolei (przeciągnij, żeby zmienić kolejność). Pusta = jedno nagranie w kółko.")
        queue_hint.setWordWrap(True)
        layout.addWidget(queue_hint)
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(100)
        self.queue_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.queue_list.model().rowsMoved.connect(lambda *_: self._save_queue(self._queue_names()))
        layout.addWidget(self.queue_list)
        queue_actions = QHBoxLayout()
        remove = QPushButton("Usuń z kolejki")
        remove.clicked.connect(self._unqueue_clip)
        clear = QPushButton("Wyczyść kolejkę")
        clear.clicked.connect(lambda: self._save_queue([]))
        queue_actions.addWidget(remove)
        queue_actions.addWidget(clear)
        layout.addLayout(queue_actions)
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
        if sys.platform == "win32":
            self.lock_box = QCheckBox("Zajmij prawdziwą kamerę (inne programy widzą ją jako zajętą)")
            self.lock_box.setToolTip("Teams i inne programy nie mogą wtedy wybrać prawdziwej kamery, tylko wirtualną.")
            self.lock_box.setChecked(self.settings.lock_input)
            self.lock_box.toggled.connect(self._set_lock)
            layout.addWidget(self.lock_box, 4, 0, 1, 2)
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

    def _queue_names(self) -> list[str]:
        return [self.queue_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue_list.count())]

    def _save_queue(self, names: list[str]) -> None:
        def save() -> None:
            self.engine.set_queue(names)
            self.settings.loop_queue = names
            config_module.save(self.settings)

        self._guard(save)

    def _queue_clip(self) -> None:
        item = self.clips.currentItem()
        if item is not None:
            self._save_queue([*self.engine.queue, item.data(Qt.ItemDataRole.UserRole)])

    def _unqueue_clip(self) -> None:
        row = self.queue_list.currentRow()
        if row >= 0:
            names = list(self.engine.queue)
            del names[row]
            self._save_queue(names)

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
        for name in self.camera_input.devices(self.settings.camera_name):
            self.input_combo.addItem(name)
        if chosen:
            self.input_combo.setCurrentText(chosen)
        else:
            self.input_combo.addItem("Brak kamery — obraz testowy")

    def _choose_input(self, name: str) -> None:
        self.settings.input_device = name
        self._guard(lambda: config_module.save(self.settings))
        self._open_input(name)

    def _make_input(self) -> CameraInput | LockedCameraInput:
        if sys.platform == "win32" and self.settings.lock_input:
            return LockedCameraInput(self.settings.width, self.settings.height, self.settings.fps)
        return CameraInput()

    def _set_lock(self, locked: bool) -> None:
        self.settings.lock_input = locked
        self._guard(lambda: config_module.save(self.settings))
        self.camera_input.close()
        self.camera_input = self._make_input()
        self._open_input(self.settings.input_device)

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

    @staticmethod
    def _pixmap(frame: np.ndarray, width: int, height: int) -> QPixmap:
        image = QImage(frame.data, frame.shape[1], frame.shape[0], frame.strides[0], QImage.Format.Format_BGR888)
        return QPixmap.fromImage(image).scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def _refresh_preview(self) -> None:
        frame = self.engine.output_frame
        if frame is not None:
            self.preview.setPixmap(self._pixmap(frame, self.preview.width(), self.preview.height()))
        self._refresh_live_inset()

    def _refresh_live_inset(self) -> None:
        live = self.camera_input.latest
        state = self.engine.state()
        if live is None or (state["source"] == "live" and state["glitch"] == "none"):
            self.live_inset.hide()
            self.live_caption.hide()
            return
        pixmap = self._pixmap(live, max(160, self.preview.width() // 3), max(90, self.preview.height() // 3))
        margin = 10
        self.live_inset.setPixmap(pixmap)
        self.live_inset.resize(pixmap.width() + 4, pixmap.height() + 4)
        self.live_inset.move(self.preview.width() - self.live_inset.width() - margin, self.preview.height() - self.live_inset.height() - margin)
        self.live_caption.adjustSize()
        self.live_caption.move(self.live_inset.x() + 6, self.live_inset.y() + 6)
        self.live_inset.show()
        self.live_caption.show()
        self.live_caption.raise_()

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
        playing = state["queuePos"] if state["source"] == "loop" else -1
        queued = [f"{'▶ ' if index == playing else ''}{index + 1}. {name}" for index, name in enumerate(state["queue"])]
        if queued != [self.queue_list.item(i).text() for i in range(self.queue_list.count())]:
            selected = self.queue_list.currentRow()
            self.queue_list.clear()
            for name, text in zip(state["queue"], queued):
                self.queue_list.addItem(text)
                self.queue_list.item(self.queue_list.count() - 1).setData(Qt.ItemDataRole.UserRole, name)
            self.queue_list.setCurrentRow(min(selected, self.queue_list.count() - 1))
        self.status.setText(f"Wejście: {state.get('camera', '…')} · Wirtualna kamera: {state.get('virtualCamera', '…')}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.engine.stopped.set()
        self.engine_thread.join(timeout=2)
        self.camera_input.close()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("PresenceCam")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
