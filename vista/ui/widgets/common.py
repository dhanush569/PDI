"""Reusable widgets for the light UI."""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from vista.ui.icons import icon
from vista.ui.theme import C, font


# ------------------------------------------------------------------------------ image helpers
def to_qimage(img: np.ndarray) -> QImage:
    img = np.ascontiguousarray(img)
    if img.ndim == 2:
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        h, w = img.shape
        return QImage(img.data, w, h, img.strides[0], QImage.Format_Grayscale8).copy()
    h, w, _ = img.shape
    return QImage(img.data, w, h, img.strides[0], QImage.Format_BGR888).copy()


def height_to_color(height_mm: np.ndarray, max_mm: float = 200.0) -> np.ndarray:
    h = np.nan_to_num(height_mm, nan=-1.0)
    img = cv2.applyColorMap(np.clip(h / max_mm * 255.0, 0, 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    img[h < 0] = (40, 40, 40)
    return img


def frame_view(frame, stream: str):
    if frame is None:
        return None
    if stream == "IR":
        if frame.ir is not None:
            return frame.ir
        return frame.color
    if stream == "HEIGHT":
        if frame.height_mm is not None:
            return height_to_color(frame.height_mm)
        stream = "DEPTH"
    if stream == "DEPTH" and frame.depth_mm is not None:
        d = frame.depth_mm
        valid = d[d > 0]
        if valid.size:
            lo, hi = np.percentile(valid, 2), np.percentile(valid, 98)
            rng = max(1.0, hi - lo)
            n = np.clip((d - lo) / rng * 255, 0, 255).astype(np.uint8)
            img = cv2.applyColorMap(255 - n, cv2.COLORMAP_TURBO)
            img[d <= 0] = (30, 30, 30)
            return img
        return np.zeros((d.shape[0], d.shape[1], 3), dtype=np.uint8)
    if stream == "VISION" and frame.vision is not None:
        return frame.vision
    return frame.color


STREAM_LABELS = {"COLOR": "Colour", "IR": "Infrared", "HEIGHT": "Height map", "DEPTH": "Depth", "VISION": "Vision camera"}


# ------------------------------------------------------------------------------ messages
def info(parent, text: str, title: str = "RANE VISTA") -> None:
    QMessageBox.information(parent, title, text)


def error(parent, text: str, title: str = "RANE VISTA") -> None:
    QMessageBox.critical(parent, title, text)


def confirm(parent, text: str, title: str = "Confirm") -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes


# ------------------------------------------------------------------------------ factories
def button(text: str, kind: str | None = None, slot=None, tip: str = "", ico: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if ico:
        col = "#FFFFFF" if kind in ("primary", "ok") else (C["ng"] if kind == "danger" else C["ink"])
        b.setIcon(icon(ico, col, 16))
        b.setIconSize(QSize(16, 16))
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def tool(ico: str, tip: str, slot=None, checkable: bool = False, size: int = 18, color: str | None = None) -> QToolButton:
    t = QToolButton()
    t.setObjectName("Tool")
    t.setIcon(icon(ico, color or C["ink"], size))
    t.setIconSize(QSize(size, size))
    t.setToolTip(tip)
    t.setCheckable(checkable)
    t.setCursor(Qt.PointingHandCursor)
    if slot:
        t.clicked.connect(slot)
    return t


def label(text: str = "", obj: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    return lb


def panel(layout_cls=QVBoxLayout, margins=10, spacing=8, obj: str = "Panel") -> tuple[QFrame, object]:
    f = QFrame()
    f.setObjectName(obj)
    lay = layout_cls(f)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    return f, lay


def hrow(*widgets, spacing: int = 6) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


def chip(text: str, level: str = "info") -> QLabel:
    lb = QLabel(text)
    fg, bg = {"ok": (C["ok"], C["ok_soft"]), "ng": (C["ng"], C["ng_soft"]), "warn": (C["warn"], C["warn_soft"]),
              "info": (C["primary"], C["primary_soft"]), "idle": (C["muted"], "#EEF1F4"),
              "amber": ("#8A5C00", C["accent_soft"])}.get(level, (C["muted"], "#EEF1F4"))
    lb.setStyleSheet(f"QLabel {{ color: {fg}; background: {bg}; border-radius: 9px; padding: 1px 8px; "
                     f"font-size: 8pt; font-weight: 600; }}")
    return lb


def icon_label(name: str, color: str = C["ink"], size: int = 20) -> QLabel:
    lb = QLabel()
    lb.setPixmap(icon(name, color, size).pixmap(size, size))
    lb.setFixedSize(size + 2, size + 2)
    return lb


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "Search"):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.addAction(icon("search", C["faint"], 16), QLineEdit.TrailingPosition)
        self.setClearButtonEnabled(True)


# ------------------------------------------------------------------------------ Arkite properties panel
class PropertiesPanel(QFrame):
    """Right-hand 'Properties' column (Arkite): tab-like header, scrollable form body, footer actions."""

    def __init__(self, title: str = "Properties", width: int = 340):
        super().__init__()
        self.setFixedWidth(width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        tab = QLabel(title)
        tab.setStyleSheet(f"QLabel {{ background: {C['surface']}; border: 1px solid {C['line']}; border-bottom: none;"
                          f" border-top-left-radius: 4px; border-top-right-radius: 4px; padding: 5px 14px; "
                          f"font-weight: 600; }}")
        tab.setFixedHeight(28)
        outer.addLayout(hrow(tab, None))
        self.box = QFrame()
        self.box.setObjectName("Panel")
        self.box.setStyleSheet("QFrame#Panel { border-top-left-radius: 0; }")
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.body_w = QWidget()
        self.body_w.setStyleSheet("background: white;")
        self.body = QVBoxLayout(self.body_w)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        scroll.setWidget(self.body_w)
        bl.addWidget(scroll, 1)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(12, 8, 12, 10)
        bl.addLayout(self.footer)
        outer.addWidget(self.box, 1)

    def heading(self, text: str, sub: str = "") -> QLabel:
        h = QLabel(text)
        h.setObjectName("H3")
        self.body.addWidget(h)
        if sub:
            self.body.addWidget(label(sub, "Faint", wrap=True))
        return h

    def field(self, caption: str, widget: QWidget) -> QWidget:
        cap = QLabel(caption)
        cap.setStyleSheet(f"color: {C['text']}; font-size: 8.5pt;")
        self.body.addWidget(cap)
        self.body.addWidget(widget)
        return widget

    def clear(self) -> None:
        def wipe(lay):
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
                elif it.layout():
                    wipe(it.layout())
        wipe(self.body)
        wipe(self.footer)

    def end(self) -> None:
        self.body.addStretch(1)


# ------------------------------------------------------------------------------ image view (zoom / pan)
class ImageView(QWidget):
    """Image with overlays. Wheel = zoom, right/middle drag = pan, left drag = draw zone (edit mode)."""

    rect_changed = Signal(list)
    clicked = Signal(float, float)
    hovered = Signal(float, float)

    def __init__(self, parent=None, placeholder: str = "No camera image", dark: bool = True):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setMouseTracking(True)
        self._img: QImage | None = None
        self._size = (0, 0)
        self.overlays: list[dict] = []
        self.edit_rect = None
        self.edit_mode = False
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self._drag_start = self._pan_start = None
        self.placeholder = placeholder
        self.bg = QColor("#20262E") if dark else QColor(C["canvas"])

    def set_image(self, img) -> None:
        if img is None:
            self._img = None
        else:
            self._img = to_qimage(img)
            self._size = (img.shape[1], img.shape[0])
        self.update()

    def set_overlays(self, overlays: list[dict]) -> None:
        self.overlays = overlays
        self.update()

    def set_edit_rect(self, rect, editable: bool = True) -> None:
        self.edit_rect = list(rect) if rect else None
        self.edit_mode = editable
        self.update()

    def zoom_by(self, f: float) -> None:
        self.zoom = float(min(12.0, max(1.0, self.zoom * f)))
        if self.zoom == 1.0:
            self.pan = QPointF(0, 0)
        self.update()

    def fit(self) -> None:
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self.update()

    def _geom(self):
        if self._img is None:
            return 1.0, 0.0, 0.0
        w, h = self._size
        s = min(self.width() / w, self.height() / h) * self.zoom
        return s, (self.width() - w * s) / 2 + self.pan.x(), (self.height() - h * s) / 2 + self.pan.y()

    def to_image(self, pos) -> tuple[float, float]:
        s, ox, oy = self._geom()
        return (pos.x() - ox) / s, (pos.y() - oy) / s

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg)
        if self._img is None:
            p.setPen(QColor("#9AA4B1"))
            p.drawText(self.rect(), Qt.AlignCenter, self.placeholder)
            return
        s, ox, oy = self._geom()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(QRectF(ox, oy, self._size[0] * s, self._size[1] * s), self._img)
        p.setRenderHint(QPainter.Antialiasing)
        for ov in self.overlays:
            pts = ov.get("poly") or []
            if len(pts) < 2:
                continue
            col = QColor(ov.get("color", C["accent"]))
            poly = QPolygonF([QPointF(ox + x * s, oy + y * s) for x, y in pts])
            if ov.get("fill"):
                fc = QColor(col)
                fc.setAlpha(int(ov["fill"] * 255))
                p.setBrush(fc)
            else:
                p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, ov.get("width", 2)))
            p.drawPolygon(poly)
            if ov.get("label"):
                p.setFont(font(8, True))
                br = poly.boundingRect()
                tw = p.fontMetrics().horizontalAdvance(ov["label"]) + 8
                p.fillRect(QRectF(br.left(), br.top() - 17, tw, 15), col)
                p.setPen(QColor("#FFFFFF"))
                p.drawText(QRectF(br.left() + 4, br.top() - 17, tw, 15), Qt.AlignVCenter, ov["label"])
        if self.edit_rect:
            x, y, w, h = self.edit_rect
            r = QRectF(ox + x * s, oy + y * s, w * s, h * s)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#FFFFFF"), 1.5, Qt.DashLine))
            p.drawRect(r)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            for cpt in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                p.drawRect(QRectF(cpt.x() - 3, cpt.y() - 3, 6, 6))

    def wheelEvent(self, e):  # noqa: N802
        if self._img is None:
            return
        before = self.to_image(e.position())
        self.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
        s, ox, oy = self._geom()
        self.pan += QPointF(e.position().x() - (ox + before[0] * s), e.position().y() - (oy + before[1] * s))
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        if self._img is None:
            return
        if e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._pan_start = (e.position(), QPointF(self.pan))
            return
        x, y = self.to_image(e.position())
        self.clicked.emit(x, y)
        if self.edit_mode and e.button() == Qt.LeftButton:
            self._drag_start = (x, y)

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._img is None:
            return
        if self._pan_start is not None:
            start, pan0 = self._pan_start
            self.pan = pan0 + (e.position() - start)
            self.update()
            return
        x, y = self.to_image(e.position())
        self.hovered.emit(x, y)
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        W, H = self._size
        x, y = min(max(x, 0), W), min(max(y, 0), H)
        self.edit_rect = [min(x0, x), min(y0, y), abs(x - x0), abs(y - y0)]
        self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._pan_start is not None:
            self._pan_start = None
            return
        if self._drag_start is not None and self.edit_rect and self.edit_rect[2] > 2 and self.edit_rect[3] > 2:
            self.edit_rect = [int(round(v)) for v in self.edit_rect]
            self.rect_changed.emit(self.edit_rect)
        self._drag_start = None


# ------------------------------------------------------------------------------ small widgets
class Pill(QLabel):
    LEVEL = {"ok": C["ok"], "error": C["ng"], "warn": C["warn"], "info": C["info"], "idle": C["faint"]}

    def __init__(self, text: str = "", level: str = "idle"):
        super().__init__()
        self.set(text, level)

    def set(self, text: str, level: str = "idle") -> None:
        self.setText(f"<span style='color:{self.LEVEL.get(level, C['faint'])}'>&#9679;</span>&nbsp;{text}")


class KpiTile(QFrame):
    def __init__(self, title: str, value: str = "-", color: str | None = None, ico: str | None = None):
        super().__init__()
        self.setObjectName("Panel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        if ico:
            box = QLabel()
            box.setFixedSize(38, 38)
            box.setAlignment(Qt.AlignCenter)
            box.setPixmap(icon(ico, color or C["primary"], 20).pixmap(20, 20))
            box.setStyleSheet(f"background: {QColor(color or C['primary']).lighter(185).name()}; border-radius: 8px;")
            lay.addWidget(box)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.value = QLabel(value)
        self.value.setFont(font(20, display=True))
        if color:
            self.value.setStyleSheet(f"color: {color};")
        t = QLabel(title)
        t.setObjectName("Muted")
        col.addWidget(self.value)
        col.addWidget(t)
        lay.addLayout(col, 1)

    def set(self, value: str) -> None:
        self.value.setText(value)


class ParamForm(QWidget):
    """Form generated from (key, label, kind, default, options) specs."""

    changed = Signal()

    def __init__(self, specs=(), values: dict | None = None, stacked: bool = False):
        super().__init__()
        self.stacked = stacked
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setVerticalSpacing(6)
        if stacked:
            self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.widgets: dict[str, tuple[str, QWidget]] = {}
        self.build(specs, values or {})

    def build(self, specs, values: dict) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.widgets = {}
        for key, lab, kind, default, opts in specs:
            v = values.get(key, default)
            if kind == "bool":
                w = QCheckBox()
                w.setChecked(bool(v))
                w.toggled.connect(self.changed)
            elif kind == "int":
                w = QSpinBox()
                lo, hi = opts or (-1_000_000, 1_000_000)
                w.setRange(lo, hi)
                w.setValue(int(v))
                w.valueChanged.connect(self.changed)
            elif kind == "float":
                w = QDoubleSpinBox()
                lo, hi = opts or (-1e6, 1e6)
                w.setRange(lo, hi)
                w.setDecimals(3 if hi <= 1.0 else 1)
                w.setSingleStep(0.05 if hi <= 1.0 else 1.0)
                w.setValue(float(v))
                w.valueChanged.connect(self.changed)
            elif kind == "choice":
                w = QComboBox()
                w.addItems(list(opts))
                w.setCurrentText(str(v))
                w.currentTextChanged.connect(self.changed)
            elif kind == "text":
                w = QPlainTextEdit(str(v or ""))
                w.setFixedHeight(64)
                w.textChanged.connect(self.changed)
            else:
                w = QLineEdit(str(v if v is not None else ""))
                w.textChanged.connect(self.changed)
            self.form.addRow(lab, w)
            self.widgets[key] = (kind, w)

    def values(self) -> dict:
        out = {}
        for key, (kind, w) in self.widgets.items():
            if kind == "bool":
                out[key] = w.isChecked()
            elif kind in ("int", "float"):
                out[key] = w.value()
            elif kind == "choice":
                out[key] = w.currentText()
            elif kind == "text":
                out[key] = w.toPlainText()
            else:
                out[key] = w.text().strip()
        return out

    def set_enabled(self, on: bool) -> None:
        for _, w in self.widgets.values():
            w.setEnabled(on)


class Page(QWidget):
    """Base page: canvas background, 10 px gutters."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
