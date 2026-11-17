"""Detection algorithms and teaching.

OBJECT   - presence from depth (or IR) compared against taught WITH / WITHOUT references,
           with occlusion handling (operator arm above the zone keeps the last stable state).
ACTIVITY - operator hand inside a zone for a minimum dwell, completed when the hand leaves.
BUTTON   - projected virtual button: a hand/fist held at a low height band over the zone.
QUALITY  - vision check: live ROI is compared to sample images of CORRECT / WRONG / MISSING
           classes with texture (NCC), shape (gradient NCC) or histogram matching; AUTO picks
           the algorithm with the best leave-one-out separation at teach time.
"""
from __future__ import annotations

import io
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

TOLERANCE = {"EXACT": 0.0, "ACCURATE": 0.2, "NARROW": 0.5, "WIDE": 1.0}
QC_TYPES = ("CORRECT", "WRONG", "MISSING")
QC_ALGOS = ("AUTO", "TEXTURE", "SHAPE", "HISTOGRAM")

OBJECT_DEFAULTS = {"present_score": 0.60, "absent_score": 0.35, "occlusion_max": 0.30, "occlusion_mm": 35.0,
                   "min_diff": 6.0, "debounce_ms": 250, "tolerance": "EXACT"}
ACTIVITY_DEFAULTS = {"min_rise_mm": 15.0, "max_rise_mm": 450.0, "enter_ratio": 0.15, "min_dwell_ms": 400,
                     "exit_ms": 250, "complete_on": "LEAVE"}
BUTTON_DEFAULTS = {"press_min_mm": 15.0, "press_max_mm": 160.0, "press_ratio": 0.35, "hold_ms": 350,
                   "function": "NONE"}
QUALITY_DEFAULTS = {"min_score": 0.70, "min_margin": 0.03, "stable_frames": 3, "search_px": 10,
                    "occlusion_mm": 40.0, "occlusion_max": 0.20, "algorithm": "AUTO"}


class TeachError(RuntimeError):
    pass


@dataclass
class DetState:
    det_id: int
    name: str
    det_type: str
    state: str = "UNTAUGHT"     # PRESENT ABSENT OCC_PRESENT OCC_ABSENT ACTIVE IDLE PRESSED OK NG MISSING
    score: float = 0.0          #   UNKNOWN OCCLUDED UNTAUGHT DISABLED
    counter: int = 0
    qc_class: str | None = None
    qc_type: str | None = None
    ts: float = 0.0
    detail: dict = field(default_factory=dict)


