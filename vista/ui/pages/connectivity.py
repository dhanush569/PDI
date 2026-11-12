"""Connectivity page (Arkite layout): Displays · Sensors · Communication cards; configuration in pop-ups."""
from __future__ import annotations

import os
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMenu, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from vista.core.security import PermissionDenied, Perm
from vista.hardware.projector_control import OptomaSerial, PJLinkClient, ProjectorError
from vista.hardware.realsense_camera import PRESETS
from vista.ui.dialogs_extra import Popup
from vista.ui.icons import icon
from vista.ui.theme import C
from vista.ui.widgets.common import (ImageView, Page, PropertiesPanel, STREAM_LABELS, button, confirm, error,
                                     frame_view, hrow, label)


class _Async(QObject):
    done = Signal(str, str)

    def run(self, fn) -> None:
        def work():
            try:
                r = fn()
                self.done.emit(str(r) if r is not None else "Done", "ok")
            except AttributeError:
                self.done.emit("Not available over RS-232 - use PJLink (LAN) for status and image mute.", "error")
            except ProjectorError as exc:
                self.done.emit(str(exc), "error")
            except Exception as exc:
                self.done.emit(f"Error: {exc}", "error")
        threading.Thread(target=work, daemon=True).start()


class DeviceCard(QFrame):
    selected = Signal(str)

    def __init__(self, key: str, ico: str, title: str):
        super().__init__()
        self.key = key
        self.setObjectName("Card")
        self.setFixedSize(300, 138)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 0)
        lay.setSpacing(2)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icon(ico, C["ink"], 20).pixmap(20, 20))
        top.addWidget(ic)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600; font-size: 10.5pt;")
        top.addWidget(t, 1)
        self.status = QLabel()
        top.addWidget(self.status)
        lay.addLayout(top)
        self.sub = label("", wrap=False)
        self.sub.setStyleSheet(f"color: {C['text']};")
        self.line2 = label("", "Muted")
        lay.addWidget(self.sub)
        lay.addWidget(self.line2)
        lay.addStretch(1)
        foot = QFrame()
        foot.setStyleSheet(f"QFrame {{ border-top: 1px solid {C['line2']}; }}")
        self.foot = QHBoxLayout(foot)
        self.foot.setContentsMargins(0, 4, 0, 6)
        self.foot.setSpacing(4)
        lay.addWidget(foot)

    def set(self, sub: str, line2: str, state: str) -> None:
        self.sub.setText(sub)
        self.line2.setText(line2)
        status_map = {
            "ok": ("check_circle", C["ok"]),
            "error": ("x_circle", C["ng"]),
            "warn": ("alert", C["warn"]),
            "sim": ("info", C["warn"]),
            "idle": ("info", C["faint"]),
        }
        ico, col = status_map.get(state, ("info", C["faint"]))
        self.status.setPixmap(icon(ico, col, 20).pixmap(20, 20))

    def action(self, text: str, slot=None, menu: QMenu | None = None) -> None:
        b = button(text, None, slot)
        b.setStyleSheet("QPushButton { border: none; background: transparent; padding: 3px 6px; }"
                        f"QPushButton:hover {{ background: {C['hover']}; }}")
        if menu:
            b.setMenu(menu)
        self.foot.addWidget(b)

    def finish(self) -> None:
        self.foot.addStretch(1)

    def mark(self, on: bool) -> None:
        self.setObjectName("CardSel" if on else "Card")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):  # noqa: N802
        self.selected.emit(self.key)


# =============================================================================== pop-ups
class StreamPopup(Popup):
    def __init__(self, page, stream: str, title: str):
        super().__init__(page, title, "Live stream - mouse wheel zooms, right button pans", "camera", 900)
        self.page, self.stream = page, stream
        self.combo = QComboBox()
        for k, v in STREAM_LABELS.items():
            self.combo.addItem(v, k)
        self.combo.setCurrentIndex(max(0, self.combo.findData(stream)))
        self.body.addLayout(hrow(label("Stream", "Muted"), self.combo, None))
        self.view = ImageView()
        self.view.setMinimumSize(840, 500)
        self.body.addWidget(self.view)
        self.add_buttons(button("Close", "primary", self.accept))
        self.t = QTimer(self)
        self.t.timeout.connect(self.tick)
        self.t.start(80)

    def tick(self) -> None:
        self.view.set_image(frame_view(self.page.ctx.latest_frame(), self.combo.currentData()))


