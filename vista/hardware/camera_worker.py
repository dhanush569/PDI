"""Camera thread: grab -> height map -> detection engine -> Qt signals."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from PySide6.QtCore import QThread, Signal

from vista.hardware.camera_base import CameraError

log = logging.getLogger(__name__)


class CameraWorker(QThread):
    frame_ready = Signal(object)     # Frame, throttled for UI
    states_ready = Signal(object)    # dict[int, DetState]
    status = Signal(dict)

    def __init__(self, ctx, camera, vision_cam=None, ui_fps: int = 15):
        super().__init__()
        self.setObjectName("CameraWorker")
        self.ctx, self.camera, self.vision_cam = ctx, camera, vision_cam
        self.ui_period = 1.0 / ui_fps
        self._running = True
        self._lock = threading.Lock()
        self._latest = None
        self._recent: deque = deque(maxlen=20)
        self.fps = 0.0
        self.connected = False

    def latest(self):
        with self._lock:
            return self._latest

    def recent(self, n: int):
        with self._lock:
            return list(self._recent)[-n:]

    def stop(self) -> None:
        self._running = False
        self.wait(4000)

    def run(self) -> None:
        while self._running is not None:
            try:
                self._loop()
                return
            except Exception as exc:          # never let the camera thread die silently
                log.exception("Camera thread error - restarting")
                self.status.emit({"camera": "error", "error": f"Camera thread error: {exc}"})
                self.connected = False
                try:
                    self.camera.stop()
                except Exception:
                    pass
                time.sleep(2.0)
                if not self._running:
                    return

    def _loop(self) -> None:
        self._running = True
        last_ui, n, t_fps = 0.0, 0, time.monotonic()
        while self._running:
            if not self.connected:
                try:
                    self.camera.start()
                    if self.vision_cam is not None:
                        try:
                            self.vision_cam.start()
                        except Exception as exc:
                            log.warning("Vision camera unavailable: %s", exc)
                            self.vision_cam = None
                    self.connected = True
                    self.status.emit({"camera": "ok", "info": self.camera.info()})
                except CameraError as exc:
                    self.status.emit({"camera": "error", "error": str(exc)})
                    log.error("%s", exc)
                    for _ in range(30):
                        if not self._running:
                            return
                        time.sleep(0.1)
                    continue
            try:
                frame = self.camera.read(1500)
            except CameraError as exc:
                log.error("Camera read failed: %s - reconnecting", exc)
                self.status.emit({"camera": "error", "error": str(exc)})
                self.connected = False
                try:
                    self.camera.stop()
                except Exception:
                    pass
                continue
            if frame is None:
                continue
            cal = self.ctx.calibration
            if cal is not None and frame.depth_mm is not None:
                try:
                    frame.height_mm = cal.height_map(frame.depth_mm, frame.intrinsics)
                except Exception as exc:
                    log.debug("Height map calculation failed: %s", exc)
            if self.vision_cam is not None:
                frame.vision = self.vision_cam.latest()
            states = self.ctx.engine.process(frame)
            with self._lock:
                self._latest = frame
                self._recent.append(frame)
            self.states_ready.emit(states)
            n += 1
            now = time.monotonic()
            if now - t_fps >= 1.0:
                self.fps = n / (now - t_fps)
                n, t_fps = 0, now
                self.status.emit({"camera": "ok", "fps": round(self.fps, 1)})
            if now - last_ui >= self.ui_period:
                last_ui = now
                self.frame_ready.emit(frame)
        try:
            self.camera.stop()
            if self.vision_cam is not None:
                self.vision_cam.stop()
        except Exception:
            pass
