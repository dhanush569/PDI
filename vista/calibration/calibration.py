"""Workstation calibration: camera <-> bench (mm) <-> projector.

Bench frame (like Arkite): X to the right, Y away from the operator (image-up), Z up from the
bench surface, origin at the bottom-left calibration marker. All detections and projections are
stored in this frame, so a projector or camera re-calibration does not invalidate teaching
geometry stored in millimetres.

Procedure (fully automatic, Arkite requires clicking every marker by hand):
  1. Projector shows black -> reference frame.
  2. Each marker of a 3x3 grid is projected one at a time; its centre is found in the camera
     image from the difference to the reference (sub-pixel centroid).
  3. The bench plane is fitted to the depth data with RANSAC.
  4. Marker centres are deprojected onto the plane -> bench mm.
  5. Homography bench mm -> projector px (and optional vision camera px -> bench mm).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from vista.hardware.camera_base import Intrinsics


class CalibrationError(RuntimeError):
    pass


def marker_grid(proj_w: int, proj_h: int, margin: float = 0.12, cols: int = 3, rows: int = 3) -> list[tuple[float, float]]:
    xs = np.linspace(proj_w * margin, proj_w * (1 - margin), cols)
    ys = np.linspace(proj_h * margin, proj_h * (1 - margin), rows)
    return [(float(x), float(y)) for y in ys for x in xs]   # row-major, row 0 = top of projector image


def find_marker(reference: np.ndarray, lit: np.ndarray, min_contrast: int = 25) -> tuple[float, float] | None:
    """Centre of the bright projected disk visible in `lit` but not in `reference` (BGR or gray)."""
    g0 = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY) if reference.ndim == 3 else reference
    g1 = cv2.cvtColor(lit, cv2.COLOR_BGR2GRAY) if lit.ndim == 3 else lit
    diff = cv2.GaussianBlur(cv2.subtract(g1, g0), (5, 5), 0)
    peak = int(diff.max())
    if peak < min_contrast:
        return None
    _, mask = cv2.threshold(diff, max(min_contrast, peak * 0.5), 255, cv2.THRESH_BINARY)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return None
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[idx, cv2.CC_STAT_AREA] < 6:
        return None
    w = np.where(labels == idx, diff.astype(np.float64), 0.0)
    m = cv2.moments(w)
    if m["m00"] <= 0:
        return None
    return m["m10"] / m["m00"], m["m01"] / m["m00"]


def fit_plane_ransac(points: np.ndarray, iters: int = 300, tol: float = 4.0, seed: int = 1):
    """Returns unit normal n and d (n.P + d = 0) oriented so the camera (origin) has positive height."""
    if len(points) < 50:
        raise CalibrationError("Not enough valid depth points to fit the bench plane.")
    rng = np.random.default_rng(seed)
    best_inl = None
    for _ in range(iters):
        p = points[rng.choice(len(points), 3, replace=False)]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        norm = np.linalg.norm(n)
        if norm < 1e-6:
            continue
        n /= norm
        d = -n.dot(p[0])
        inl = np.abs(points @ n + d) < tol
        if best_inl is None or inl.sum() > best_inl.sum():
            best_inl = inl
    if best_inl is None or best_inl.sum() < 0.3 * len(points):
        raise CalibrationError("Bench plane not found - clear the calibration area of parts and fixtures.")
    pts = points[best_inl]
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    n = vt[2]
    d = -n.dot(c)
    if d < 0:
        n, d = -n, -d
    return n, float(d), float(best_inl.mean())


@dataclass
class Calibration:
    intrinsics: Intrinsics
    n: np.ndarray
    d: float
    origin: np.ndarray
    ex: np.ndarray
    ey: np.ndarray
    H_bench2proj: np.ndarray
    proj_size: tuple[int, int]
    rms_px: float
    created: float = field(default_factory=time.time)
    H_vision2bench: np.ndarray | None = None
    _K: np.ndarray | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ serialisation
    def to_dict(self) -> dict:
        return {
            "intrinsics": self.intrinsics.to_dict(), "n": self.n.tolist(), "d": self.d,
            "origin": self.origin.tolist(), "ex": self.ex.tolist(), "ey": self.ey.tolist(),
            "H_bench2proj": np.asarray(self.H_bench2proj).tolist(), "proj_size": list(self.proj_size),
            "rms_px": self.rms_px, "created": self.created,
            "H_vision2bench": None if self.H_vision2bench is None else np.asarray(self.H_vision2bench).tolist(),
        }

    @staticmethod
    def from_dict(d: dict) -> "Calibration":
        return Calibration(
            intrinsics=Intrinsics.from_dict(d["intrinsics"]), n=np.array(d["n"], float), d=float(d["d"]),
            origin=np.array(d["origin"], float), ex=np.array(d["ex"], float), ey=np.array(d["ey"], float),
            H_bench2proj=np.array(d["H_bench2proj"], float), proj_size=tuple(d["proj_size"]),
            rms_px=float(d.get("rms_px", 0)), created=float(d.get("created", 0)),
            H_vision2bench=None if d.get("H_vision2bench") is None else np.array(d["H_vision2bench"], float))

    # ------------------------------------------------------------------ geometry
    def _rays(self, depth_shape: tuple[int, int] | None = None, intr: Intrinsics | None = None):
        if intr is not None:
            i = intr
        else:
            i = self.intrinsics
            if depth_shape is not None and (depth_shape[0] != i.height or depth_shape[1] != i.width):
                sx = depth_shape[1] / float(i.width)
                sy = depth_shape[0] / float(i.height)
                i = Intrinsics(depth_shape[1], depth_shape[0], i.fx * sx, i.fy * sy, i.cx * sx, i.cy * sy)
        uu, vv = np.meshgrid(np.arange(i.width, dtype=np.float32), np.arange(i.height, dtype=np.float32))
        return (uu - i.cx) / i.fx, (vv - i.cy) / i.fy

    def height_map(self, depth_mm: np.ndarray, intrinsics: Intrinsics | None = None) -> np.ndarray:
        """Height above the bench (mm) for every pixel; NaN where depth is invalid."""
        if self._K is None or self._K.shape != depth_mm.shape:
            rx, ry = self._rays(depth_shape=depth_mm.shape, intr=intrinsics)
            self._K = (self.n[0] * rx + self.n[1] * ry + self.n[2]).astype(np.float32)
        h = depth_mm * self._K + np.float32(self.d)
        h[depth_mm <= 0] = np.nan
        return h

    def pixel_to_bench(self, u: float, v: float, height: float = 0.0,
                       intrinsics: Intrinsics | None = None) -> tuple[float, float]:
        i = intrinsics or self.intrinsics
        r = np.array([(u - i.cx) / i.fx, (v - i.cy) / i.fy, 1.0])
        denom = self.n.dot(r)
        if abs(denom) < 1e-9:
            raise CalibrationError("Ray parallel to bench")
        t = (height - self.d) / denom
        p = t * r - self.origin
        return float(p.dot(self.ex)), float(p.dot(self.ey))

    def bench_to_pixel(self, x: float, y: float, height: float = 0.0,
                       intrinsics: Intrinsics | None = None) -> tuple[float, float]:
        p = self.origin + x * self.ex + y * self.ey + height * self.n
        i = intrinsics or self.intrinsics
        return float(i.fx * p[0] / p[2] + i.cx), float(i.fy * p[1] / p[2] + i.cy)

    def pixel_poly_to_bench(self, poly_px, height: float = 0.0) -> list[list[float]]:
        return [list(self.pixel_to_bench(u, v, height)) for u, v in poly_px]

    def camera_to_bench(self, pts, height: float = 0.0) -> np.ndarray:
        return np.array(self.pixel_poly_to_bench(pts, height), dtype=float)

    def bench_to_proj(self, pts) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.H_bench2proj).reshape(-1, 2)

    def proj_to_bench(self, pts) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, np.linalg.inv(self.H_bench2proj)).reshape(-1, 2)

    def mm_per_px(self) -> float:
        a = np.array(self.pixel_to_bench(self.intrinsics.cx, self.intrinsics.cy))
        b = np.array(self.pixel_to_bench(self.intrinsics.cx + 10, self.intrinsics.cy))
        return float(np.linalg.norm(b - a) / 10)


def deproject(intr: Intrinsics, depth_mm: np.ndarray, u: float, v: float, win: int = 3) -> np.ndarray | None:
    ui, vi = int(round(u)), int(round(v))
    patch = depth_mm[max(0, vi - win):vi + win + 1, max(0, ui - win):ui + win + 1]
    vals = patch[patch > 0]
    if vals.size < 3:
        return None
    z = float(np.median(vals))
    return np.array([(u - intr.cx) / intr.fx * z, (v - intr.cy) / intr.fy * z, z])


def solve_calibration(intr: Intrinsics, depth_mm: np.ndarray, cam_pts: list[tuple[float, float]],
                      proj_pts: list[tuple[float, float]], proj_size: tuple[int, int],
                      origin_index: int, xaxis_index: int) -> Calibration:
    if len(cam_pts) < 4:
        raise CalibrationError("At least 4 markers must be detected.")
    # 1. bench plane from depth inside the marker hull (shrunk to avoid edges)
    hull = cv2.convexHull(np.array(cam_pts, np.float32))
    mask = np.zeros(depth_mm.shape, np.uint8)
    cv2.fillConvexPoly(mask, hull.astype(np.int32), 1)
    vv, uu = np.nonzero((mask > 0) & (depth_mm > 0))
    if len(uu) > 20000:
        sel = np.random.default_rng(3).choice(len(uu), 20000, replace=False)
        uu, vv = uu[sel], vv[sel]
    z = depth_mm[vv, uu].astype(np.float64)
    pts3 = np.column_stack(((uu - intr.cx) / intr.fx * z, (vv - intr.cy) / intr.fy * z, z))
    n, d, inlier_ratio = fit_plane_ransac(pts3)

    # 2. markers on the plane (ray/plane intersection is more precise than raw depth)
    def on_plane(u, v):
        r = np.array([(u - intr.cx) / intr.fx, (v - intr.cy) / intr.fy, 1.0])
        return (-d / n.dot(r)) * r

    P = np.array([on_plane(u, v) for u, v in cam_pts])
    origin = P[origin_index]
    ex = P[xaxis_index] - origin
    ex -= n * ex.dot(n)
    if np.linalg.norm(ex) < 1e-3:
        raise CalibrationError("Origin and X-axis markers coincide.")
    ex /= np.linalg.norm(ex)
    ey = np.cross(n, ex)
    bench = np.array([[(p - origin).dot(ex), (p - origin).dot(ey)] for p in P])
    H, _ = cv2.findHomography(bench, np.array(proj_pts, np.float64), cv2.RANSAC if len(P) >= 6 else 0, 4.0)
    if H is None:
        raise CalibrationError("Projector homography could not be computed - markers may be collinear.")
    reproj = cv2.perspectiveTransform(bench.reshape(-1, 1, 2), H).reshape(-1, 2)
    rms = float(np.sqrt(np.mean(np.sum((reproj - np.array(proj_pts)) ** 2, axis=1))))
    cal = Calibration(intr, n, d, origin, ex, ey, H, proj_size, rms)
    cal.plane_inlier_ratio = inlier_ratio  # type: ignore[attr-defined]
    return cal


def solve_vision_homography(cal: Calibration, vision_pts: list[tuple[float, float]],
                            proj_pts: list[tuple[float, float]]) -> np.ndarray:
    if len(vision_pts) < 4:
        raise CalibrationError("At least 4 markers must be visible in the vision camera.")
    bench = cal.proj_to_bench(proj_pts)
    H, _ = cv2.findHomography(np.array(vision_pts, np.float64), bench, cv2.RANSAC if len(vision_pts) >= 6 else 0, 3.0)
    if H is None:
        raise CalibrationError("Vision camera homography failed.")
    return H