class ProjectorPopup(Popup):
    def __init__(self, page):
        super().__init__(page, "Projector", "Optoma ZH406ST - output display and remote control (PJLink / RS-232)",
                         "projector", 620)
        self.page, ctx = page, page.ctx
        s = ctx.settings
        f = QFormLayout()
        self.screen = QComboBox()
        self.screen.addItem("<Auto / Secondary Display>", "")
        for scr in QGuiApplication.screens():
            g = scr.geometry()
            self.screen.addItem(f"{scr.name()}  ({g.width()}x{g.height()})", scr.name())
        idx = self.screen.findData(s.get("projector_screen", ""))
        self.screen.setCurrentIndex(max(0, idx))
        self.rot = QComboBox()
        self.rot.addItems(["0", "90", "180", "270"])
        self.rot.setCurrentText(str(s.get("projector_rotation", 0)))
        self.fh, self.fv = QCheckBox("Mirror horizontally"), QCheckBox("Mirror vertically")
        self.fh.setChecked(bool(s.get("projector_flip_h")))
        self.fv.setChecked(bool(s.get("projector_flip_v")))
        self.host, self.port, self.serial = QLineEdit(s.get("pjlink_host", "")), QSpinBox(), \
            QLineEdit(s.get("projector_serial_port", ""))
        self.port.setRange(1, 65535)
        self.port.setValue(int(s.get("pjlink_port", 4352)))
        self.serial.setPlaceholderText("e.g. COM3 (optional)")
        for lab_, w in (("Output display", self.screen), ("Rotation", self.rot), ("", self.fh), ("", self.fv),
                        ("PJLink IP address", self.host), ("PJLink port", self.port), ("RS-232 port", self.serial)):
            f.addRow(lab_, w)
        self.body.addLayout(f)
        self.body.addWidget(label("Windows display scaling of the projector must be 100 %. The PJLink password "
                                  "(if any) is read from PROJECTOR_PJLINK_PASSWORD in .env.", "Muted", wrap=True))
        self.body.addLayout(hrow(button("Power on", None, lambda: self.cmd(lambda c: c.power_on()), ico="sun"),
                                 button("Power off", None, self.off, ico="moon"),
                                 button("Image mute", None, lambda: self.cmd(lambda c: c.av_mute(True))),
                                 button("Unmute", None, lambda: self.cmd(lambda c: c.av_mute(False))),
                                 button("Status", None, lambda: self.cmd(lambda c: c.info()), ico="info"), None))
        self.result = label("", wrap=True)
        self.body.addWidget(self.result)
        self.a = _Async()
        self.a.done.connect(self._done)
        self.add_buttons(button("Close", None, self.reject), button("Save", "primary", self.save, ico="save"))

    def client(self):
        if self.host.text().strip():
            return PJLinkClient(self.host.text().strip(), self.port.value(),
                                os.environ.get("PROJECTOR_PJLINK_PASSWORD", ""))
        if self.serial.text().strip():
            return OptomaSerial(self.serial.text().strip(),
                                commands=self.page.ctx.settings.get("projector_serial_commands") or {})
        return None

    def cmd(self, fn) -> None:
        c = self.client()
        if c is None:
            return error(self, "Enter the projector IP address (PJLink) or an RS-232 port.")
        self.result.setText("Sending…")
        self.a.run(lambda: fn(c))

    def off(self) -> None:
        if confirm(self, "Switch the projector off? Operators lose guidance until it is on again."):
            self.cmd(lambda c: c.power_off())

    def _done(self, text: str, level: str) -> None:
        self.result.setText(text)
        self.result.setStyleSheet(f"color: {C['ok'] if level == 'ok' else C['ng']}")
        self.page.ctx.audit("PROJECTOR_COMMAND", "projector", "", {"result": text[:200]})

    def save(self) -> None:
        try:
            self.page.ctx.save_settings({"projector_screen": self.screen.currentData(),
                                         "projector_rotation": int(self.rot.currentText()),
                                         "projector_flip_h": self.fh.isChecked(),
                                         "projector_flip_v": self.fv.isChecked(),
                                         "pjlink_host": self.host.text().strip(), "pjlink_port": self.port.value(),
                                         "projector_serial_port": self.serial.text().strip()})
        except PermissionDenied as exc:
            return error(self, str(exc))
        self.page.main.projector.place()
        self.accept()


