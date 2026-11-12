"""Full-screen output on the projector display (Optoma ZH406ST over HDMI)."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from vista.engine.steps import operator_text
from vista.ui.theme import DISPLAY


class ProjectorWindow(QWidget):
    def __init__(self, ctx, executor):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("RANE VISTA projector")
        self.setCursor(Qt.BlankCursor)
        self.ctx, self.ex = ctx, executor
        self.mode = "RUN"              # RUN | MARKERS | VERIFY | TEST | BLANK
        self.markers: list[tuple[float, float]] = []
        self.marker_radius = 22
        self.highlight: int | None = None
        self.states: dict = {}
        self._t = QTimer(self)
        self._t.timeout.connect(self.update)
        self._t.start(66)

    # ------------------------------------------------------------------ placement
    def place(self) -> str:
        name = self.ctx.settings.get("projector_screen") or ""
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()

        target_scr = None
        if name:
            target_scr = next((s for s in screens if s.name() == name), None)

        # If not explicitly found, select a secondary screen that is NOT the primary screen
        if target_scr is None and len(screens) > 1:
            target_scr = next((s for s in screens if s != primary), screens[1])

        # If there is no dedicated external projector display (e.g. laptop or single monitor):
        # NEVER open a full-screen black window covering the operator screen!
        if target_scr is None or target_scr == primary:
            self.hide()
            return ""

        self.setScreen(target_scr)
        self.setGeometry(target_scr.geometry())
        self.showFullScreen()
        return target_scr.name()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key_Escape, Qt.Key_Q):
            self.hide()
        super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.hide()

    def on_states(self, states: dict) -> None:
        self.states = states

    def show_markers(self, pts, radius: int = 22) -> None:
        self.mode, self.markers, self.marker_radius = "MARKERS", list(pts), radius
        self.repaint()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    # ------------------------------------------------------------------ painting
    def _proj_scale(self):
        cal = self.ctx.calibration
        pw, ph = cal.proj_size if cal else (1920, 1080)
        return self.width() / pw, self.height() / ph

    def _mm_poly(self, pts_mm) -> QPolygonF | None:
        cal = self.ctx.calibration
        if cal is None or not pts_mm:
            return None
        sx, sy = self._proj_scale()
        pp = cal.bench_to_proj(pts_mm)
        return QPolygonF([QPointF(x * sx, y * sy) for x, y in pp])

    def _offset(self, det: dict, poly_mm):
        off = (det.get("params") or {}).get("proj_offset_mm") or [0, 0]
        return [[x + off[0], y + off[1]] for x, y in poly_mm]

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self.mode == "BLANK":
            return
        s = self.ctx.settings
        tr = QTransform()
        cx, cy = self.width() / 2, self.height() / 2
        tr.translate(cx, cy)
        tr.rotate(int(s.get("projector_rotation", 0)))
        tr.scale(-1 if s.get("projector_flip_h") else 1, -1 if s.get("projector_flip_v") else 1)
        tr.translate(-cx, -cy)
        p.setTransform(tr)
        p.setRenderHint(QPainter.Antialiasing)
        if self.mode == "MARKERS":
            sx, sy = self._proj_scale() if self.ctx.calibration else (self.width() / 1920, self.height() / 1080)
            p.setBrush(Qt.white)
            p.setPen(Qt.NoPen)
            for x, y in self.markers:
                p.drawEllipse(QPointF(x * sx, y * sy), self.marker_radius * sx, self.marker_radius * sx)
            return
        if self.ctx.calibration is None:
            self._pattern(p)
            return
        if self.mode == "VERIFY":
            self._verify(p)
            return
        if self.mode == "TEST":
            self._all_zones(p, faint=False)
            return
        self._run(p)

    def _font(self, rel: float, bold: bool = False) -> QFont:
        f = QFont(DISPLAY)
        f.setPixelSize(max(10, int(self.height() * rel)))
        f.setBold(bold)
        return f

    def _pattern(self, p: QPainter) -> None:
        p.setPen(QPen(QColor("#F2B233"), 3))
        p.drawRect(self.rect().adjusted(4, 4, -4, -4))
        p.drawLine(self.rect().topLeft(), self.rect().bottomRight())
        p.drawLine(self.rect().topRight(), self.rect().bottomLeft())
        p.setFont(QFont(DISPLAY, 40))
        p.drawText(self.rect(), Qt.AlignCenter, "RANE VISTA\nprojector not calibrated")

    def _verify(self, p: QPainter) -> None:
        cal = self.ctx.calibration
        p.setPen(QPen(QColor("#4C8DD6"), 1))
        for x in range(-200, 2001, 100):
            poly = self._mm_poly([[x, -300], [x, 1200]])
            p.drawLine(poly[0], poly[1])
        for y in range(-300, 1201, 100):
            poly = self._mm_poly([[-200, y], [2000, y]])
            p.drawLine(poly[0], poly[1])
        for (a, col) in (([[0, 0], [150, 0]], "#E5484D"), ([[0, 0], [0, 150]], "#2FB670")):
            p.setPen(QPen(QColor(col), 5))
            poly = self._mm_poly(a)
            p.drawLine(poly[0], poly[1])
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont(DISPLAY, 18))
        o = self._mm_poly([[10, 10]])[0]
        p.drawText(o, f"origin  ({cal.rms_px:.2f} px rms)")

    def _draw_zone(self, p, det, color: str, fill: float, text: str | None, width: int = 4) -> None:
        zone = det.get("model", {}).get("zone_mm")
        poly = self._mm_poly(self._offset(det, zone)) if zone else None
        if poly is None:
            return
        c = QColor(color)
        p.setPen(QPen(c, width))
        fc = QColor(c)
        fc.setAlpha(int(255 * fill))
        p.setBrush(fc if fill > 0 else Qt.NoBrush)
        p.drawPolygon(poly)
        if text:
            p.setFont(self._font(0.024))
            br = poly.boundingRect()
            p.setPen(c)
            p.drawText(QRectF(br.left() - 150, br.bottom() + 6, br.width() + 300, 40), Qt.AlignHCenter, text)

    def _all_zones(self, p, faint: bool) -> None:
        for det_id, det in self.ctx.engine.meta().items():
            st = self.states.get(det_id)
            col = "#4C8DD6"
            if st and st.state in ("PRESENT", "OK", "ACTIVE", "PRESSED"):
                col = "#2FB670"
            if det_id == self.highlight:
                col = "#F2B233"
            self._draw_zone(p, det, col, 0.0 if faint else 0.15, None if faint else det["name"], 2 if faint else 3)

    def _area_rect(self, key: str) -> QRectF | None:
        x, y, w, h = self.ctx.settings.get(key) or [0, 0, 0, 0]
        poly = self._mm_poly([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
        return poly.boundingRect() if poly is not None else None

    def _run(self, p: QPainter) -> None:
        s = self.ctx.settings
        meta = self.ctx.engine.meta()
        if s.get("projector_show_all"):
            self._all_zones(p, faint=True)
        for det_id, det in meta.items():           # reset button always visible
            if det["det_type"] == "BUTTON" and (det.get("params") or {}).get("function") == "RESET":
                self._draw_zone(p, det, "#93A1AF", 0.15, "RESET", 2)
        blink = (time.monotonic() * 2) % 2 < 1.3
        ex = self.ex
        lines = []
        if ex.mode == "RUNNING" and ex.root is not None:
            now = time.monotonic()
            for n in ex.root.walk():                # short green flash for just-completed steps
                det = meta.get(n.step.get("detection_id"))
                if det and n.state == "DONE" and now - n.data.get("t_done", 0) < 0.8:
                    self._draw_zone(p, det, s["color_ok"], 0.35, None)
            for n in ex.active_leaves():
                det = meta.get(n.step.get("detection_id"))
                txt = operator_text(n.step)
                lines.append(txt)
                if det is None:
                    continue
                st = self.states.get(det["id"])
                color, fill = s["color_todo"], 0.35 if blink else 0.18
                if st is not None and st.state in ("NG", "MISSING"):
                    color, fill = s["color_ng"], 0.45
                    txt = f"{st.qc_class or 'NG'}"
                self._draw_zone(p, det, color, fill, txt)
        r = self._area_rect("instruction_area_mm")
        if r is not None and lines:
            p.setPen(QColor("#FFFFFF"))
            p.setFont(self._font(0.032))
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, "\n".join(lines[:3]))
        if ex.mode == "RESULT" and ex.last_result:
            res = ex.last_result["result"]
            col = {"PASS": s["color_ok"], "FAIL": s["color_ng"]}.get(res, "#93A1AF")
            r = self._area_rect("result_area_mm")
            if r is not None:
                p.fillRect(r, QColor(col))
                p.setPen(QColor("#1B2129"))
                p.setFont(self._font(0.07, True))
                p.drawText(r, Qt.AlignCenter, res)
