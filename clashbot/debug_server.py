from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from typing import Dict, Optional

from .engine.constants import TICKS_PER_SECOND
from .engine.simulation import GameEngine, GameOptions


WEB_ROOT = Path(__file__).resolve().parent / "web"


class DebugRuntime:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.engine = GameEngine()
        self.paused = False
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        self._thread.join(timeout=1.0)

    def reset(self) -> Dict:
        with self.lock:
            self.engine = GameEngine()
            self.paused = False
            return self.engine.snapshot()

    def snapshot(self) -> Dict:
        with self.lock:
            return self.engine.snapshot()

    def play(self, payload: Dict) -> Dict:
        with self.lock:
            command = self.engine.submit_play(
                side=str(payload["side"]),
                hand_slot=int(payload["handSlot"]),
                x=float(payload["x"]),
                y=float(payload["y"]),
                client_tick=int(payload["clientTick"]) if payload.get("clientTick") is not None else None,
            )
            return {
                "queued": True,
                "executeTick": command.execute_tick,
                "sequence": command.sequence,
                "state": self.engine.snapshot(),
            }

    def set_pause(self, paused: bool) -> Dict:
        with self.lock:
            self.paused = paused
            return {"paused": self.paused, "state": self.engine.snapshot()}

    def step_once(self) -> Dict:
        with self.lock:
            self.engine.step()
            return self.engine.snapshot()

    def replay(self) -> Dict:
        with self.lock:
            return self.engine.export_replay()

    def _loop(self) -> None:
        frame_time = 1.0 / TICKS_PER_SECOND
        next_time = time.monotonic()
        while self.running:
            now = time.monotonic()
            if now >= next_time:
                with self.lock:
                    if not self.paused:
                        self.engine.step()
                next_time += frame_time
                if next_time < now - frame_time:
                    next_time = now + frame_time
            else:
                time.sleep(min(0.005, next_time - now))


def make_handler(runtime: DebugRuntime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ClashBotDebug/0.1"

        def do_GET(self) -> None:
            if self.path == "/api/state":
                self._send_json(runtime.snapshot())
                return
            if self.path == "/api/replay":
                self._send_json(runtime.replay())
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                path = "/index.html"
            self._send_static(path)

        def do_POST(self) -> None:
            try:
                payload = self._read_json()
                if self.path == "/api/play":
                    self._send_json(runtime.play(payload))
                elif self.path == "/api/reset":
                    self._send_json(runtime.reset())
                elif self.path == "/api/pause":
                    self._send_json(runtime.set_pause(bool(payload.get("paused"))))
                elif self.path == "/api/step":
                    self._send_json(runtime.step_once())
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            except Exception as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def log_message(self, fmt: str, *args) -> None:
            return

        def _read_json(self) -> Dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _send_json(self, payload: Dict) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, path: str) -> None:
            safe = path.lstrip("/")
            target = (WEB_ROOT / safe).resolve()
            if WEB_ROOT not in target.parents and target != WEB_ROOT:
                self._send_error(HTTPStatus.FORBIDDEN, "forbidden")
                return
            if not target.exists() or not target.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "not found")
                return
            ctype = "text/plain"
            if target.suffix == ".html":
                ctype = "text/html; charset=utf-8"
            elif target.suffix == ".js":
                ctype = "text/javascript; charset=utf-8"
            elif target.suffix == ".css":
                ctype = "text/css; charset=utf-8"
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            body = json.dumps({"error": message}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    runtime = DebugRuntime()
    runtime.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(runtime))
    print("ClashBot debug server listening on http://%s:%d" % (args.host, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
        server.server_close()


if __name__ == "__main__":
    main()