class CameraPopup(Popup):
    def __init__(self, page):
        super().__init__(page, "3D sensor settings", "Intel RealSense D456 / D457 depth processing", "camera", 520)
        self.page = page
        cam = page.ctx.camera
        opts = {**(cam.options() if cam else {}), **(page.ctx.settings.get("camera_options") or {})}
        f = QFormLayout()
        self.preset = QComboBox()
        self.preset.addItems(list(PRESETS))
        self.preset.setCurrentText(opts.get("preset", "High density"))
        self.laser = QSpinBox()
        self.laser.setRange(0, 360)
        self.laser.setSuffix(" mW")
        self.laser.setValue(int(opts.get("laser_power", 150)))
        self.ae = QCheckBox("Auto exposure")
        self.ae.setChecked(bool(opts.get("auto_exposure", True)))
        self.fs, self.ft, self.fhole = QCheckBox("Spatial"), QCheckBox("Temporal"), QCheckBox("Hole filling")
        for cb, k in ((self.fs, "spatial"), (self.ft, "temporal"), (self.fhole, "hole_filling")):
            cb.setChecked(bool(opts.get(k, True)))
        info = cam.info() if cam else {}
        for k in ("name", "serial", "firmware", "usb", "resolution"):
            if info.get(k):
                f.addRow(label(k.title(), "Muted"), label(str(info[k])))
        f.addRow("Depth preset", self.preset)
        f.addRow("Laser power", self.laser)
        f.addRow("", self.ae)
        f.addRow("Filters", hrow(self.fs, self.ft, self.fhole, None))
        self.body.addLayout(f)
        self.add_buttons(button("Close", None, self.reject), button("Apply", "primary", self.apply, ico="save"))

    def apply(self) -> None:
        opts = {"preset": self.preset.currentText(), "laser_power": float(self.laser.value()),
                "auto_exposure": self.ae.isChecked(), "spatial": self.fs.isChecked(),
                "temporal": self.ft.isChecked(), "hole_filling": self.fhole.isChecked()}
        try:
            self.page.ctx.save_settings({"camera_options": opts})
        except PermissionDenied as exc:
            return error(self, str(exc))
        for k, v in opts.items():
            try:
                self.page.ctx.camera.set_option(k, v)
            except (KeyError, AttributeError):
                pass
        self.accept()


class ScannerPopup(Popup):
    def __init__(self, page):
        super().__init__(page, "Barcode / QR code scanner", "USB keyboard-wedge or serial (COM) scanner", "barcode",
                         520)
        self.page = page
        s = page.ctx.settings
        f = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(["KEYBOARD", "SERIAL"])
        self.mode.setCurrentText(s.get("scanner_mode", "KEYBOARD"))
        self.port, self.baud = QLineEdit(s.get("scanner_port", "")), QSpinBox()
        self.baud.setRange(1200, 921600)
        self.baud.setValue(int(s.get("scanner_baud", 9600)))
        f.addRow("Mode", self.mode)
        f.addRow("COM port", self.port)
        f.addRow("Baud rate", self.baud)
        self.body.addLayout(f)
        self.body.addWidget(label("Scan a code now to test - it appears below.", "Muted"))
        self.log = QListWidget()
        self.log.setFixedHeight(150)
        for code in page.scans[:30]:
            self.log.addItem(code)
        self.body.addWidget(self.log)
        page.main.scanned_any.connect(self._scan)
        self.add_buttons(button("Close", None, self.reject), button("Apply", "primary", self.apply, ico="save"))

    def _scan(self, code: str) -> None:
        self.log.insertItem(0, code)

    def apply(self) -> None:
        try:
            self.page.ctx.save_settings({"scanner_mode": self.mode.currentText(), "scanner_port": self.port.text(),
                                         "scanner_baud": self.baud.value()})
        except PermissionDenied as exc:
            return error(self, str(exc))
        self.page.main.setup_scanner()
        self.accept()


