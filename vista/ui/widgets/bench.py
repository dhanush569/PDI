"""Bench canvas (Arkite 'Projector' tab): projector footprint, 100 mm grid, origin, detection zones and
projection areas in bench millimetres. Zones can be selected and - when editable - dragged to shift
their projection (stored as proj_offset_mm)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from vista.ui.theme import C


class BenchCanvas(QWidget):
    zone_clicked = Signal(int)
    zone_moved = Signal(int, float, float)        # det_id, dx_mm, dy_mm (total offset)
    zone_dragging = Signal(int, float, float)     # det_id, dx_mm, dy_mm (real-time during drag)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setMinimumSize(360, 240)
        self.setMouseTracking(True)
        self.items: list[dict] = []     # {id, poly, label, color, fill, offset}
        self.areas: list[dict] = []     # {rect:[x,y,w,h], label}
        self.texts: list[dict] = []
        self.selected: int | None = None
        self.editable = False
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self._drag = None
        self._pan_start = None

    def set_scene(self, items, areas=(), texts=(), selected=None, editable=False) -> None:
        self.items, self.areas, self.texts = list(items), list(areas), list(texts)
        self.selected, self.editable = selected, editable
        self.update()

    # ------------------------------------------------------------------ geometry
    def _footprint(self):
        cal = self.ctx.calibration
        if cal is None:
            return None
        w, h = cal.proj_size
        return cal.proj_to_bench([[0, 0], [w, 0], [w, h], [0, h]]).tolist()

    def _extent(self):
        fp = self._footprint()
        pts = fp or [[-100, -200], [1600, -200], [1600, 900], [-100, 900]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def _xf(self):
        x0, y0, x1, y1 = self._extent()
        m = 24
        s = min((self.width() - 2 * m) / max(1, x1 - x0), (self.height() - 2 * m) / max(1, y1 - y0)) * self.zoom
        ox = (self.width() - (x1 - x0) * s) / 2 - x0 * s + self.pan.x()
        oy = (self.height() + (y1 - y0) * s) / 2 + y0 * s + self.pan.y()
        return s, ox, oy

    def to_px(self, x, y):
        s, ox, oy = self._xf()
        return QPointF(ox + x * s, oy - y * s)

    def to_mm(self, pos):
        s, ox, oy = self._xf()
        return (pos.x() - ox) / s, (oy - pos.y()) / s

    def _poly(self, it):
        dx, dy = it.get("offset", (0, 0))
        return QPolygonF([self.to_px(x + dx, y + dy) for x, y in it["poly"]])

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#ECEFF3"))
        fp = self._footprint()
        if fp:
            poly = QPolygonF([self.to_px(*pt) for pt in fp])
            p.setPen(QPen(QColor("#8C97A5"), 1.2))
            p.setBrush(QColor("#F4F5F7"))
            p.drawPolygon(poly)
        else:
            p.setPen(QColor(C["muted"]))
            p.drawText(self.rect().adjusted(0, 8, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                       "Not calibrated - layout shown in nominal bench millimetres")
        x0, y0, x1, y1 = self._extent()
        p.setPen(QPen(QColor("#E0E4EA"), 1))
        for gx in range(int(x0 // 100) * 100, int(x1) + 1, 100):
            p.drawLine(self.to_px(gx, y0), self.to_px(gx, y1))
        for gy in range(int(y0 // 100) * 100, int(y1) + 1, 100):
            p.drawLine(self.to_px(x0, gy), self.to_px(x1, gy))
        o = self.to_px(0, 0)
        p.setPen(QPen(QColor("#D12C2C"), 2.2))
        p.drawLine(o, self.to_px(80, 0))
        p.setPen(QPen(QColor("#1B9E3E"), 2.2))
        p.drawLine(o, self.to_px(0, 80))
        f = QFont()
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#D12C2C"))
        p.drawText(self.to_px(84, -14), "X")
        p.setPen(QColor("#1B9E3E"))
        p.drawText(self.to_px(-22, 88), "Y")
        for a in self.areas:
            x, y, w, h = a["rect"]
            r = QRectF(self.to_px(x, y + h), self.to_px(x + w, y))
            p.setPen(QPen(QColor(C["primary"]), 1.4, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 170))
            p.drawRect(r)
            p.setPen(QColor(C["primary"]))
            p.drawText(r, Qt.AlignCenter, a["label"])
        for it in self.items:
            sel = it["id"] == self.selected
            col = QColor(it.get("color", "#E6D200"))
            fill = QColor(col)
            fill.setAlpha(int(255 * it.get("fill", 0.85)))
            p.setBrush(fill)
            p.setPen(QPen(QColor("#2B66D9") if sel else QColor("#1C1C1C"), 2.6 if sel else 1.6))
            poly = self._poly(it)
            p.drawPolygon(poly)
            if it.get("label") and (sel or self.zoom > 1.6 or it.get("always_label")):
                br = poly.boundingRect()
                p.setPen(QColor("#1C1C1C"))
                p.drawText(QRectF(br.left() - 80, br.bottom() + 2, br.width() + 160, 16), Qt.AlignHCenter,
                           it["label"])
        for t in self.texts:
            p.setPen(QColor(C["ink"]))
            p.drawText(self.to_px(*t["at"]), t["text"])

    # ------------------------------------------------------------------ interaction
    def _hit(self, pos):
        for it in reversed(self.items):
            if self._poly(it).containsPoint(pos, Qt.OddEvenFill):
                return it
        return None

    def wheelEvent(self, e):  # noqa: N802
        before = self.to_mm(e.position())
        self.zoom = min(10.0, max(0.5, self.zoom * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)))
        after = self.to_px(*before)
        self.pan += e.position() - after
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._pan_start = (e.position(), QPointF(self.pan))
            return
        it = self._hit(e.position())
        if it is None:
            return
        self.selected = it["id"]
        self.zone_clicked.emit(it["id"])
        if self.editable:
            self._drag = (it, self.to_mm(e.position()), tuple(it.get("offset", (0, 0))))
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._pan_start is not None:
            start, p0 = self._pan_start
            self.pan = p0 + (e.position() - start)
            self.update()
            return
        if self._drag:
            it, start, off0 = self._drag
            x, y = self.to_mm(e.position())
            it["offset"] = (round(off0[0] + x - start[0]), round(off0[1] + y - start[1]))
            self.update()
            self.zone_dragging.emit(it["id"], float(it["offset"][0]), float(it["offset"][1]))
            return
        self.setCursor(Qt.PointingHandCursor if self._hit(e.position()) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._pan_start is not None:
            self._pan_start = None
            return
        if self._drag:
            it, _, off0 = self._drag
            if tuple(it.get("offset", (0, 0))) != off0:
                self.zone_moved.emit(it["id"], float(it["offset"][0]), float(it["offset"][1]))
            self._drag = None

    def reset_view(self) -> None:
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self.update()
