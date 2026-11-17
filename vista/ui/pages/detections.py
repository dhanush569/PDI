"""Detections page (Arkite layout): tree | Teaching · Fine tune · Projector | Properties."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
                               QProgressBar, QScrollArea, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from vista.core.security import PermissionDenied, Perm
from vista.ui.dialogs_extra import Popup
from vista.ui.icons import icon
from vista.ui.theme import C, font
from vista.ui.widgets.bench import BenchCanvas
from vista.ui.widgets.common import (ImageView, Page, ParamForm, PropertiesPanel, SearchBox, button, chip, confirm,
                                     error, frame_view, height_to_color, hrow, info, label, to_qimage, tool)
from vista.vision.detectors import (ACTIVITY_DEFAULTS, BUTTON_DEFAULTS, OBJECT_DEFAULTS, QC_ALGOS, QC_TYPES,
                                   QUALITY_DEFAULTS, TOLERANCE, TeachError, decode_png, teach_object, teach_quality,
                                   teach_zone_baseline)

TYPE_LABEL = {
    "OBJECT": "Object",
    "OBJECT_QUALITY": "Object with Quality Check",
    "TOOL": "Tool",
    "PICKING_BIN": "Picking Bin",
    "BUTTON": "Virtual button",
    "ACTIVITY": "Activity",
    "QUALITY": "Quality check",
    "TEXT_OCR": "Text (OCR)",
    "BARCODE": "Barcode (QR/DM)",
    "GROUP": "Group",
    "CADCAM": "CAD/CAM detection",
}
TYPE_ICON = {
    "OBJECT": "cube",
    "OBJECT_QUALITY": "cube",
    "TOOL": "tool",
    "PICKING_BIN": "bin",
    "BUTTON": "button",
    "ACTIVITY": "hand",
    "QUALITY": "eye",
    "TEXT_OCR": "text",
    "BARCODE": "barcode",
    "GROUP": "group",
    "CADCAM": "cad",
}
TYPE_SHORTCUT = {
    "OBJECT": "Ctrl+Shift+O",
    "OBJECT_QUALITY": "Ctrl+Shift+K",
    "TOOL": "Ctrl+Shift+T",
    "PICKING_BIN": "Ctrl+Shift+P",
    "BUTTON": "Ctrl+Shift+B",
    "ACTIVITY": "Ctrl+Shift+A",
    "QUALITY": "Ctrl+Shift+Q",
    "TEXT_OCR": "Ctrl+Shift+X",
    "BARCODE": "Ctrl+Shift+C",
}
SENSORS = {
    "OBJECT": ["DEPTH", "IR"],
    "OBJECT_QUALITY": ["COLOR", "VISION"],
    "TOOL": ["DEPTH", "IR", "VISION"],
    "PICKING_BIN": ["DEPTH"],
    "BUTTON": ["DEPTH"],
    "ACTIVITY": ["DEPTH"],
    "QUALITY": ["COLOR", "VISION"],
    "TEXT_OCR": ["COLOR", "VISION"],
    "BARCODE": ["COLOR", "VISION"],
    "GROUP": ["DEPTH"],
    "CADCAM": ["DEPTH"],
}
SENSOR_LABEL = {"DEPTH": "3D sensor - depth", "IR": "3D sensor - infrared", "COLOR": "3D sensor - colour (RGB)",
                "VISION": "Vision camera"}
STREAM_FOR = {"DEPTH": "HEIGHT", "IR": "IR", "COLOR": "COLOR", "VISION": "VISION"}
TOL_LABEL = {"EXACT": "Exact (0% variation)", "ACCURATE": "Accurate (20% variation)",
             "NARROW": "Narrow (50% variation)", "WIDE": "Wide (100% variation)"}
DEFAULT_CLASSES = [{"name": "Expected result", "type": "CORRECT"}, {"name": "Bad result", "type": "WRONG"},
                   {"name": "Missing", "type": "MISSING"}]
STATE_GLYPH = {"PRESENT": ("●", C["primary"]), "OK": ("●", C["ok"]), "ACTIVE": ("●", C["ok"]),
               "PRESSED": ("●", C["ok"]), "ABSENT": ("○", C["primary"]), "IDLE": ("○", C["faint"]),
               "NG": ("●", C["ng"]), "MISSING": ("●", C["ng"]), "UNTAUGHT": ("⚠", C["warn"]),
               "DISABLED": ("⊘", C["faint"]), "OCC_PRESENT": ("◐", C["primary"]), "OCC_ABSENT": ("◑", C["primary"]),
               "OCCLUDED": ("◐", C["primary"]), "UNKNOWN": ("❚❚", C["primary"])}

PARAM_SPECS = {
    "OBJECT": [("present_score", "Present at score ≥", "float", OBJECT_DEFAULTS["present_score"], (0, 1)),
               ("absent_score", "Absent at score ≤", "float", OBJECT_DEFAULTS["absent_score"], (0, 1)),
               ("min_diff", "Minimum change (mm / IR)", "float", OBJECT_DEFAULTS["min_diff"], (1, 200)),
               ("occlusion_mm", "Occlusion above object (mm)", "float", OBJECT_DEFAULTS["occlusion_mm"], (5, 1000)),
               ("occlusion_max", "Maximum occluded fraction", "float", OBJECT_DEFAULTS["occlusion_max"], (0, 1)),
               ("debounce_ms", "Debounce (ms)", "int", OBJECT_DEFAULTS["debounce_ms"], (0, 5000))],
    "OBJECT_QUALITY": [("present_score", "Present at score ≥", "float", 0.70, (0, 1)),
                       ("min_score", "Minimum quality similarity", "float", QUALITY_DEFAULTS["min_score"], (0, 1)),
                       ("min_margin", "Minimum margin to bad classes", "float", QUALITY_DEFAULTS["min_margin"], (0, 1)),
                       ("debounce_ms", "Debounce (ms)", "int", 200, (0, 5000))],
    "TOOL": [("present_score", "Detected at score ≥", "float", 0.65, (0, 1)),
             ("smart_tool", "Smart tool controller link", "bool", False, None),
             ("wait_ok", "Require OK signal before release", "bool", True, None),
             ("debounce_ms", "Debounce (ms)", "int", 200, (0, 5000))],
    "PICKING_BIN": [("min_rise_mm", "Hand inside bin (mm from rim)", "float", 30.0, (5, 300)),
                    ("enter_ratio", "Bin opening fill ratio", "float", 0.15, (0, 1)),
                    ("min_dwell_ms", "Minimum dwell time (ms)", "int", 150, (0, 5000)),
                    ("material_code", "Linked material code", "str", "", None)],
    "ACTIVITY": [("min_rise_mm", "Hand above bench from (mm)", "float", ACTIVITY_DEFAULTS["min_rise_mm"], (1, 500)),
                 ("max_rise_mm", "… up to (mm)", "float", ACTIVITY_DEFAULTS["max_rise_mm"], (10, 1500)),
                 ("enter_ratio", "Zone fill to count as inside", "float", ACTIVITY_DEFAULTS["enter_ratio"], (0, 1)),
                 ("min_dwell_ms", "Minimum dwell (ms)", "int", ACTIVITY_DEFAULTS["min_dwell_ms"], (0, 60000)),
                 ("exit_ms", "Leave confirmation (ms)", "int", ACTIVITY_DEFAULTS["exit_ms"], (0, 5000)),
                 ("complete_on", "Complete on", "choice", ACTIVITY_DEFAULTS["complete_on"], ["LEAVE", "ENTER"])],
    "BUTTON": [("press_min_mm", "Press height from (mm)", "float", BUTTON_DEFAULTS["press_min_mm"], (1, 500)),
               ("press_max_mm", "… up to (mm)", "float", BUTTON_DEFAULTS["press_max_mm"], (10, 800)),
               ("press_ratio", "Zone fill to press", "float", BUTTON_DEFAULTS["press_ratio"], (0, 1)),
               ("hold_ms", "Hold time (ms)", "int", BUTTON_DEFAULTS["hold_ms"], (0, 5000))],
    "QUALITY": [("min_score", "Minimum similarity", "float", QUALITY_DEFAULTS["min_score"], (0, 1)),
                ("min_margin", "Minimum margin to bad classes", "float", QUALITY_DEFAULTS["min_margin"], (0, 1)),
                ("stable_frames", "Stable evaluations", "int", QUALITY_DEFAULTS["stable_frames"], (1, 30)),
                ("search_px", "Position search (px)", "int", QUALITY_DEFAULTS["search_px"], (0, 80)),
                ("occlusion_mm", "Occlusion height (mm)", "float", QUALITY_DEFAULTS["occlusion_mm"], (5, 1000)),
                ("occlusion_max", "Maximum occluded fraction", "float", QUALITY_DEFAULTS["occlusion_max"], (0, 1))],
    "TEXT_OCR": [("variable", "Store text in variable", "str", "OCR_RESULT", None),
                 ("expected_regex", "Validate pattern (regex)", "str", "", None),
                 ("ignore_whitespace", "Ignore whitespace", "bool", True, None),
                 ("ignore_case", "Ignore letter case", "bool", True, None)],
    "BARCODE": [("variable", "Store barcode in variable", "str", "BARCODE", None),
                ("symbology", "Symbology", "choice", "ALL", ["ALL", "QR", "DATA_MATRIX", "CODE128", "EAN13"]),
                ("expected_regex", "Validate regex", "str", "", None)],
    "GROUP": [],
    "CADCAM": [("template_detection", "Template detection name", "str", "", None),
               ("stl_model_path", "STL 3D model file", "str", "", None)],
}


def _pix(img: np.ndarray | None, w: int, h: int) -> QPixmap | None:
    if img is None:
        return None
    return QPixmap.fromImage(to_qimage(img)).scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class SnapBox(QFrame):
    """Arkite 'Without … / With …' snapshot tile."""

    def __init__(self, title: str):
        super().__init__()
        self.setStyleSheet(f"QFrame {{ background: white; border: 1px solid {C['line']}; border-radius: 4px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("border: none; font-size: 8.5pt;")
        lay.addWidget(self.title)
        self.img = QLabel("not captured")
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setMinimumHeight(118)
        self.img.setStyleSheet(f"border: none; background: #F3F5F8; color: {C['faint']};")
        lay.addWidget(self.img, 1)

    def set(self, pm: QPixmap | None, text: str = "not captured") -> None:
        if pm is None:
            self.img.setPixmap(QPixmap())
            self.img.setText(text)
        else:
            self.img.setPixmap(pm)


class TeachActivityDialog(Popup):
    """Arkite Live Motion Capture & Teaching Dialog for Activity detections.
    User positions workspace, performs the activity in real time, and clicks OK."""

    def __init__(self, parent, page, det: dict, roi: list):
        super().__init__(parent, f"Teach Activity: {det['name']}",
                         "Position workspace, keep empty for baseline, then perform activity inside the workspace.",
                         ico="hand", width=740)
        self.page = page
        self.det = det
        self.roi = roi
        self.ctx = page.ctx
        self.baseline_sig = None
        self.baseline_depth = None
        self.motion_count = 0

        self.cam_view = ImageView(placeholder="Streaming camera feed...")
        self.cam_view.setMinimumSize(540, 300)
        self.cam_view.set_edit_rect(roi, editable=False)
        self.body.addWidget(self.cam_view)

        self.status_lbl = label("Acquiring baseline empty workspace...", "H3")
        self.body.addWidget(self.status_lbl)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.body.addWidget(self.bar)

        self.stats_lbl = label("Baseline: pending | Motion events detected: 0 | Live status: Waiting", "Muted")
        self.body.addWidget(self.stats_lbl)

        self.btn_recapture = button("Recapture Empty Zone", None, self._capture_baseline, ico="camera")
        self.btn_ok = button("OK - Complete Teaching", "primary", self._finish, ico="check_circle")
        self.btn_cancel = button("Cancel", None, self.reject)

        self.footer.addWidget(self.btn_recapture)
        self.footer.addStretch(1)
        self.footer.addWidget(self.btn_cancel)
        self.footer.addWidget(self.btn_ok)

        # Project workspace live on physical table immediately
        self._project_live_roi()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

        QTimer.singleShot(300, self._capture_baseline)

    def _project_live_roi(self) -> None:
        cal = self.ctx.calibration
        if cal is not None and self.roi:
            try:
                x, y, w, h = self.roi
                corners_px = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
                poly_mm = cal.pixel_poly_to_bench(corners_px)
                items = self.page._get_live_items()
                self.page.main.projector.set_live_scene(items, highlight=self.det["id"],
                                                        live_roi_poly=poly_mm, title=f"Perform Activity: {self.det['name']}")
            except Exception:
                pass

    def _capture_baseline(self) -> None:
        try:
            sig, heights = self.page._signals(5)
            self.baseline_sig = sig
            self.baseline_depth = sig[0] if sig else None
            self.status_lbl.setText("Baseline acquired! Now perform the activity inside the workspace.")
            self.status_lbl.setStyleSheet(f"color: {C['ok']}; font-weight: bold;")
        except Exception as exc:
            self.status_lbl.setText(f"Waiting for camera frames: {exc}")

    def _tick(self) -> None:
        f = self.ctx.latest_frame()
        if f is None:
            return
        img = frame_view(f, self.page.stream.currentText())
        self.cam_view.set_image(img)
        # Check motion relative to baseline in workspace ROI
        if self.baseline_depth is not None and f.height_mm is not None and self.roi:
            x, y, w, h = [int(v) for v in self.roi]
            H, W = f.height_mm.shape
            if 0 <= y and y + h <= H and 0 <= x and x + w <= W:
                curr_roi = f.height_mm[y:y + h, x:x + w]
                base_roi = self.baseline_depth[y:y + h, x:x + w]
                diff = np.abs(curr_roi - base_roi)
                valid = np.isfinite(diff)
                if np.any(valid):
                    motion_level = float(np.nanpercentile(diff[valid], 90))
                    val = min(100, int(motion_level * 2))
                    self.bar.setValue(val)
                    if motion_level > 25.0:
                        self.motion_count += 1
                    self.stats_lbl.setText(f"Baseline: OK | Motion level: {motion_level:.1f} mm | Frames recorded: {self.motion_count}")

    def _finish(self) -> None:
        if self.baseline_depth is None or self.baseline_sig is None:
            return error(self, "No baseline captured. Keep the workspace empty and click 'Recapture Empty Zone'.")
        try:
            model, arrays = teach_zone_baseline(self.roi, self.baseline_sig[0])
            self.ctx.store_teaching(self.det["id"], model, arrays)
            self.timer.stop()
            self.accept()
        except Exception as exc:
            error(self, str(exc))

    def closeEvent(self, ev) -> None:
        self.timer.stop()
        super().closeEvent(ev)

    def reject(self) -> None:
        self.timer.stop()
        super().reject()


class TeachObjectDialog(Popup):
    """Arkite Guided Object Teaching Dialog.
    Guides the user step-by-step:
      Step 1: Capture empty zone snapshot (Without object)
      Step 2: Capture object snapshot (With object)
      Step 3: Verify and complete teaching.
    """

    def __init__(self, parent, page, det: dict, roi: list):
        super().__init__(parent, f"Teach Object: {det['name']}",
                         "Follow the steps below to capture without and with snapshots for teaching.",
                         ico="cube", width=760)
        self.page = page
        self.det = det
        self.roi = roi
        self.ctx = page.ctx
        self.caps = dict(page.caps)

        self.cam_view = ImageView(placeholder="Streaming camera feed...")
        self.cam_view.setMinimumSize(540, 260)
        self.cam_view.set_edit_rect(roi, editable=False)
        self.body.addWidget(self.cam_view)

        # Snapshots row
        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(12)

        # Card A: Without
        card_a = QFrame()
        card_a.setStyleSheet(f"QFrame {{ background: white; border: 1px solid {C['line']}; border-radius: 4px; padding: 4px; }}")
        la = QVBoxLayout(card_a)
        la.setSpacing(4)
        self.lbl_a_title = QLabel(f"<b>Step 1: Without {det['name']}</b>")
        self.lbl_a_title.setAlignment(Qt.AlignCenter)
        self.snap_a = QLabel("Empty zone not captured")
        self.snap_a.setAlignment(Qt.AlignCenter)
        self.snap_a.setMinimumHeight(100)
        self.snap_a.setStyleSheet(f"border: 1px dashed {C['line']}; background: #F8F9FA; color: {C['faint']};")
        self.btn_cap_a = button("1. Capture Without", "primary", self._capture_without, ico="camera")
        la.addWidget(self.lbl_a_title)
        la.addWidget(self.snap_a, 1)
        la.addWidget(self.btn_cap_a)
        cards_lay.addWidget(card_a, 1)

        # Card B: With
        card_b = QFrame()
        card_b.setStyleSheet(f"QFrame {{ background: white; border: 1px solid {C['line']}; border-radius: 4px; padding: 4px; }}")
        lb = QVBoxLayout(card_b)
        lb.setSpacing(4)
        self.lbl_b_title = QLabel(f"<b>Step 2: With {det['name']}</b>")
        self.lbl_b_title.setAlignment(Qt.AlignCenter)
        self.snap_b = QLabel("Object zone not captured")
        self.snap_b.setAlignment(Qt.AlignCenter)
        self.snap_b.setMinimumHeight(100)
        self.snap_b.setStyleSheet(f"border: 1px dashed {C['line']}; background: #F8F9FA; color: {C['faint']};")
        self.btn_cap_b = button("2. Capture With", None, self._capture_with, ico="camera")
        lb.addWidget(self.lbl_b_title)
        lb.addWidget(self.snap_b, 1)
        lb.addWidget(self.btn_cap_b)
        cards_lay.addWidget(card_b, 1)

        self.body.addLayout(cards_lay)

        self.status_lbl = label("Step 1: Remove the object from the working zone, then click '1. Capture Without'.", "Muted")
        self.body.addWidget(self.status_lbl)

        # Footer buttons
        self.btn_cancel = button("Cancel", None, self.reject)
        self.btn_ok = button("OK - Complete Teaching", "primary", self._finish, ico="check_circle")
        self.btn_ok.setEnabled(False)

        self.footer.addStretch(1)
        self.footer.addWidget(self.btn_cancel)
        self.footer.addWidget(self.btn_ok)

        # Populate if already captured
        self._refresh_snapshots()

        # Project workspace live on physical table immediately
        self._project_live_roi()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

    def _project_live_roi(self) -> None:
        cal = self.ctx.calibration
        if cal is not None and self.roi:
            try:
                x, y, w, h = self.roi
                corners_px = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
                poly_mm = cal.pixel_poly_to_bench(corners_px)
                items = self.page._get_live_items()
                self.page.main.projector.set_live_scene(items, highlight=self.det["id"],
                                                        live_roi_poly=poly_mm, title=f"Teach: {self.det['name']}")
            except Exception:
                pass

    def _refresh_snapshots(self) -> None:
        sensor = self.page.mode_lbl.currentData() or self.det.get("sensor", "DEPTH")
        x, y, w, h = [int(v) for v in self.roi]
        if "without" in self.caps:
            sig = self.caps["without"][0]
            crop = np.nanmedian(np.stack(sig), axis=0)[y:y + h, x:x + w]
            img = height_to_color(crop, 120) if sensor != "IR" else crop.astype(np.uint8)
            pm = _pix(img, 240, 95)
            if pm:
                self.snap_a.setPixmap(pm)
            self.lbl_a_title.setText(f"<b>Step 1: Without {self.det['name']}</b> <span style='color:{C['ok']};'>✔ Ready</span>")
            self.btn_cap_a.setText("Recapture Without")

        if "with" in self.caps:
            sig = self.caps["with"][0]
            crop = np.nanmedian(np.stack(sig), axis=0)[y:y + h, x:x + w]
            img = height_to_color(crop, 120) if sensor != "IR" else crop.astype(np.uint8)
            pm = _pix(img, 240, 95)
            if pm:
                self.snap_b.setPixmap(pm)
            self.lbl_b_title.setText(f"<b>Step 2: With {self.det['name']}</b> <span style='color:{C['ok']};'>✔ Ready</span>")
            self.btn_cap_b.setText("Recapture With")

        if "without" in self.caps and "with" in self.caps:
            self.status_lbl.setText("Both snapshots captured! Click 'OK - Complete Teaching' to finish.")
            self.status_lbl.setStyleSheet(f"color: {C['ok']}; font-weight: bold;")
            self.btn_ok.setEnabled(True)
        elif "without" in self.caps:
            self.status_lbl.setText("Step 1 done. Place the object inside the working zone and click '2. Capture With'.")
            self.status_lbl.setStyleSheet("font-weight: bold;")
            self.btn_cap_b.setStyleSheet(f"background: {C['primary']}; color: white; font-weight: bold;")

    def _capture_without(self) -> None:
        try:
            sig, heights = self.page._signals()
            self.caps["without"] = (sig, heights)
            self._refresh_snapshots()
        except Exception as exc:
            error(self, str(exc))

    def _capture_with(self) -> None:
        try:
            sig, heights = self.page._signals()
            self.caps["with"] = (sig, heights)
            self._refresh_snapshots()
        except Exception as exc:
            error(self, str(exc))

    def _tick(self) -> None:
        f = self.ctx.latest_frame()
        if f is None:
            return
        img = frame_view(f, self.page.stream.currentText())
        self.cam_view.set_image(img)

    def _finish(self) -> None:
        if "without" not in self.caps or "with" not in self.caps:
            return error(self, "Please capture both snapshots first.")
        try:
            d = self.det
            sensor = self.page.mode_lbl.currentData() or d["sensor"]
            p = {**OBJECT_DEFAULTS, **d["params"]}
            model, arrays = teach_object(self.roi, self.caps["without"][0], self.caps["with"][0], sensor,
                                         self.page.tolerance.currentData(), self.caps["with"][1], p["min_diff"])
            self.ctx.store_teaching(d["id"], model, arrays)
            self.page.caps = self.caps
            self.timer.stop()
            self.accept()
        except Exception as exc:
            error(self, str(exc))

    def closeEvent(self, ev) -> None:
        self.timer.stop()
        super().closeEvent(ev)

    def reject(self) -> None:
        self.timer.stop()
        super().reject()


class DetectionsPage(Page):
    def __init__(self, ctx, executor, main):
        super().__init__()
        self.ctx, self.ex, self.main = ctx, executor, main
        self.det: dict | None = None
        self.rows: list[dict] = []
        self.items: dict[int, QTreeWidgetItem] = {}
        self.caps: dict = {}
        self.qc_samples: list[tuple[str, str, np.ndarray]] = []
        self.roi = None
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # ---------------------------------------------------------------- tree
        left = QFrame()
        left.setObjectName("Panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 8, 8)
        self.b_new = button("New", "primary", ico="plus")
        m = QMenu(self)
        ORDERED_TYPES_1 = ["OBJECT", "OBJECT_QUALITY", "TOOL", "PICKING_BIN", "BUTTON",
                           "ACTIVITY", "QUALITY", "TEXT_OCR", "BARCODE"]
        ORDERED_TYPES_2 = ["GROUP", "CADCAM"]

        for t in ORDERED_TYPES_1:
            act = m.addAction(icon(TYPE_ICON[t], C["ink"], 16), TYPE_LABEL[t], lambda t=t: self.new(t, False))
            if t in TYPE_SHORTCUT:
                act.setShortcut(QKeySequence(TYPE_SHORTCUT[t]))
        m.addSeparator()
        for t in ORDERED_TYPES_2:
            act = m.addAction(icon(TYPE_ICON[t], C["ink"], 16), TYPE_LABEL[t], lambda t=t: self.new(t, False))
            if t in TYPE_SHORTCUT:
                act.setShortcut(QKeySequence(TYPE_SHORTCUT[t]))

        m.addSeparator()
        sw_menu = m.addMenu(icon("connectivity", C["ink"], 16), "Station-wide (all jobs)")
        for t in ORDERED_TYPES_1 + ORDERED_TYPES_2:
            sw_menu.addAction(icon(TYPE_ICON[t], C["ink"], 16), TYPE_LABEL[t], lambda t=t: self.new(t, True))

        self.b_new.setMenu(m)
        self.new_menu_obj = m
        ll.addLayout(hrow(self.b_new, None, tool("expand", "Expand all", lambda: self.tree.expandAll()),
                          tool("collapse", "Collapse all", lambda: self.tree.collapseAll())))
        self.search = SearchBox("Search for detections")
        self.search.textChanged.connect(self._filter)
        ll.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIconSize(QSize(17, 17))
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.tree.setColumnWidth(1, 30)
        self.tree.currentItemChanged.connect(self._selected)
        ll.addWidget(self.tree, 1)
        left.setMinimumWidth(270)
        split.addWidget(left)

        # ---------------------------------------------------------------- centre tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._teaching_tab(), "Teaching")
        self.tabs.addTab(self._finetune_tab(), "Fine tune")
        self.tabs.addTab(self._projector_tab(), "Projector")
        self.tabs.currentChanged.connect(self._tab_changed)
        split.addWidget(self.tabs)
        split.setSizes([290, 900])
        self.props = PropertiesPanel(width=300)
        root.addWidget(self.props)
        self._t = QTimer(self)
        self._t.timeout.connect(self._live)
        self._t.start(200)
        main.job_context_changed.connect(lambda _: self.on_show() if self.isVisible() else None)

    def _tab_changed(self, idx: int) -> None:
        if idx in (1, 2):
            self.main.projector.set_live_scene(self._get_live_items(), highlight=self.det["id"] if self.det else None,
                                               title="Fine Tune" if idx == 1 else "Projector Bench")
        else:
            self._bench_scene()

    # ================================================================== tab builders
    def _teaching_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        col = QVBoxLayout()
        self.sensor_lbl = label("", "H3")
        self.stream = QComboBox()
        self.stream.addItems(["COLOR", "IR", "HEIGHT", "DEPTH", "VISION"])
        col.addLayout(hrow(icon_lbl := QLabel(), self.sensor_lbl, None, label("Stream", "Muted"), self.stream,
                           tool("zoom_in", "Zoom in", lambda: self.view.zoom_by(1.25)),
                           tool("zoom_out", "Zoom out", lambda: self.view.zoom_by(0.8)),
                           tool("fit", "Fit", lambda: self.view.fit())))
        icon_lbl.setPixmap(icon("camera", C["ink"], 18).pixmap(18, 18))
        self.view = ImageView(placeholder="Waiting for camera")
        self.view.rect_changed.connect(self._roi_changed)
        self.view.rect_dragging.connect(self._roi_dragging)
        self.view.hovered.connect(self._hover)
        col.addWidget(self.view, 1)
        self.readout = label("Mouse wheel: zoom   Right button: move   Left drag: working zone", "Muted")
        col.addWidget(self.readout)
        lay.addLayout(col, 1)

        side = QWidget()
        side.setFixedWidth(270)
        self.side = QVBoxLayout(side)
        self.side.setContentsMargins(0, 0, 0, 0)
        self.side.setSpacing(6)
        self.snap_a, self.snap_b = SnapBox("Without"), SnapBox("With")
        self.btn_a = button("Capture", None, lambda: self.capture("without"), ico="camera")
        self.btn_b = button("Capture", None, lambda: self.capture("with"), ico="camera")
        self.side.addWidget(self.snap_a)
        self.side.addWidget(self.btn_a)
        self.side.addWidget(self.snap_b)
        self.side.addWidget(self.btn_b)
        # quality check classes
        self.qc_box = QWidget()
        ql = QVBoxLayout(self.qc_box)
        ql.setContentsMargins(0, 0, 0, 0)
        self.qc_scroll = QScrollArea()
        self.qc_scroll.setWidgetResizable(True)
        self.qc_inner = QWidget()
        self.qc_inner.setStyleSheet("background: white;")
        self.qc_lay = QVBoxLayout(self.qc_inner)
        self.qc_lay.setContentsMargins(4, 4, 4, 4)
        self.qc_scroll.setWidget(self.qc_inner)
        ql.addWidget(self.qc_scroll, 1)
        ql.addLayout(hrow(button("Add class", None, self.add_class, ico="plus"), None))
        self.side.addWidget(self.qc_box, 1)
        hint = QFrame()
        hint.setObjectName("Hint")
        hl = QHBoxLayout(hint)
        hl.setContentsMargins(6, 4, 6, 4)
        self.hint_lbl = label("", "Muted", wrap=True)
        hl.addWidget(self.hint_lbl)
        self.side.addWidget(hint)
        g = QGridLayout()
        self.tolerance = QComboBox()
        for k in TOLERANCE:
            self.tolerance.addItem(TOL_LABEL[k], k)
        self.tolerance.setCurrentIndex(2)
        self.algo = QComboBox()
        self.algo.addItems(list(QC_ALGOS))
        self.mode_lbl = QComboBox()
        self.l_tol, self.l_algo = label("Position tolerance"), label("Algorithm")
        g.addWidget(label("Sensor"), 0, 0)
        g.addWidget(self.mode_lbl, 0, 1)
        g.addWidget(self.l_tol, 1, 0)
        g.addWidget(self.tolerance, 1, 1)
        g.addWidget(self.l_algo, 2, 0)
        g.addWidget(self.algo, 2, 1)
        self.side.addLayout(g)
        self.mode_lbl.currentIndexChanged.connect(self._sensor_changed)
        self.b_cancel = button("Cancel", None, self._show_det)
        self.b_teach = button("Teach", "primary", self.teach, ico="teach")
        self.side.addLayout(hrow(self.b_cancel, self.b_teach))
        lay.addWidget(side)
        return w

    def _finetune_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        self.ft_state = QLabel("-")
        self.ft_state.setFont(font(20, display=True))
        top.addWidget(self.ft_state)
        top.addStretch(1)
        self.ft_detail = label("", "Muted")
        top.addWidget(self.ft_detail)
        lay.addLayout(top)
        self.ft_bar = QProgressBar()
        self.ft_bar.setRange(0, 100)
        self.ft_bar.setFixedHeight(14)
        lay.addWidget(self.ft_bar)
        lay.addWidget(label("Live score of the selected detection. Adjust thresholds while placing / removing the "
                            "object or performing the activity, then save.", "Muted", wrap=True))
        self.ft_form = ParamForm()
        lay.addWidget(self.ft_form)
        self.b_ft_save = button("Save fine-tune", "primary", self.save_finetune, ico="save")
        lay.addLayout(hrow(None, self.b_ft_save))
        lay.addStretch(1)
        return w

    def _projector_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        self.bench = BenchCanvas(self.ctx)
        self.bench.zone_clicked.connect(lambda did: self.select_detection(did))
        self.bench.zone_moved.connect(self._zone_moved)
        self.bench.zone_dragging.connect(self._zone_dragging)
        lay.addLayout(hrow(label("Drag the selected zone to shift its projection on the bench.", "Muted"), None,
                           button("Reset shift", None, self._reset_shift, ico="reset"),
                           button("Show all zones on bench", None, lambda: self.main.projector.set_mode("TEST"),
                                  ico="projector"),
                           button("Normal guidance", None, lambda: self.main.projector.set_mode("RUN")),
                           tool("fit", "Reset view", lambda: self.bench.reset_view())))
        lay.addWidget(self.bench, 1)
        return w

    # ================================================================== loading
    def on_show(self) -> None:
        self.reload()

    def on_hide(self) -> None:
        self.ctx.load_engine(self.ex.job["id"] if self.ex.job else None)
        self.ctx.engine.set_watch(set())
        self.main.projector.highlight = None

    def editable(self) -> bool:
        return bool(self.ctx.session and self.ctx.session.can(Perm.TEACH_DETECTIONS)) and self.ex.mode != "RUNNING"

    def reload(self) -> None:
        job_id = self.main.edit_job_id
        if self.ex.mode != "RUNNING":
            for w in self.ctx.load_engine(job_id):
                self.main.add_message(w, "error", "Detections")
        self.rows = self.ctx.detections.list_for(job_id)
        job = self.ctx.jobs.get(job_id) if job_id else None
        sel = self.det["id"] if self.det else None
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        station = [d for d in self.rows if d["job_id"] is None]
        jobspec = [d for d in self.rows if d["job_id"] is not None]
        roots = []
        for title, rows in ((f"Station-wide ({len(station)})", station),
                            (f"Job-specific ({job['code'] if job else 'no job'}) ({len(jobspec)})", jobspec)):
            top = QTreeWidgetItem([title, ""])
            top.setForeground(0, QColor(C["primary"]))
            top.setFont(0, font(9, True))
            self.tree.addTopLevelItem(top)
            roots.append(top)
            groups: dict[str, QTreeWidgetItem] = {}
            for d in rows:
                parent = top
                if d["group_name"]:
                    if d["group_name"] not in groups:
                        g = QTreeWidgetItem([f"{d['group_name']}", ""])
                        top.addChild(g)
                        groups[d["group_name"]] = g
                    parent = groups[d["group_name"]]
                it = QTreeWidgetItem([d["name"], ""])
                it.setIcon(0, icon(TYPE_ICON[d["det_type"]], C["ink"], 17))
                it.setData(0, Qt.UserRole, d["id"])
                it.setToolTip(0, f"{TYPE_LABEL[d['det_type']]} · {SENSOR_LABEL[d['sensor']]}")
                parent.addChild(it)
                self.items[d["id"]] = it
            for name, g in groups.items():
                g.setText(0, f"{name} ({g.childCount()})")
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._filter(self.search.text())
        if sel in self.items:
            self.tree.setCurrentItem(self.items[sel])
        else:
            self.det = None
            self._show_det()
        self.b_new.setEnabled(self.editable())
        self._bench_scene()

    def _filter(self, text: str) -> None:
        t = text.lower().strip()
        for did, it in self.items.items():
            it.setHidden(bool(t) and t not in it.text(0).lower())

    def select_detection(self, det_id: int) -> None:
        if det_id in self.items:
            self.tree.setCurrentItem(self.items[det_id])

    def new_menu(self) -> None:
        self.new_menu_obj.exec(self.b_new.mapToGlobal(self.b_new.rect().bottomLeft()))

    def _selected(self, cur, _prev) -> None:
        det_id = cur.data(0, Qt.UserRole) if cur else None
        self.det = next((d for d in self.rows if d["id"] == det_id), None)
        self._show_det()

    # ================================================================== show detection
    def _show_det(self) -> None:
        d = self.det
        self.caps = {}
        edit = self.editable()
        self.main.projector.highlight = d["id"] if d else None
        if d is None:
            self.sensor_lbl.setText("Select a detection")
            self.view.set_edit_rect(None, False)
            self.ctx.engine.set_watch(set())
            self._properties()
            for w in (self.btn_a, self.btn_b, self.b_teach, self.b_cancel):
                w.setEnabled(False)
            self.ft_form.build([], {})
            self._bench_scene()
            return
        t = d["det_type"]
        self.mode_lbl.blockSignals(True)
        self.mode_lbl.clear()
        for s in SENSORS[t]:
            self.mode_lbl.addItem(SENSOR_LABEL[s], s)
        self.mode_lbl.setCurrentIndex(max(0, self.mode_lbl.findData(d["sensor"])))
        self.mode_lbl.blockSignals(False)
        self.stream.setCurrentText(STREAM_FOR[d["sensor"]])
        self.sensor_lbl.setText(SENSOR_LABEL[d["sensor"]])
        self.roi = d["model"].get("roi")
        self.view.set_edit_rect(self.roi, edit)
        quality = t in ("QUALITY", "OBJECT_QUALITY", "TEXT_OCR", "BARCODE")
        zone = t in ("ACTIVITY", "BUTTON", "PICKING_BIN")
        is_obj = t in ("OBJECT", "TOOL", "CADCAM")
        self.qc_box.setVisible(quality)
        for w in (self.snap_a, self.btn_a):
            w.setVisible(not quality and not (t == "ACTIVITY"))
        for w in (self.snap_b, self.btn_b):
            w.setVisible(is_obj)
        self.l_tol.setVisible(is_obj)
        self.tolerance.setVisible(is_obj)
        self.l_algo.setVisible(quality)
        self.algo.setVisible(quality)
        if is_obj:
            self.snap_a.title.setText(f"Without {d['name']}")
            self.snap_b.title.setText(f"With {d['name']}")
            self.hint_lbl.setText("Set the working zone around the object, excluding the operator and other moving "
                                  "content. Teaching compares the without and with snapshots.")
        elif t == "ACTIVITY":
            self.hint_lbl.setText("Position the workspace with left-drag on camera, then click Teach to start live "
                                  "motion capture. Perform the activity inside the workspace in real time.")
        elif zone:
            self.snap_a.title.setText("Empty zone")
            self.hint_lbl.setText("Draw the zone where the hand works, keep it empty and capture. Then perform the "
                                  "activity - the counter on Fine tune must increase.")
        else:
            self.hint_lbl.setText("Draw the region to check. Add several samples per class under realistic "
                                  "variation; AUTO picks the best algorithm.")
            self._load_qc(d)
        self.snap_a.set(None)
        self.snap_b.set(None)
        self._thumbs_from_model(d)
        for w in (self.btn_a, self.btn_b, self.b_teach, self.b_cancel):
            w.setEnabled(edit)
        defaults_map = {
            "OBJECT": OBJECT_DEFAULTS, "TOOL": OBJECT_DEFAULTS, "CADCAM": OBJECT_DEFAULTS,
            "ACTIVITY": ACTIVITY_DEFAULTS, "PICKING_BIN": ACTIVITY_DEFAULTS, "BUTTON": BUTTON_DEFAULTS,
            "QUALITY": QUALITY_DEFAULTS, "OBJECT_QUALITY": QUALITY_DEFAULTS, "TEXT_OCR": {}, "BARCODE": {},
            "GROUP": {},
        }
        p = {**defaults_map.get(t, {}), **d["params"]}
        self.ft_form.build(PARAM_SPECS.get(t, []), p)
        self.ft_form.set_enabled(edit)
        self.b_ft_save.setEnabled(edit)
        self.ctx.engine.set_watch({d["id"]})
        self._properties()
        self._bench_scene()

    def _thumbs_from_model(self, d: dict) -> None:
        if not d["taught_at"] or d["det_type"] == "QUALITY":
            return
        for s in self.ctx.detections.samples(d["id"]):
            if s["kind"] != "REFS":
                continue
            from vista.vision.detectors import unpack_arrays
            arr = unpack_arrays(s["data"])
            if "ref_without" in arr:
                self.snap_a.set(_pix(height_to_color(arr["ref_without"], 120), 250, 118))
                self.snap_b.set(_pix(height_to_color(arr["ref_with"], 120), 250, 118))
                self.snap_a.title.setText(f"Without {d['name']} (Saved)")
                self.snap_b.title.setText(f"With {d['name']} (Saved)")
            elif "baseline" in arr:
                self.snap_a.set(_pix(height_to_color(arr["baseline"], 120), 250, 118))
                self.snap_a.title.setText("Empty zone (Saved)")

    def _properties(self) -> None:
        p = self.props
        p.clear()
        d = self.det
        if d is None:
            p.heading("Detection", "Select a detection or create one with New.")
            p.end()
            return
        edit = self.editable()
        p.heading("Detection", f"ID {d['id']} · " + ("station-wide" if d["job_id"] is None else "job-specific"))
        self.p_name = p.field("Name", QLineEdit(d["name"]))
        self.p_enabled = QComboBox()
        self.p_enabled.addItems(["On", "Off"])
        self.p_enabled.setCurrentIndex(0 if d["enabled"] else 1)
        p.field("Enabled", self.p_enabled)
        tbox = QComboBox()
        tbox.addItem(icon(TYPE_ICON[d["det_type"]], C["ink"], 16), TYPE_LABEL[d["det_type"]])
        tbox.setEnabled(False)
        p.field("Type", tbox)
        self.p_group = p.field("Group", QLineEdit(d["group_name"]))
        self.p_function = None
        if d["det_type"] == "BUTTON":
            self.p_function = QComboBox()
            self.p_function.addItems(["NONE", "RESET"])
            self.p_function.setCurrentText(d["params"].get("function", "NONE"))
            p.field("Station function", self.p_function)
        state = "Taught " + d["taught_at"].astimezone().strftime("%d-%m-%Y %H:%M") if d["taught_at"] else "Not taught"
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 4, 0, 0)
        rl.addWidget(chip(state, "ok" if d["taught_at"] else "warn"))
        if d["det_type"] == "QUALITY" and d["model"].get("algorithm"):
            rl.addWidget(chip(d["model"]["algorithm"].title(), "info"))
        rl.addStretch(1)
        p.body.addWidget(row)
        used = self.ctx.detections.usage(d["id"])
        p.heading("Used by")
        p.body.addWidget(label("\n".join(f"{u['job_code']} · {u['step_name']}" for u in used[:8]) or "No steps yet",
                               "Muted", wrap=True))
        for w in (self.p_name, self.p_enabled, self.p_group) + ((self.p_function,) if self.p_function else ()):
            w.setEnabled(edit)
        p.end()
        sv = button("Save", "primary", self.save_props, ico="save")
        cl = button("Clear teaching", None, self.clear_teaching)
        dl = button("", "danger", self.delete, "Delete detection", ico="trash")
        for b in (sv, cl, dl):
            b.setEnabled(edit)
        p.footer.addWidget(dl)
        p.footer.addWidget(cl)
        p.footer.addStretch(1)
        p.footer.addWidget(sv)

    # ================================================================== quality classes
    def _load_qc(self, d: dict) -> None:
        self.classes = list(d["params"].get("classes") or DEFAULT_CLASSES)
        self.qc_samples = [(s["class_name"], s["meta"].get("type", "CORRECT"), decode_png(s["data"]))
                           for s in self.ctx.detections.samples(d["id"]) if s["kind"] == "QC"]
        self.algo.setCurrentText(d["params"].get("algorithm", "AUTO"))
        self._render_classes()

    def _render_classes(self) -> None:
        while self.qc_lay.count():
            it = self.qc_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        edit = self.editable()
        for ci, c in enumerate(self.classes):
            head = QHBoxLayout()
            lvl = {"CORRECT": "ok", "WRONG": "ng", "MISSING": "warn"}[c["type"]]
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 2, 0, 6)
            bl.setSpacing(3)
            typ = QComboBox()
            typ.addItems(list(QC_TYPES))
            typ.setCurrentText(c["type"])
            typ.setFixedWidth(96)
            typ.setEnabled(edit)
            typ.currentTextChanged.connect(lambda v, i=ci: self._set_class_type(i, v))
            head.addWidget(chip(c["name"], lvl))
            head.addStretch(1)
            head.addWidget(typ)
            bl.addLayout(head)
            thumbs = QHBoxLayout()
            thumbs.setSpacing(3)
            for si, (cls, _t, img) in enumerate(self.qc_samples):
                if cls != c["name"]:
                    continue
                th = QLabel()
                th.setPixmap(_pix(img, 54, 54))
                th.setFixedSize(56, 56)
                th.setStyleSheet(f"border: 2px solid {C['ok'] if c['type'] == 'CORRECT' else C['ng']}; "
                                 "border-radius: 3px;")
                th.setToolTip("Double-click to remove")
                th.mouseDoubleClickEvent = (lambda _e, k=si: self._remove_sample(k)) if edit else (lambda _e: None)
                thumbs.addWidget(th)
            thumbs.addStretch(1)
            bl.addLayout(thumbs)
            add = button(f"Add {c['name'].lower()}", None, lambda _=False, n=c["name"]: self.add_sample(n), ico="camera")
            add.setEnabled(edit)
            bl.addWidget(add)
            self.qc_lay.addWidget(box)
        self.qc_lay.addStretch(1)

    def _set_class_type(self, i: int, v: str) -> None:
        self.classes[i]["type"] = v
        self.qc_samples = [(c, v if c == self.classes[i]["name"] else t, img) for c, t, img in self.qc_samples]
        self._render_classes()

    def add_class(self) -> None:
        if self.det is None or self.det["det_type"] != "QUALITY":
            return
        name, ok = QInputDialog.getText(self, "New class", "Class name (e.g. 'Clip not fully seated'):")
        if ok and name.strip() and name.strip() not in [c["name"] for c in self.classes]:
            self.classes.append({"name": name.strip(), "type": "WRONG"})
            self._render_classes()

    def add_sample(self, cls: str) -> None:
        if not self._can_teach():
            return
        if not self.roi:
            return error(self, "Draw the region to check on the image first.")
        x, y, w, h = [int(v) for v in self.roi]
        if self.qc_samples and self.qc_samples[0][2].shape[:2] != (h, w):
            if not confirm(self, "The region size changed - existing samples must be removed. Continue?"):
                return
            self.qc_samples = []
        f = self.ctx.latest_frame()
        img = None if f is None else (f.vision if self.mode_lbl.currentData() == "VISION" else f.color)
        if img is None:
            return error(self, "No image from the selected sensor.")
        ctype = next(c["type"] for c in self.classes if c["name"] == cls)
        self.qc_samples.append((cls, ctype, img[y:y + h, x:x + w].copy()))
        self._render_classes()

    def _remove_sample(self, k: int) -> None:
        if 0 <= k < len(self.qc_samples):
            del self.qc_samples[k]
            self._render_classes()

    # ================================================================== actions
    def _can_teach(self) -> bool:
        if self.det is None:
            return False
        try:
            self.ctx.require(Perm.TEACH_DETECTIONS)
        except PermissionDenied as exc:
            error(self, str(exc))
            return False
        if self.ex.mode == "RUNNING":
            error(self, "Teaching is not possible while an inspection is running.")
            return False
        return True

    def new(self, det_type: str, station_wide: bool) -> None:
        try:
            self.ctx.require(Perm.TEACH_DETECTIONS)
        except PermissionDenied as exc:
            return error(self, str(exc))
        job_id = None if station_wide else self.main.edit_job_id
        if not station_wide and job_id is None:
            return error(self, "Select a job first (Jobs page or Ctrl+P) for a job-specific detection.")
        name, ok = QInputDialog.getText(self, f"New {TYPE_LABEL[det_type].lower()}", "Name (used in instructions):")
        if not ok or not name.strip():
            return
        group, _ = QInputDialog.getText(self, "Group", "Group (optional, e.g. GAUGE, QUALITY CHECK):")
        params = {"classes": DEFAULT_CLASSES} if det_type in ("QUALITY", "OBJECT_QUALITY") else {}
        sensor_default = SENSORS[det_type][0] if SENSORS.get(det_type) else "DEPTH"
        try:
            det_id = self.ctx.detections.create(job_id, name.strip(), group.strip(), det_type,
                                                sensor_default, params)
        except Exception as exc:
            return error(self, f"Could not create detection (name already used?): {exc}")
        self.ctx.audit("DETECTION_CREATED", "detection", det_id, {"name": name, "type": det_type})
        self.det = {"id": det_id}
        self.reload()

    def _sensor_changed(self) -> None:
        if self.det is not None:
            s = self.mode_lbl.currentData()
            self.stream.setCurrentText(STREAM_FOR.get(s, "COLOR"))
            self.sensor_lbl.setText(SENSOR_LABEL.get(s, ""))

    def save_props(self) -> None:
        if not self._can_teach():
            return
        d = self.det
        params = dict(d["params"])
        if self.p_function is not None:
            params["function"] = self.p_function.currentText()
        if d["det_type"] == "QUALITY":
            params["classes"] = self.classes
            params["algorithm"] = self.algo.currentText()
        try:
            self.ctx.detections.update_props(d["id"], self.p_name.text().strip() or d["name"],
                                             self.p_group.text().strip(), self.mode_lbl.currentData() or d["sensor"],
                                             self.p_enabled.currentIndex() == 0, params)
        except Exception as exc:
            return error(self, f"Save failed: {exc}")
        if d["job_id"]:
            self.ctx.jobs.mark_draft(d["job_id"], self.ctx.session.user_id)
        self.ctx.audit("DETECTION_UPDATED", "detection", d["id"], {"params": params})
        self.reload()

    def save_finetune(self) -> None:
        if not self._can_teach():
            return
        d = self.det
        params = {**d["params"], **self.ft_form.values()}
        self.ctx.detections.update_props(d["id"], d["name"], d["group_name"], d["sensor"], d["enabled"], params)
        if d["job_id"]:
            self.ctx.jobs.mark_draft(d["job_id"], self.ctx.session.user_id)
        self.ctx.audit("DETECTION_FINETUNED", "detection", d["id"], {"params": params})
        self.reload()

    def _roi_changed(self, rect) -> None:
        if self.det is not None and self.editable():
            self.roi = rect
            self.caps.clear()
            d = self.det
            t = d["det_type"]
            is_obj = t in ("OBJECT", "TOOL", "CADCAM")
            if is_obj:
                self.snap_a.title.setText(f"Without {d['name']}")
                self.snap_a.set(None, "Zone updated.\nClick '1. Capture Without'")
                self.snap_b.title.setText(f"With {d['name']}")
                self.snap_b.set(None, "Click '2. Capture With'")
                self.btn_a.setText("1. Capture Without")
                self.btn_b.setText("2. Capture With")
                self.btn_a.setStyleSheet("")
                self.btn_b.setStyleSheet("")
                self.readout.setText("Working zone updated. Step 1: Remove object and click '1. Capture Without'. Step 2: Place object and click '2. Capture With'.")
            elif t in ("BUTTON", "PICKING_BIN"):
                self.snap_a.title.setText("Empty zone")
                self.snap_a.set(None, "Zone updated.\nClick 'Capture'")
                self.btn_a.setText("Capture Empty Zone")
                self.btn_a.setStyleSheet("")
                self.readout.setText("Working zone updated. Keep zone empty and click 'Capture'.")
            else:
                self.readout.setText(f"Working zone {rect} - click Teach to configure")
            self._roi_dragging(rect)

    def _hover(self, x: float, y: float) -> None:
        f = self.ctx.latest_frame()
        cal = self.ctx.calibration
        if f is None or f.depth_mm is None:
            return
        H, W = f.depth_mm.shape
        if not (0 <= x < W and 0 <= y < H):
            return
        txt = f"Sensor stream {SENSOR_LABEL.get(self.mode_lbl.currentData(), '')}   Pixel {int(x)}, {int(y)}"
        if cal is not None and f.height_mm is not None:
            h = f.height_mm[int(y), int(x)]
            if np.isfinite(h):
                bx, by = cal.pixel_to_bench(x, y, float(h))
                txt += f"   Location {bx:.1f}, {by:.1f}, {h:.1f} mm"
        if f.ir is not None:
            txt += f"   IR {int(f.ir[int(y), int(x)])}"
        self.readout.setText(txt)

    def _signals(self, n: int = 8):
        frames = self.ctx.worker.recent(n) if self.ctx.worker else []
        if not frames:
            raise TeachError("No camera frames available.")
        if self.mode_lbl.currentData() == "IR":
            sig = [f.ir.astype(np.float32) for f in frames if f.ir is not None]
        else:
            sig = [f.height_mm for f in frames if f.height_mm is not None]
        if not sig:
            raise TeachError("No height data - calibrate the workstation first.")
        return sig, [f.height_mm for f in frames if f.height_mm is not None]

    def capture(self, kind: str) -> None:
        if not self._can_teach():
            return
        if not self.roi:
            return error(self, "Draw the working zone on the image first.")
        d = self.det
        kind = "baseline" if d["det_type"] in ("ACTIVITY", "BUTTON", "PICKING_BIN") else kind
        try:
            sig, heights = self._signals()
        except TeachError as exc:
            return error(self, str(exc))
        self.caps[kind] = (sig, heights)
        x, y, w, h = [int(v) for v in self.roi]
        crop = np.nanmedian(np.stack(sig), axis=0)[y:y + h, x:x + w]
        img = height_to_color(crop, 120) if self.mode_lbl.currentData() != "IR" else crop.astype(np.uint8)
        if kind == "with":
            self.snap_b.set(_pix(img, 250, 118))
            self.snap_b.title.setText(f"With {d['name']} (Ready)")
            self.btn_b.setText("2. Recapture With")
            self.btn_b.setStyleSheet(f"color: {C['ok']}; font-weight: bold;")
        elif kind == "without":
            self.snap_a.set(_pix(img, 250, 118))
            self.snap_a.title.setText(f"Without {d['name']} (Ready)")
            self.btn_a.setText("1. Recapture Without")
            self.btn_a.setStyleSheet(f"color: {C['ok']}; font-weight: bold;")
            self.btn_b.setStyleSheet(f"background: {C['primary']}; color: white; font-weight: bold;")
        else:
            self.snap_a.set(_pix(img, 250, 118))
            self.snap_a.title.setText("Empty zone (Ready)")
            self.btn_a.setText("Recapture Empty Zone")
            self.btn_a.setStyleSheet(f"color: {C['ok']}; font-weight: bold;")

    def teach(self) -> None:
        if not self._can_teach():
            return
        d = self.det
        if not self.roi:
            return error(self, "Draw the working zone on the image first.")
        sensor = self.mode_lbl.currentData() or d["sensor"]
        try:
            if sensor != d["sensor"]:
                self.ctx.detections.update_props(d["id"], d["name"], d["group_name"], sensor, d["enabled"],
                                                 d["params"])
            if d["det_type"] in ("OBJECT", "TOOL", "CADCAM"):
                if "without" not in self.caps or "with" not in self.caps:
                    dlg = TeachObjectDialog(self, self, d, self.roi)
                    if dlg.exec() != QDialog.Accepted:
                        return
                    info(self, f"'{d['name']}' taught successfully. Test it now on Fine tune: the live state must follow reality.")
                    self.reload()
                    return
                p = {**OBJECT_DEFAULTS, **d["params"]}
                model, arrays = teach_object(self.roi, self.caps["without"][0], self.caps["with"][0], sensor,
                                             self.tolerance.currentData(), self.caps["with"][1], p["min_diff"])
                self.ctx.store_teaching(d["id"], model, arrays)
            elif d["det_type"] in ("ACTIVITY", "PICKING_BIN", "BUTTON"):
                if d["det_type"] == "ACTIVITY":
                    dlg = TeachActivityDialog(self, self, d, self.roi)
                    if dlg.exec() != QDialog.Accepted:
                        return
                    info(self, f"Activity '{d['name']}' taught successfully with live motion capture.")
                    self.reload()
                    return
                if "baseline" not in self.caps:
                    if confirm(self, f"Empty zone snapshot is needed to teach '{d['name']}'.\nEnsure the zone is empty and click OK to capture now."):
                        self.capture("baseline")
                        if "baseline" not in self.caps:
                            return
                    else:
                        return
                model, arrays = teach_zone_baseline(self.roi, self.caps["baseline"][0])
                self.ctx.store_teaching(d["id"], model, arrays)
            else:
                types = {c["name"]: c["type"] for c in self.classes}
                self.qc_samples = [(c, types.get(c, t), img) for c, t, img in self.qc_samples if c in types]
                params = {**d["params"], "classes": self.classes, "algorithm": self.algo.currentText()}
                self.ctx.detections.update_props(d["id"], d["name"], d["group_name"], sensor, d["enabled"], params)
                heights = None
                if sensor == "COLOR":
                    try:
                        heights = self._signals(5)[1] or None
                    except TeachError:
                        heights = None
                model, arrays = teach_quality(self.roi, self.qc_samples, self.algo.currentText(), heights)
                self.ctx.store_teaching(d["id"], model, arrays, self.qc_samples)
        except (TeachError, PermissionDenied) as exc:
            return error(self, str(exc))
        info(self, f"'{d['name']}' taught. Test it now on Fine tune: the live state must follow reality.")
        self.reload()

    def clear_teaching(self) -> None:
        if self._can_teach() and confirm(self, f"Remove all teaching data of '{self.det['name']}'?"):
            self.ctx.detections.clear_teaching(self.det["id"])
            self.ctx.audit("DETECTION_CLEARED", "detection", self.det["id"], {})
            self.reload()

    def delete(self) -> None:
        if not self._can_teach():
            return
        used = self.ctx.detections.usage(self.det["id"])
        msg = f"Delete detection '{self.det['name']}'?"
        if used:
            msg += "\n\nUsed by:\n" + "\n".join(f"{u['job_code']}: {u['step_name']}" for u in used[:10]) + \
                   "\nThese steps lose their detection and the job cannot be released until fixed."
        if confirm(self, msg):
            self.ctx.detections.delete(self.det["id"])
            self.ctx.audit("DETECTION_DELETED", "detection", self.det["id"], {"name": self.det["name"]})
            self.det = None
            self.reload()

    # ================================================================== projector tab
    def _get_live_items(self, active_drag_id: int | None = None, active_dx: float = 0.0, active_dy: float = 0.0) -> list[dict]:
        items = []
        for d in self.rows:
            zone = d["model"].get("zone_mm")
            if zone:
                off = list(d["params"].get("proj_offset_mm") or [0, 0])
                if active_drag_id == d["id"]:
                    off = [active_dx, active_dy]
                items.append({"id": d["id"], "poly": zone, "label": d["name"], "color": "#F2E600", "fill": 0.9,
                              "offset": tuple(off)})
        return items

    def _bench_scene(self) -> None:
        items = self._get_live_items()
        self.bench.set_scene(items, selected=self.det["id"] if self.det else None, editable=self.editable())
        if self.isVisible():
            self.main.projector.set_live_scene(items, highlight=self.det["id"] if self.det else None)

    def _zone_dragging(self, det_id: int, dx: float, dy: float) -> None:
        items = self._get_live_items(active_drag_id=det_id, active_dx=dx, active_dy=dy)
        self.main.projector.set_live_scene(items, highlight=det_id)

    def _roi_dragging(self, roi_px: list) -> None:
        if not roi_px or not self.det:
            return
        cal = self.ctx.calibration
        if cal is not None:
            try:
                x, y, w, h = roi_px
                corners_px = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
                poly_mm = cal.pixel_poly_to_bench(corners_px)
                items = self._get_live_items()
                self.main.projector.set_live_scene(items, highlight=self.det["id"],
                                                   live_roi_poly=poly_mm,
                                                   title=f"Drawing Workspace: {self.det['name']}")
            except Exception:
                pass

    def _zone_moved(self, det_id: int, dx: float, dy: float) -> None:
        d = next((r for r in self.rows if r["id"] == det_id), None)
        if d is None or not self.editable():
            return
        params = {**d["params"], "proj_offset_mm": [dx, dy]}
        self.ctx.detections.update_props(det_id, d["name"], d["group_name"], d["sensor"], d["enabled"], params)
        d["params"] = params
        self.ctx.audit("PROJECTION_SHIFTED", "detection", det_id, {"offset_mm": [dx, dy]})
        self.ctx.load_engine(self.main.edit_job_id)
        self._bench_scene()

    def _reset_shift(self) -> None:
        if self.det is not None and self.editable():
            self._zone_moved(self.det["id"], 0.0, 0.0)
            self._bench_scene()

    # ================================================================== live
    def on_frame(self, frame) -> None:
        if not self.isVisible() or self.tabs.currentIndex() != 0:
            return
        self.view.set_image(frame_view(frame, self.stream.currentText()))
        vision = self.stream.currentText() == "VISION"
        ovs = []
        for d in self.rows:
            zone = d["model"].get("zone_px")
            if not zone or (d["sensor"] == "VISION") != vision:
                continue
            sel = self.det is not None and d["id"] == self.det["id"]
            if sel:
                continue
            ovs.append({"poly": zone, "color": "#E6D200", "fill": 0.25, "width": 1})
        if self.det is not None and self.det["model"].get("zone_px") and not self.view.edit_rect:
            ovs.append({"poly": self.det["model"]["zone_px"], "color": C["primary"], "label": self.det["name"]})
        self.view.set_overlays(ovs)

    def _live(self) -> None:
        if not self.isVisible():
            return
        states = self.main.last_states
        for det_id, it in self.items.items():
            st = states.get(det_id)
            if st is not None:
                g, col = STATE_GLYPH.get(st.state, ("○", C["faint"]))
                it.setText(1, g)
                it.setForeground(1, QColor(col))
                it.setToolTip(1, st.state.replace("_", " ").lower())
        if self.det:
            st = states.get(self.det["id"])
            if st:
                col = STATE_GLYPH.get(st.state, ("", C["muted"]))[1]
                self.ft_state.setText(st.state.replace("_", " ").title())
                self.ft_state.setStyleSheet(f"color: {col};")
                self.ft_bar.setValue(int(min(1.0, st.score) * 100))
                extra = f"score {st.score:.2f}"
                if st.det_type in ("ACTIVITY", "BUTTON"):
                    extra += f" · count {st.counter}"
                if st.qc_class:
                    extra += f" · {st.qc_class}"
                if st.detail.get("occlusion") is not None:
                    extra += f" · occlusion {st.detail['occlusion']:.0%}"
                self.ft_detail.setText(extra)

        # In Fine tune subtab or Projector subtab, project live onto the workbench
        if self.isVisible() and self.tabs.currentIndex() in (1, 2):
            self.main.projector.set_live_scene(self._get_live_items(), highlight=self.det["id"] if self.det else None,
                                               title=f"Fine Tune: {self.det['name']}" if (self.det and self.tabs.currentIndex() == 1) else None)

    def hideEvent(self, e):  # noqa: N802
        if hasattr(self.main, "projector") and self.main.projector:
            self.main.projector.clear_live_scene()
        super().hideEvent(e)