class DatabasePopup(Popup):
    def __init__(self, page):
        super().__init__(page, "Database", "PostgreSQL connection and storage status", "database", 560)
        ctx = page.ctx
        f = QFormLayout()
        ok = ctx.db.ping()
        ver = ctx.db.fetchall("SELECT version, name, applied_at FROM schema_version ORDER BY version") if ok else []
        counts = ctx.db.fetchone(
            "SELECT (SELECT count(*) FROM inspection_run) AS runs, (SELECT count(*) FROM inspection_step_result) "
            "AS results, (SELECT count(*) FROM audit_log) AS audit, pg_size_pretty(pg_database_size("
            "current_database())) AS size") if ok else {}
        rows = [("Connection", ctx.cfg.db.safe_repr()), ("Status", "connected" if ok else "unreachable"),
                ("Schema version", ", ".join(v["name"] for v in ver) or "-"),
                ("Inspections stored", str(counts.get("runs", "-"))), ("Step results", str(counts.get("results", "-"))),
                ("Audit entries", str(counts.get("audit", "-"))), ("Database size", str(counts.get("size", "-"))),
                ("Results waiting to be written", str(ctx.recorder.pending))]
        for k, v in rows:
            f.addRow(label(k, "Muted"), label(v, wrap=True))
        self.body.addLayout(f)
        self.body.addWidget(label("Back up daily with pg_dump -Fc and test a restore quarterly (see README).",
                                  "Muted", wrap=True))
        self.add_buttons(button("Close", "primary", self.accept))


