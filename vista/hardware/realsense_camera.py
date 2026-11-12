"""Intel RealSense D456 / D457 driver (librealsense2 / pyrealsense2).

Notes for the station build:
* D457 ships in GMSL mode. For a Windows box PC over USB, open the small cap on the camera
  and move the interface switch to USB before connecting.
* Use a USB 3.x port and cable; the driver warns when the device enumerates as USB 2.
* D45x minimum depth distance is ~52 cm - mount the camera 0.9-1.3 m above the bench.
* Colour is aligned to the depth grid so depth, colour and IR share one pixel coordinate system.
"""
from __future__ import annotations

import logging
import time

import cv2
import numpy as np

from vista.hardware.camera_base import CameraBase, CameraError, Frame, Intrinsics

log = logging.getLogger(__name__)

SUPPORTED_MODELS = ("D456", "D457", "D455", "D435", "D415")

PRESETS = {"Default": 1, "Hand": 2, "High accuracy": 3, "High density": 4, "Medium density": 5}


def _rs():
    try:
        import pyrealsense2 as rs  # noqa: WPS433 - optional dependency, imported lazily
    except ImportError as exc:
        raise CameraError(
            "pyrealsense2 is not installed. Install the Intel RealSense SDK Python package "
            "(pip install pyrealsense2) or set CAMERA_BACKEND=sim for simulation.") from exc
    return rs


def list_devices() -> list[dict]:
    rs = _rs()
    out = []
    for dev in rs.context().query_devices():
        def gi(info):
            try:
                return dev.get_info(info)
            except RuntimeError:
                return ""
        out.append({"name": gi(rs.camera_info.name), "serial": gi(rs.camera_info.serial_number),
                    "firmware": gi(rs.camera_info.firmware_version),
                    "usb": gi(rs.camera_info.usb_type_descriptor)})
    return out