def pack_arrays(**arrays) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def unpack_arrays(blob: bytes) -> dict:
    with np.load(io.BytesIO(bytes(blob)), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise TeachError("Image encoding failed")
    return buf.tobytes()


def decode_png(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(bytes(data), np.uint8), cv2.IMREAD_UNCHANGED)


def _crop(img: np.ndarray, roi) -> np.ndarray:
    x, y, w, h = [int(v) for v in roi]
    return img[y:y + h, x:x + w]


def signal_image(frame, sensor: str) -> np.ndarray | None:
    if sensor == "IR":
        return None if frame.ir is None else frame.ir.astype(np.float32)
    if frame.height_mm is not None:
        return frame.height_mm
    return frame.depth_mm


def median_stack(imgs: list[np.ndarray]) -> np.ndarray:
    if not imgs:
        raise TeachError("No frames captured")
    return np.nanmedian(np.stack(imgs, axis=0), axis=0).astype(np.float32)


# =============================================================================== OBJECT
class ObjectDetector:
    det_type = "OBJECT"

    def __init__(self, det: dict, arrays: dict):
        self.det = det
        self.p = {**OBJECT_DEFAULTS, **(det.get("params") or {})}
        m = det["model"]
        self.roi = m["roi"]
        self.sensor = det["sensor"] if det["sensor"] in ("DEPTH", "IR") else "DEPTH"
        self.mask = arrays["mask"].astype(bool)
        self.ref_with = arrays["ref_with"]
        self.ref_without = arrays["ref_without"]
        self.h_with = arrays.get("h_with")
        self.fill_with = float(m.get("fill_with", 1.0))
        self.fill_without = float(m.get("fill_without", 0.0))
        self.stable = "ABSENT"
        self._cand, self._cand_since = None, 0.0

    def measure(self, frame) -> tuple[float, float]:
        sig_full = signal_image(frame, self.sensor)
        if sig_full is None:
            return 0.0, 0.0
        sig = _crop(sig_full, self.roi)
        if sig.shape != self.mask.shape:
            return 0.0, 0.0
        thr = self.p["min_diff"] if self.sensor == "DEPTH" else max(12.0, self.p["min_diff"] * 2)
        valid = self.mask & np.isfinite(sig)
        dw, do = np.abs(sig - self.ref_with), np.abs(sig - self.ref_without)
        occluded = np.zeros_like(valid)
        if frame.height_mm is not None:
            h = _crop(frame.height_mm, self.roi)
            if h.shape == self.mask.shape:
                top = self.h_with if self.h_with is not None else (self.ref_with if self.sensor == "DEPTH" else None)
                if top is not None:
                    with np.errstate(invalid="ignore"):
                        occluded = valid & (h > np.fmax(top, 0) + self.p["occlusion_mm"])
        n_mask = max(1, int(self.mask.sum()))
        occl_ratio = float(occluded.sum()) / n_mask
        usable = valid & ~occluded
        n_usable = int(usable.sum())
        if n_usable < 0.2 * n_mask:
            return 0.0, max(occl_ratio, 1.0 - n_usable / n_mask)
        occupied = usable & (do > thr) & (dw < do)
        fill = float(occupied.sum()) / n_usable
        span = max(1e-3, self.fill_with - self.fill_without)
        score = float(np.clip((fill - self.fill_without) / span, 0.0, 1.0))
        return score, occl_ratio

    def update(self, frame, st: DetState) -> None:
        score, occl = self.measure(frame)
        st.score, st.detail = score, {"occlusion": round(occl, 2)}
        if occl > self.p["occlusion_max"]:
            st.state = "OCC_PRESENT" if self.stable == "PRESENT" else "OCC_ABSENT"
            self._cand = None
            return
        cand = "PRESENT" if score >= self.p["present_score"] else ("ABSENT" if score <= self.p["absent_score"] else None)
        if cand is None or cand == self.stable:
            self._cand = None
        elif cand != self._cand:
            self._cand, self._cand_since = cand, frame.ts
        elif (frame.ts - self._cand_since) * 1000 >= self.p["debounce_ms"]:
            self.stable, self._cand = cand, None
        st.state = self.stable


def teach_object(roi, without_imgs: list[np.ndarray], with_imgs: list[np.ndarray], sensor: str, tolerance: str,
                 height_with: list[np.ndarray] | None, min_diff: float = 6.0):
    """Returns (model, arrays). `*_imgs` are full-frame signal images (height map or IR)."""
    x, y, w, h = [int(v) for v in roi]
    if w < 4 or h < 4:
        raise TeachError("Draw a working zone around the object first.")
    ref_without = _crop(median_stack(without_imgs), roi)
    ref_with = _crop(median_stack(with_imgs), roi)
    thr = min_diff * (1.5 if sensor == "DEPTH" else 3.0)
    with np.errstate(invalid="ignore"):
        diff = np.abs(ref_with - ref_without)
        aoi = (diff > thr) & np.isfinite(diff)
    aoi = cv2.morphologyEx(aoi.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    aoi = cv2.morphologyEx(aoi, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(aoi)
    keep = np.zeros_like(aoi, dtype=bool)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 12:
            keep |= labels == i
    core_area = int(keep.sum())
    if core_area < 12:
        raise TeachError("No difference found between 'without' and 'with'. Remove the object for the "
                         "'without' snapshot, place it for the 'with' snapshot, and keep hands out of the zone.")
    radius = int(round(TOLERANCE.get(tolerance, 0.0) * np.sqrt(core_area / np.pi)))
    mask = keep
    if radius > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
        mask = cv2.dilate(keep.astype(np.uint8), k) > 0
    mask &= np.isfinite(ref_with) & np.isfinite(ref_without)
    hw = None
    if height_with:
        hw = _crop(median_stack(height_with), roi)
    # self-calibrated fill ratios
    valid = mask
    dw, do = np.abs(ref_with - ref_without), np.zeros_like(ref_with)
    occ_with = valid & (dw > min_diff)
    fill_with = float(occ_with.sum()) / max(1, int(valid.sum()))
    ys, xs = np.nonzero(mask)
    pts = np.column_stack((xs + x, ys + y)).astype(np.float32)
    box = cv2.boxPoints(cv2.minAreaRect(pts)).tolist()
    top_h = float(np.nanmedian(hw[keep])) if hw is not None else 0.0
    model = {"roi": [x, y, w, h], "fill_with": fill_with, "fill_without": 0.0, "zone_px": box,
             "core_area_px": core_area, "top_height_mm": top_h if np.isfinite(top_h) else 0.0}
    arrays = {"mask": mask.astype(np.uint8), "ref_with": ref_with, "ref_without": ref_without}
    if hw is not None:
        arrays["h_with"] = hw
    _ = do
    return model, arrays


# =============================================================================== ACTIVITY / BUTTON
class _ZoneMotion:
    def __init__(self, det: dict, arrays: dict, defaults: dict):
        self.det = det
        self.p = {**defaults, **(det.get("params") or {})}
        self.roi = det["model"]["roi"]
        self.baseline = arrays["baseline"]
        self.mask = np.isfinite(self.baseline)

    def ratio(self, frame, lo: float, hi: float) -> float:
        if frame.height_mm is None:
            return 0.0
        h = _crop(frame.height_mm, self.roi)
        if h.shape != self.baseline.shape:
            return 0.0
        with np.errstate(invalid="ignore"):
            rise = h - self.baseline
            fg = self.mask & (rise > lo) & (rise < hi)
        return float(fg.sum()) / max(1, int(self.mask.sum()))


class ActivityDetector(_ZoneMotion):
    det_type = "ACTIVITY"

    def __init__(self, det, arrays):
        super().__init__(det, arrays, ACTIVITY_DEFAULTS)
        self.counter = 0
        self.inside = False
        self._enter_since = None
        self._exit_since = None
        self._counted = False

    def update(self, frame, st: DetState) -> None:
        r = self.ratio(frame, self.p["min_rise_mm"], self.p["max_rise_mm"])
        thr = self.p["enter_ratio"]
        t = frame.ts
        if r >= thr:
            self._exit_since = None
            if self._enter_since is None:
                self._enter_since = t
            dwell = (t - self._enter_since) * 1000
            if dwell >= self.p["min_dwell_ms"]:
                self.inside = True
                if self.p["complete_on"] == "ENTER" and not self._counted:
                    self.counter += 1
                    self._counted = True
        elif r < thr * 0.5:
            if self.inside:
                if self._exit_since is None:
                    self._exit_since = t
                if (t - self._exit_since) * 1000 >= self.p["exit_ms"]:
                    if self.p["complete_on"] == "LEAVE" and not self._counted:
                        self.counter += 1
                    self.inside, self._counted, self._enter_since, self._exit_since = False, False, None, None
            else:
                self._enter_since = None
        st.score, st.counter = r, self.counter
        st.state = "ACTIVE" if self.inside else "IDLE"


class ButtonDetector(_ZoneMotion):
    det_type = "BUTTON"

    def __init__(self, det, arrays):
        super().__init__(det, arrays, BUTTON_DEFAULTS)
        self.counter = 0
        self.pressed = False
        self._since = None

    def update(self, frame, st: DetState) -> None:
        r = self.ratio(frame, self.p["press_min_mm"], self.p["press_max_mm"])
        # a full arm reaching across (very high) must not count as a press
        high = self.ratio(frame, self.p["press_max_mm"], 2000.0)
        if r >= self.p["press_ratio"] and high < 0.5:
            if self._since is None:
                self._since = frame.ts
            if not self.pressed and (frame.ts - self._since) * 1000 >= self.p["hold_ms"]:
                self.pressed = True
                self.counter += 1
        elif r < self.p["press_ratio"] * 0.5:
            self.pressed, self._since = False, None
        st.score, st.counter = r, self.counter
        st.state = "PRESSED" if self.pressed else "IDLE"


def teach_zone_baseline(roi, height_imgs: list[np.ndarray]):
    x, y, w, h = [int(v) for v in roi]
    if w < 4 or h < 4:
        raise TeachError("Draw the zone first.")
    base = _crop(median_stack(height_imgs), roi)
    if np.isfinite(base).mean() < 0.5:
        raise TeachError("Too little valid depth in the zone - check camera view and lighting.")
    model = {"roi": [x, y, w, h], "zone_px": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
             "top_height_mm": float(np.nanmedian(base))}
    return model, {"baseline": base}


# =============================================================================== QUALITY
def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def feat(img: np.ndarray, algo: str) -> np.ndarray:
    if algo == "HISTOGRAM":
        if img.ndim == 3:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [30, 16], [0, 180, 0, 256])
        else:
            hist = cv2.calcHist([img], [0], None, [32], [0, 256])
        return (hist / max(1.0, float(hist.sum()))).astype(np.float32)
    g = cv2.GaussianBlur(_gray(img), (3, 3), 0).astype(np.float32)
    if algo == "SHAPE":
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        return cv2.magnitude(gx, gy)
    return g


def similarity(live_f: np.ndarray, tmpl_f: np.ndarray, algo: str) -> float:
    if algo == "HISTOGRAM":
        return float(cv2.compareHist(live_f, tmpl_f, cv2.HISTCMP_INTERSECT))
    if live_f.shape[0] < tmpl_f.shape[0] or live_f.shape[1] < tmpl_f.shape[1]:
        return 0.0
    if float(tmpl_f.std()) < 1e-3:
        # flat template (e.g. empty seat) - compare intensity statistics instead of correlation
        d = abs(float(live_f.mean()) - float(tmpl_f.mean())) / 255.0 + abs(float(live_f.std()) - 0.0) / 128.0
        return float(np.clip(1.0 - d, 0.0, 1.0))
    res = cv2.matchTemplate(live_f, tmpl_f, cv2.TM_CCOEFF_NORMED)
    return float(np.clip(res.max(), 0.0, 1.0))


def classify(live_f, samples: list[tuple[str, str, np.ndarray]], algo: str) -> list[tuple[str, str, float]]:
    """Best score per class: [(class, type, score)] sorted descending."""
    best: dict[str, tuple[str, float]] = {}
    for cls, ctype, f in samples:
        s = similarity(live_f, f, algo)
        if cls not in best or s > best[cls][1]:
            best[cls] = (ctype, s)
    return sorted(((c, t, s) for c, (t, s) in best.items()), key=lambda r: -r[2])


def choose_algorithm(samples: list[tuple[str, str, np.ndarray]]) -> tuple[str, dict]:
    """Leave-one-out evaluation across algorithms; returns (algo, report)."""
    classes = {c for c, _, _ in samples}
    if len(classes) < 2 or len(samples) < 3:
        return "TEXTURE", {"note": "Only one class taught - texture matching used. Add BAD/MISSING samples for "
                                   "automatic algorithm selection."}
    report = {}
    for algo in ("TEXTURE", "SHAPE", "HISTOGRAM"):
        feats = [(c, t, feat(img, algo)) for c, t, img in samples]
        correct, margins = 0, []
        for i, (c, t, f) in enumerate(feats):
            others = feats[:i] + feats[i + 1:]
            if not any(o[0] == c for o in others):
                continue
            ranked = classify(f, others, algo)
            if ranked and ranked[0][0] == c:
                correct += 1
                margins.append(ranked[0][2] - (ranked[1][2] if len(ranked) > 1 else 0.0))
            else:
                margins.append(-0.1)
        report[algo] = {"accuracy": correct / max(1, len(feats)), "margin": float(np.mean(margins) if margins else 0)}
    algo = max(report, key=lambda a: (round(report[a]["accuracy"], 3), report[a]["margin"]))
    return algo, report


class QualityDetector:
    det_type = "QUALITY"

    def __init__(self, det: dict, arrays: dict, samples: list[tuple[str, str, np.ndarray]]):
        self.det = det
        self.p = {**QUALITY_DEFAULTS, **(det.get("params") or {})}
        m = det["model"]
        self.roi = m["roi"]
        self.sensor = det["sensor"] if det["sensor"] in ("COLOR", "VISION") else "COLOR"
        self.algo = m.get("algorithm", "TEXTURE")
        self.samples = [(c, t, feat(img, self.algo)) for c, t, img in samples]
        self.baseline = arrays.get("baseline")
        self.history: deque = deque(maxlen=max(1, int(self.p["stable_frames"])))
        self.last_live: np.ndarray | None = None

    def _image(self, frame):
        return frame.vision if self.sensor == "VISION" else frame.color

    def update(self, frame, st: DetState) -> None:
        img = self._image(frame)
        if img is None or not self.samples:
            st.state = "UNKNOWN"
            return
        if self.sensor == "COLOR" and self.baseline is not None and frame.height_mm is not None:
            h = _crop(frame.height_mm, self.roi)
            if h.shape == self.baseline.shape:
                with np.errstate(invalid="ignore"):
                    occ = float(np.mean(h > self.baseline + self.p["occlusion_mm"]))
                if occ > self.p["occlusion_max"]:
                    self.history.clear()
                    st.state, st.detail = "OCCLUDED", {"occlusion": round(occ, 2)}
                    return
        x, y, w, h_ = [int(v) for v in self.roi]
        m = int(self.p["search_px"]) if self.algo != "HISTOGRAM" else 0
        H, W = img.shape[:2]
        live = img[max(0, y - m):min(H, y + h_ + m), max(0, x - m):min(W, x + w + m)]
        self.last_live = img[y:y + h_, x:x + w]
        ranked = classify(feat(live, self.algo), self.samples, self.algo)
        if not ranked:
            st.state = "UNKNOWN"
            return
        cls, ctype, score = ranked[0]
        if ctype == "CORRECT":
            runner = next((r for r in ranked[1:] if r[1] != "CORRECT"), None)
            margin = score - (runner[2] if runner else 0.0)
            if score < self.p["min_score"] or margin < self.p["min_margin"]:
                cls, ctype = "Not recognised", "WRONG"
        verdict = {"CORRECT": "OK", "WRONG": "NG", "MISSING": "MISSING"}[ctype]
        self.history.append((verdict, cls))
        st.score, st.qc_class, st.qc_type = score, cls, ctype
        st.detail = {"ranking": [(c, round(s, 3)) for c, _, s in ranked[:4]]}
        if len(self.history) == self.history.maxlen and len(set(self.history)) == 1:
            st.state = verdict
        else:
            st.state = "UNKNOWN"


def teach_quality(roi, samples: list[tuple[str, str, np.ndarray]], algorithm: str,
                  height_imgs: list[np.ndarray] | None):
    x, y, w, h = [int(v) for v in roi]
    if w < 8 or h < 8:
        raise TeachError("Draw a region of at least 8x8 px around the feature to check.")
    if not any(t == "CORRECT" for _, t, _ in samples):
        raise TeachError("Add at least one sample to a CORRECT class (expected result).")
    for c, _, img in samples:
        if img.shape[:2] != (h, w):
            raise TeachError(f"Sample of class '{c}' was captured with a different region size - recapture it.")
    report = {}
    algo = algorithm
    if algorithm == "AUTO":
        algo, report = choose_algorithm(samples)
    model = {"roi": [x, y, w, h], "algorithm": algo, "auto_report": report,
             "zone_px": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}
    arrays = {}
    if height_imgs:
        base = _crop(median_stack(height_imgs), roi)
        arrays["baseline"] = base
        model["top_height_mm"] = float(np.nanmedian(base)) if np.isfinite(base).any() else 0.0
    return model, arrays


def build_detector(det: dict, arrays: dict, qc_samples: list[tuple[str, str, np.ndarray]]):
    t = det["det_type"]
    if t in ("OBJECT", "TOOL", "CADCAM"):
        return ObjectDetector(det, arrays)
    if t in ("ACTIVITY", "PICKING_BIN"):
        return ActivityDetector(det, arrays)
    if t == "BUTTON":
        return ButtonDetector(det, arrays)
    if t in ("QUALITY", "OBJECT_QUALITY", "TEXT_OCR", "BARCODE"):
        return QualityDetector(det, arrays, qc_samples)
    if t == "GROUP":
        return ActivityDetector(det, arrays)
    raise ValueError(t)
