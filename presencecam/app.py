"""PresenceCam: control panel (browser, also from a phone) around the frame engine."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .engine import Engine, EngineConfig

PANEL = (Path(__file__).parent / "panel.html").read_bytes()


def make_handler(engine: Engine) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # keep the terminal quiet
            pass

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            if self.path == "/":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(PANEL)
            elif self.path == "/api/state":
                self._json(HTTPStatus.OK, engine.state())
            elif self.path == "/preview.mjpg":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while not engine.stopped.is_set():
                        jpeg = engine.preview_jpeg
                        if jpeg:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/source":
                    engine.set_source(str(body.get("source")), body.get("clip"))
                elif self.path == "/api/glitch":
                    engine.set_glitch(str(body.get("glitch")))
                elif self.path == "/api/record":
                    engine.start_recording(float(body.get("seconds", 30)))
                elif self.path == "/api/delete-clip":
                    engine.delete_clip(str(body.get("clip")))
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self._json(HTTPStatus.OK, engine.state())

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="USB camera -> loop/glitch -> virtual camera for Teams/Zoom/Meet.")
    parser.add_argument("--camera", type=int, default=0, help="camera index (0 = first USB/built-in camera)")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--clips", type=Path, default=Path("clips"), help="where recorded clips are kept")
    parser.add_argument("--no-virtual", action="store_true", help="preview only, no virtual camera")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to control it from your phone (anyone on the network could)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    engine = Engine(EngineConfig(args.camera, args.width, args.height, args.fps, args.clips, not args.no_virtual))
    threading.Thread(target=engine.run, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(engine))
    print(f"PresenceCam panel: http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stopped.set()
        server.server_close()


if __name__ == "__main__":
    main()