class RealSenseCamera(CameraBase):
    def __init__(self, serial: str = "", width: int = 848, height: int = 480, fps: int = 30):
        self.serial = serial
        self.width, self.height, self.fps = width, height, fps
        self.name = "RealSense"
        self._pipe = None
        self._align = None
        self._filters: list = []
        self._depth_scale_mm = 1.0
        self._intr: Intrinsics | None = None
        self._info: dict = {}
        self._seq = 0
        self._depth_sensor = None
        self._opts = {"preset": "High density", "laser_power": 150.0, "auto_exposure": True,
                      "spatial": True, "temporal": True, "hole_filling": True}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        rs = _rs()
        devices = list_devices()
        if not devices:
            raise CameraError("No RealSense camera detected. Check the USB 3 cable (and the USB switch on a D457).")
        dev = next((d for d in devices if d["serial"] == self.serial), None) if self.serial else devices[0]
        if dev is None:
            raise CameraError(f"RealSense with serial {self.serial} not found. Connected: "
                              + ", ".join(d["serial"] for d in devices))
        if not any(m in dev["name"] for m in SUPPORTED_MODELS):
            log.warning("Camera model %s is not in the validated list %s", dev["name"], SUPPORTED_MODELS)
        if dev["usb"] and dev["usb"].startswith("2"):
            log.warning("RealSense enumerated as USB %s - use a USB 3 port/cable for full frame rate", dev["usb"])
            if self.width == 848 and self.height == 480:
                log.info("Adapting RealSense resolution to 640x480 for USB 2.x compatibility")
                self.width, self.height = 640, 480

        def _make_config(w: int, h: int, fps: int):
            c = rs.config()
            c.enable_device(dev["serial"])
            c.enable_stream(rs.stream.depth, w, h, rs.format.z16, fps)
            c.enable_stream(rs.stream.color, w, h, rs.format.bgr8, fps)
            c.enable_stream(rs.stream.infrared, 1, w, h, rs.format.y8, fps)
            return c

        pipe = rs.pipeline()
        try:
            profile = pipe.start(_make_config(self.width, self.height, self.fps))
        except RuntimeError as exc:
            if self.width != 640 or self.height != 480:
                log.warning("Could not start streams at %dx%d@%d (%s) - attempting 640x480 fallback",
                            self.width, self.height, self.fps, exc)
                try:
                    self.width, self.height = 640, 480
                    profile = pipe.start(_make_config(640, 480, self.fps))
                except RuntimeError as exc2:
                    raise CameraError(f"Could not start RealSense streams: {exc2}") from exc2
            else:
                raise CameraError(f"Could not start RealSense streams {self.width}x{self.height}@{self.fps}: {exc}") from exc

        self._pipe = pipe
        self._depth_sensor = profile.get_device().first_depth_sensor()
        self._depth_scale_mm = self._depth_sensor.get_depth_scale() * 1000.0
        self._align = rs.align(rs.stream.depth)
        vp = profile.get_stream(rs.stream.depth).as_video_stream_profile().get_intrinsics()
        self._intr = Intrinsics(vp.width, vp.height, vp.fx, vp.fy, vp.ppx, vp.ppy)
        self._info = {**dev, "resolution": f"{self.width}x{self.height}@{self.fps}"}
        self.name = f"{dev['name']} ({dev['serial']})"
        self._build_filters()
        for k, v in self._opts.items():
            self._apply_option(k, v)
        # let auto-exposure settle
        for _ in range(10):
            try:
                pipe.wait_for_frames(2000)
            except RuntimeError:
                break
        log.info("RealSense started: %s", self._info)

    def stop(self) -> None:
        if self._pipe is not None:
            try:
                self._pipe.stop()
            except RuntimeError:
                pass
            self._pipe = None

    def _build_filters(self) -> None:
        rs = _rs()
        self._filters = []
        if self._opts["spatial"]:
            self._filters.append(rs.spatial_filter())
        if self._opts["temporal"]:
            self._filters.append(rs.temporal_filter())
        if self._opts["hole_filling"]:
            self._filters.append(rs.hole_filling_filter(1))

    # ------------------------------------------------------------------ options
    def options(self) -> dict:
        return dict(self._opts)

    def set_option(self, key: str, value) -> None:
        if key not in self._opts:
            raise KeyError(key)
        self._opts[key] = value
        if self._pipe is not None:
            self._apply_option(key, value)

    def _apply_option(self, key: str, value) -> None:
        rs = _rs()
        s = self._depth_sensor
        try:
            if key == "preset" and s.supports(rs.option.visual_preset):
                s.set_option(rs.option.visual_preset, PRESETS.get(value, 1))
            elif key == "laser_power" and s.supports(rs.option.laser_power):
                rng = s.get_option_range(rs.option.laser_power)
                s.set_option(rs.option.laser_power, float(min(max(value, rng.min), rng.max)))
            elif key == "auto_exposure" and s.supports(rs.option.enable_auto_exposure):
                s.set_option(rs.option.enable_auto_exposure, 1 if value else 0)
            elif key in ("spatial", "temporal", "hole_filling"):
                self._build_filters()
        except RuntimeError as exc:
            log.warning("Could not apply RealSense option %s=%s: %s", key, value, exc)

    # ------------------------------------------------------------------ frames
    def read(self, timeout_ms: int = 1000) -> Frame | None:
        if self._pipe is None:
            raise CameraError("Camera not started")
        try:
            frames = self._pipe.wait_for_frames(timeout_ms)
        except RuntimeError as exc:
            raise CameraError(f"RealSense frame timeout: {exc}") from exc
        ir_frame = frames.get_infrared_frame(1)
        aligned = self._align.process(frames)
        depth = aligned.get_depth_frame()
        color = aligned.get_color_frame()
        if not depth:
            return None
        for f in self._filters:
            depth = f.process(depth)
        depth_mm = np.asanyarray(depth.get_data()).astype(np.float32) * self._depth_scale_mm
        color_img = np.asanyarray(color.get_data()).copy() if color else None
        ir_img = np.asanyarray(ir_frame.get_data()).copy() if ir_frame else None
        if ir_img is not None:
            ir_img = cv2.GaussianBlur(ir_img, (5, 5), 0)   # suppress the emitter dot pattern
        self._seq += 1
        return Frame(ts=time.monotonic(), depth_mm=depth_mm, color=color_img, ir=ir_img, intrinsics=self._intr,
                     seq=self._seq)

    def info(self) -> dict:
        return dict(self._info)