# =============================================================================== page
class ConnectivityPage(Page):
    def __init__(self, ctx, main):
        super().__init__()
        self.ctx, self.main = ctx, main
        self.scans: list[str] = []
        self.cam_ok = False
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("Page")
        body.setAttribute(Qt.WA_StyledBackground, True)
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(8, 4, 8, 8)
        self.lay.setSpacing(6)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.props = PropertiesPanel(width=320)
        root.addWidget(self.props)
        self.cards: dict[str, DeviceCard] = {}

        tests = QMenu(self)
        tests.addAction("Test grid", lambda: main.projector.set_mode("VERIFY"))
        tests.addAction("All detection zones", lambda: main.projector.set_mode("TEST"))
        tests.addAction("Blank", lambda: main.projector.set_mode("BLANK"))
        tests.addAction("Normal guidance", lambda: main.projector.set_mode("RUN"))
        sections = [
            ("Displays", [("projector", "projector", "Projector",
                           [("Test", None, tests), ("Calibrate", lambda: main.show_page("calibration"), None),
                            ("Configure…", lambda: self._popup(ProjectorPopup), None)]),
                          ("screen", "monitor", "Operator screen", [("Identify", self.identify, None)])]),
            ("Sensors", [("depth", "camera", "3D sensor",
                          [("View stream", lambda: StreamPopup(self, "HEIGHT", "3D sensor").exec(), None),
                           ("Calibrate", lambda: main.show_page("calibration"), None),
                           ("Configure…", lambda: self._popup(CameraPopup), None)]),
                         ("rgb", "camera", "3D sensor (RGB)",
                          [("View stream", lambda: StreamPopup(self, "COLOR", "3D sensor (RGB)").exec(), None)]),
                         ("vision", "eye", "Vision camera",
                          [("View stream", lambda: StreamPopup(self, "VISION", "Vision camera").exec(), None)])]),
            ("Communication", [("scanner", "barcode", "Barcode / QR code scanner",
                                [("Message log", lambda: ScannerPopup(self).exec(), None),
                                 ("Configure…", lambda: self._popup(ScannerPopup), None)]),
                               ("database", "database", "Database",
                                [("Status…", lambda: DatabasePopup(self).exec(), None)])]),
        ]
        for title, cards in sections:
            h = QLabel(title)
            h.setObjectName("H1")
            h.setStyleSheet("font-size: 14pt;")
            self.lay.addWidget(h)
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            for i, (key, ico, ttl, actions) in enumerate(cards):
                c = DeviceCard(key, ico, ttl)
                for text, slot, menu in actions:
                    c.action(text, slot, menu)
                c.finish()
                c.selected.connect(self._select)
                grid.addWidget(c, 0, i)
                self.cards[key] = c
            grid.setColumnStretch(len(cards), 1)
            self.lay.addLayout(grid)
            self.lay.addSpacing(8)
        self.lay.addStretch(1)
        main.scanned_any.connect(self._on_scanned)
        main.worker.status.connect(self._cam_status)
        self.sel = "projector"
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_tick)
        self._refresh_timer.start(2500)

    def _on_tick(self) -> None:
        if self.isVisible():
            self.on_show()

    def _on_scanned(self, code: str) -> None:
        self.scans.insert(0, code)
        if self.isVisible():
            self.on_show()

    def _cam_status(self, st: dict) -> None:
        if st.get("camera") == "error":
            self.cam_ok = False
        elif st.get("camera") == "ok":
            self.cam_ok = True
        if self.isVisible():
            self.on_show()

    def _popup(self, cls) -> None:
        try:
            self.ctx.require(Perm.MANAGE_HARDWARE)
        except PermissionDenied as exc:
            return error(self, str(exc))
        cls(self).exec()
        self.on_show()

    def identify(self) -> None:
        from PySide6.QtWidgets import QLabel as _L
        scr = self.main.screen()
        lbl = _L(f"Operator screen\n{scr.name()}", None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"background: {C['primary']}; color: white; font-size: 36pt; padding: 40px;")
        lbl.adjustSize()
        g = scr.geometry()
        lbl.move(g.center() - lbl.rect().center())
        lbl.show()
        QTimer.singleShot(2000, lbl.close)
        self._identify = lbl

    def on_show(self) -> None:
        ctx, s = self.ctx, self.ctx.settings
        cal = ctx.calibration
        cal_txt = f"Calibrated {datetime.fromtimestamp(cal.created):%Y-%m-%d}" if cal else "Not calibrated"

        # 1. Projector (Optoma ZH406ST) - check for physical secondary display
        screens = QGuiApplication.screens()
        proj_screen_name = s.get("projector_screen") or ""
        matched_screen = None
        if proj_screen_name:
            matched_screen = next((scr for scr in screens if scr.name() == proj_screen_name), None)
        elif len(screens) > 1:
            matched_screen = screens[1]

        pjlink_host = s.get("pjlink_host", "").strip()
        pjlink_txt = f"PJLink {pjlink_host}" if pjlink_host else "PJLink not set"

        if matched_screen is not None:
            g = matched_screen.geometry()
            self.cards["projector"].set(
                f"Optoma ZH406ST · {matched_screen.name()} ({g.width()}x{g.height()})",
                f"{cal_txt} · {pjlink_txt}",
                "ok" if cal else "warn",
            )
        else:
            self.cards["projector"].set(
                "Optoma ZH406ST · Disconnected",
                f"No secondary display (HDMI/DP) · {pjlink_txt}",
                "error",
            )

        # 2. Operator touch screen
        scr = self.main.screen()
        g = scr.geometry()
        self.cards["screen"].set(f"{scr.name()} · {g.width()}x{g.height()}", "Touch screen for operators", "ok")

        # 3. 3D Sensor (Depth) & RGB Camera - distinguish simulation vs physical RealSense
        is_sim = getattr(ctx.cfg, "camera_backend", "realsense") == "sim"
        info = ctx.camera.info() if ctx.camera else {}
        fps = self.main.worker.fps

        if is_sim:
            self.cards["depth"].set(
                "Simulated 3D Sensor (Software)",
                f"CAMERA_BACKEND=sim · No physical hardware ({fps:.0f} FPS)",
                "sim",
            )
            self.cards["rgb"].set(
                "Simulated RGB Stream (Software)",
                f"Synthetic bench camera · {cal_txt}",
                "sim",
            )
        else:
            is_connected = self.cam_ok or self.main.worker.connected
            if is_connected:
                dev_name = info.get("name", "RealSense D456 / D457")
                serial = info.get("serial", "-")
                self.cards["depth"].set(
                    f"{dev_name} · {serial}",
                    f"{cal_txt} · {fps:.0f} FPS",
                    "ok" if cal else "warn",
                )
                self.cards["rgb"].set(
                    f"{serial} · colour aligned to depth",
                    cal_txt,
                    "ok" if cal else "warn",
                )
            else:
                self.cards["depth"].set(
                    "RealSense 3D Sensor · Disconnected",
                    "No RealSense device detected on USB 3.0",
                    "error",
                )
                self.cards["rgb"].set(
                    "RealSense RGB · Disconnected",
                    "Sensor offline",
                    "error",
                )

        # 4. Vision Camera (Optional 2D UVC)
        vi = ctx.cfg.vision_camera_index
        if vi >= 0:
            if self.main.worker.vision_cam is not None:
                self.cards["vision"].set(
                    f"UVC Camera (Index {vi}) · Connected",
                    "Calibrated" if cal and cal.H_vision2bench is not None else "Not calibrated",
                    "ok",
                )
            else:
                self.cards["vision"].set(
                    f"UVC Camera (Index {vi}) · Disconnected",
                    "Camera index unreachable",
                    "error",
                )
        else:
            self.cards["vision"].set(
                "Vision Camera · Not configured",
                "Set VISION_CAMERA_INDEX in .env to enable",
                "idle",
            )

        # 5. Scanner (Barcode / QR code)
        mode = s.get("scanner_mode", "KEYBOARD")
        if mode == "SERIAL":
            port = s.get("scanner_port", "").strip()
            port_found = False
            if port:
                try:
                    import serial.tools.list_ports
                    available = [p.device.upper() for p in serial.tools.list_ports.comports()]
                    port_found = port.upper() in available
                except Exception:
                    port_found = False
            if port_found:
                self.cards["scanner"].set(
                    f"Serial Scanner · {port}",
                    f"Last scan: {self.scans[0] if self.scans else 'None'}",
                    "ok",
                )
            else:
                self.cards["scanner"].set(
                    f"Serial Scanner · {port or 'Port not set'} (Not found)",
                    "Port unavailable · Check USB connection",
                    "error",
                )
        else:
            last_scan_txt = f"Last scan: {self.scans[0]}" if self.scans else "Awaiting scan input"
            self.cards["scanner"].set(
                "USB Keyboard Wedge (Listening)",
                f"{last_scan_txt} · Virtual input",
                "idle",
            )

        # 6. Database
        ok = ctx.db.ping()
        self.cards["database"].set(
            f"{ctx.cfg.db.host}:{ctx.cfg.db.port} / {ctx.cfg.db.name}",
            f"Schema {ctx.cfg.db.schema} · {ctx.recorder.pending} queued",
            "ok" if ok else "error",
        )
        self._select(self.sel)

    def _select(self, key: str) -> None:
        self.sel = key
        for k, c in self.cards.items():
            c.mark(k == key)
        p = self.props
        p.clear()
        c = self.cards[key]
        p.heading({"projector": "Projector", "screen": "Display",
                                                             "depth": "Sensor", "rgb": "Sensor", "vision": "Sensor",
                                                             "scanner": "Communication",
                                                             "database": "Communication"}[key])
        p.field("Name", QLineEdit({"projector": "Projector", "screen": "Operator screen", "depth": "3D sensor",
                                   "rgb": "3D sensor (RGB)", "vision": "Vision camera",
                                   "scanner": "Barcode / QR code scanner", "database": "Database"}[key])).setReadOnly(True)
        details = {"Device": c.sub.text(), "Status": c.line2.text()}
        if key in ("depth", "rgb"):
            details.update({k.title(): str(v) for k, v in (self.ctx.camera.info() if self.ctx.camera else {}).items()})
            p.heading("Local properties")
            ser = QComboBox()
            ser.addItem("<Automatic>" if not self.ctx.cfg.realsense_serial else self.ctx.cfg.realsense_serial)
            ser.setEnabled(False)
            p.field("Specified serial (REALSENSE_SERIAL in .env)", ser)
        if key == "projector" and self.ctx.calibration:
            details["Reprojection error"] = f"{self.ctx.calibration.rms_px:.2f} px"
            details["Camera resolution on bench"] = f"{self.ctx.calibration.mm_per_px():.2f} mm/px"
        for k, v in details.items():
            p.body.addWidget(label(k, "Faint"))
            p.body.addWidget(label(v, wrap=True))
        p.end()
