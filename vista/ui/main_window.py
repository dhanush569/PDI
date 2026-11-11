"""Application shell: VS Code-style title bar, three-section ribbon, Arkite/VS Code status bar."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from vista import __version__
from vista.core.config import resource_dir
from vista.core.security import PermissionDenied, Perm
from vista.engine.executor import Executor
from vista.hardware.barcode_scanner import KeyboardWedgeFilter, SerialScanner
from vista.hardware.camera_worker import CameraWorker
from vista.ui.dialogs import ChangePasswordDialog, LoginDialog
from vista.ui.dialogs_extra import (AboutDialog, CommandPalette, ExportDialog, HelpDialog, MessageLogDialog,
                                    ShortcutsDialog)
from vista.ui.icons import icon
from vista.ui.pages.calibration import CalibrationPage
from vista.ui.pages.connectivity import ConnectivityPage
from vista.ui.pages.dashboard import DashboardPage
from vista.ui.pages.detections import DetectionsPage
from vista.ui.pages.hardware import HardwarePage
from vista.ui.pages.jobs import JobsPage
from vista.ui.pages.operate import OperatePage
from vista.ui.pages.processes import ProcessesPage
from vista.ui.pages.settings import SettingsPage
from vista.ui.pages.steps import StepsPage
from vista.ui.pages.users import UsersPage
from vista.ui.projector_window import ProjectorWindow
from vista.ui.theme import C
from vista.ui.widgets.common import confirm, error, info

log = logging.getLogger(__name__)

# section title, [(key, label, icon, permissions (any), shortcut)]
RIBBON = [
    ("Configure", [("dashboard", "Dashboard", "dashboard", (Perm.VIEW_REPORTS,), "Ctrl+1"),
                   ("jobs", "Jobs", "job", (Perm.EDIT_JOBS, Perm.RELEASE_JOBS), "Ctrl+2"),
                   ("steps", "Steps", "steps", (Perm.EDIT_JOBS, Perm.RELEASE_JOBS), "Ctrl+3"),
                   ("detections", "Detections", "detection", (Perm.TEACH_DETECTIONS,), "Ctrl+4"),
                   ("processes", "Processes", "process", (Perm.MANAGE_PROCESSES, Perm.EDIT_JOBS), "Ctrl+5"),
                   ("connectivity", "Connectivity", "connectivity", (Perm.MANAGE_HARDWARE,), "Ctrl+6")]),
    ("Production", [("operate", "Operate", "operate", (Perm.RUN_INSPECTION,), "Ctrl+7"),
                    ("calibration", "Calibration", "calibration", (Perm.CALIBRATE, Perm.MANAGE_HARDWARE), "Ctrl+8"),
                    ("hardware", "Hardware", "hardware", (Perm.MANAGE_HARDWARE,), "Ctrl+9")]),
    ("Administration", [("users", "Users", "users", (Perm.MANAGE_USERS,), None),
                        ("settings", "Settings", "settings", (Perm.MANAGE_SETTINGS,), None),
                        ("@help", "Help", "help", (), "F1"),
                        ("@about", "About", "info", (), None)]),
]


class _Activity(QObject):
    def __init__(self, cb):
        super().__init__()
        self.cb = cb

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            self.cb()
        return False


def _tb(ico: str, tip: str, slot, size: int = 17) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon(ico, C["ink"], size))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.clicked.connect(slot)
    return b


class TitleBar(QFrame):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setObjectName("TitleBar")
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(2)
        logo = QLabel()
        png = resource_dir() / "assets" / "rane_vista.png"
        logo.setPixmap(QPixmap(str(png)).scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                       if png.exists() else icon("operate", C["primary"], 20).pixmap(20, 20))
        logo.setFixedWidth(30)
        logo.setToolTip(f"RANE VISTA {__version__}")
        lay.addWidget(logo)
        self.menu_buttons = {}
        for name in ("File", "Edit", "View", "Run", "Tools", "Help"):
            b = QToolButton()
            b.setText(name)
            b.setPopupMode(QToolButton.InstantPopup)
            b.setMenu(QMenu(b))
            lay.addWidget(b)
            self.menu_buttons[name] = b
        sep = QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet(f"background: {C['line']};")
        lay.addSpacing(6)
        lay.addWidget(sep)
        lay.addSpacing(4)
        self.back = _tb("arrow_left", "Go back (Alt+Left)", win.go_back)
        self.fwd = _tb("arrow_right", "Go forward (Alt+Right)", win.go_forward)
        lay.addWidget(self.back)
        lay.addWidget(self.fwd)
        lay.addStretch(1)
        self.command = QPushButton()
        self.command.setObjectName("Command")
        self.command.setIcon(icon("search", C["muted"], 15))
        self.command.setFixedSize(470, 26)
        self.command.setCursor(Qt.PointingHandCursor)
        self.command.setToolTip("Search jobs, steps, detections and commands (Ctrl+P)")
        self.command.clicked.connect(win.open_palette)
        lay.addWidget(self.command)
        lay.addStretch(1)
        self.station = QLabel()
        self.station.setStyleSheet(f"color: {C['muted']}; padding-right: 10px;")
        lay.addWidget(self.station)
        self.user = QLabel()
        self.user.setStyleSheet("padding-right: 6px;")
        lay.addWidget(self.user)
        self.role = QLabel()
        self.role.setObjectName("RoleChip")
        lay.addWidget(self.role)
        lay.addSpacing(4)
        lay.addWidget(_tb("logout", "Sign out", win.sign_out))
        lay.addSpacing(10)
        for name, tip, slot in (("min", "Minimise", win.showMinimized), ("max", "Maximise", win.toggle_max),
                                ("close", "Close", win.close)):
            b = _tb(name, tip, slot, 16)
            b.setObjectName("WinClose" if name == "close" else "WinBtn")
            b.setStyleSheet("QToolButton { border-radius: 0; min-width: 44px; min-height: 36px; }"
                            + ("QToolButton:hover { background: #E81123; }" if name == "close" else ""))
            lay.addWidget(b)
            if name == "max":
                self.max_btn = b

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.win.toggle_max()


class Ribbon(QFrame):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.setObjectName("Ribbon")
        self.setFixedHeight(80)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 2)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QToolButton] = {}
        self.sections: list[tuple] = []
        for si, (title, items) in enumerate(RIBBON):
            sec = QWidget()
            sl = QVBoxLayout(sec)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(0)
            row = QHBoxLayout()
            row.setSpacing(2)
            keys = []
            for key, text, ico, _perms, sc in items:
                b = QToolButton()
                b.setObjectName("RibbonBtn")
                b.setText(text)
                b.setIcon(icon(ico, C["ink"], 26))
                b.setIconSize(QSize(26, 26))
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                b.setCursor(Qt.PointingHandCursor)
                b.setToolTip(f"{text} ({sc})" if sc else text)
                if key.startswith("@"):
                    b.clicked.connect(lambda _=False, k=key: win.ribbon_action(k))
                else:
                    b.setCheckable(True)
                    self.group.addButton(b)
                    b.clicked.connect(lambda _=False, k=key: win.show_page(k))
                row.addWidget(b)
                self.buttons[key] = b
                keys.append(key)
            sl.addLayout(row)
            cap = QLabel(title)
            cap.setObjectName("RibbonGroup")
            cap.setAlignment(Qt.AlignHCenter)
            sl.addWidget(cap)
            lay.addWidget(sec)
            sep = None
            if si < len(RIBBON) - 1:
                sep = QFrame()
                sep.setObjectName("RibbonSep")
                sep.setFrameShape(QFrame.VLine)
                lay.addWidget(sep)
            self.sections.append((sec, keys, sep))
        lay.addStretch(1)
        self.job_ctx = QLabel()
        self.job_ctx.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.job_ctx.setStyleSheet(f"color: {C['muted']}; padding-right: 8px;")
        lay.addWidget(self.job_ctx)


class StatusBar(QFrame):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.setObjectName("StatusBar")
        self.setFixedHeight(24)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.cam = QLabel("  Camera starting  ")
        self.cam.setObjectName("ChipWarn")
        self.cam.setFixedHeight(24)
        lay.addWidget(self.cam)
        self.mode = QLabel("Edit mode")
        self.cal = QLabel()
        self.db = QLabel()
        self.scan = QLabel("Scanner ready")
        for w in (self.mode, self.cal, self.db, self.scan):
            lay.addWidget(w)
        lay.addStretch(1)
        lay.addWidget(QLabel(f"RANE VISTA {__version__}"))
        self.msg = QPushButton("No errors or warnings")
        self.msg.setObjectName("ChipBtnOk")
        self.msg.setFixedHeight(24)
        self.msg.setMinimumWidth(230)
        self.msg.setCursor(Qt.PointingHandCursor)
        self.msg.clicked.connect(win.show_messages)
        lay.addWidget(self.msg)

    def set_messages(self, n_err: int, n_warn: int) -> None:
        if n_err:
            text, obj = f"{n_err} error(s), {n_warn} warning(s)", "ChipBtnNg"
        elif n_warn:
            text, obj = f"{n_warn} warning(s)", "ChipBtnWarn"
        else:
            text, obj = "No errors or warnings", "ChipBtnOk"
        self.msg.setText(text)
        if self.msg.objectName() != obj:
            self.msg.setObjectName(obj)
            self.msg.style().unpolish(self.msg)
            self.msg.style().polish(self.msg)


class MainWindow(QMainWindow):
    scanned_any = Signal(str)
    job_context_changed = Signal(object)      # job being edited on Jobs / Steps / Detections
    message_added = Signal(dict)

    def __init__(self, ctx, camera, vision_cam=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.ctx = ctx
        self.setWindowTitle(f"RANE VISTA - {ctx.cfg.station_code}")
        self.resize(1600, 950)
        self.setMinimumSize(1024, 640)
        self.last_states: dict = {}
        self.messages: list[dict] = []
        self.edit_job_id = ctx.settings.get("last_job_id")
        self._last_activity = time.monotonic()
        self._history: list[str] = []
        self._hpos = -1
        self._allowed: set[str] = set()
        self._maxed = False
        self._normal_geo = None
        self.serial_scanner: SerialScanner | None = None
        ctx.camera = camera
        for k, v in (ctx.settings.get("camera_options") or {}).items():
            try:
                camera.set_option(k, v)
            except Exception:
                pass
        self.executor = Executor(ctx)
        self.projector = ProjectorWindow(ctx, self.executor)
        self.worker = CameraWorker(ctx, camera, vision_cam)
        ctx.worker = self.worker

        central = QWidget()
        central.setMouseTracking(True)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(3, 3, 3, 3)
        outer.setSpacing(0)
        self.title = TitleBar(self)
        self.ribbon = Ribbon(self)
        outer.addWidget(self.title)
        outer.addWidget(self.ribbon)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.status = StatusBar(self)
        outer.addWidget(self.status)
        self.setStyleSheet(f"QMainWindow {{ background: {C['canvas']}; border: 1px solid #B9C1CC; }}")

        self.pages = {
            "dashboard": DashboardPage(ctx, self), "jobs": JobsPage(ctx, self), "steps": StepsPage(ctx, self),
            "detections": DetectionsPage(ctx, self.executor, self), "processes": ProcessesPage(ctx, self),
            "connectivity": ConnectivityPage(ctx, self), "operate": OperatePage(ctx, self.executor, self),
            "calibration": CalibrationPage(ctx, self), "hardware": HardwarePage(ctx, self),
            "users": UsersPage(ctx, self), "settings": SettingsPage(ctx, self)}
        for p in self.pages.values():
            self.stack.addWidget(p)
        self._current: str | None = None
        self._build_menus()

        self.worker.states_ready.connect(self._on_states)
        self.worker.frame_ready.connect(self._on_frame)
        self.worker.status.connect(self._on_status)
        self.executor.message.connect(lambda t, lvl: self.add_message(t, lvl, "Inspection"))
        self.executor.run_started.connect(lambda _: self._refresh_mode())
        self.executor.run_finished.connect(lambda _: self._refresh_mode())
        self.wedge = KeyboardWedgeFilter()
        self.wedge.scanned.connect(self._on_scan)
        QApplication.instance().installEventFilter(self.wedge)
        self._activity = _Activity(self._touch)
        QApplication.instance().installEventFilter(self._activity)
        self.setup_scanner()
        for seq, slot in (("Ctrl+P", self.open_palette), ("Alt+Left", self.go_back), ("Alt+Right", self.go_forward)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)
        self.apply_session()
        self.worker.start()
        self.projector.place()
        if ctx.calibration is None:
            self.add_message("Workstation not calibrated - run Calibration before teaching or inspecting.", "warn",
                             "Calibration")

    # ================================================================== menus
    def _act(self, menu: QMenu, text: str, slot, shortcut: str | None = None, ico: str | None = None,
             perm: tuple = ()) -> QAction:
        a = QAction(text, self)
        if ico:
            a.setIcon(icon(ico, C["ink"], 16))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.ApplicationShortcut)
        a.triggered.connect(slot)
        a.setData(perm)
        menu.addAction(a)
        self.addAction(a)
        self._actions.append(a)
        return a

    def _build_menus(self) -> None:
        self._actions: list[QAction] = []
        m = {k: b.menu() for k, b in self.title.menu_buttons.items()}
        f = m["File"]
        self._act(f, "New job…", lambda: self._page_call("jobs", "new_job"), "Ctrl+N", "plus", (Perm.EDIT_JOBS,))
        self._act(f, "Open job package…", self.import_package, "Ctrl+O", "open", (Perm.EDIT_JOBS,))
        self._act(f, "Save job package…", self.export_package, "Ctrl+S", "save", (Perm.EDIT_JOBS, Perm.RELEASE_JOBS))
        f.addSeparator()
        self._act(f, "Export inspection data…", self.export_data, "Ctrl+E", "export", (Perm.EXPORT_REPORTS,))
        f.addSeparator()
        self._act(f, "Change password…", lambda: ChangePasswordDialog(self.ctx).exec(), None, "key")
        self._act(f, "Sign out", self.sign_out, None, "logout")
        self._act(f, "Exit", self.close, "Alt+F4", "close")
        e = m["Edit"]
        self._act(e, "Find…", self.open_palette, None, "search")
        self._act(e, "New step…", lambda: self._page_call("steps", "add_step_menu"), "Ctrl+Shift+N", "plus",
                  (Perm.EDIT_JOBS,))
        self._act(e, "New detection…", lambda: self._page_call("detections", "new_menu"), "Ctrl+Shift+D",
                  "detection", (Perm.TEACH_DETECTIONS,))
        self._act(e, "Duplicate job…", lambda: self._page_call("jobs", "duplicate"), None, "copy", (Perm.EDIT_JOBS,))
        self._act(e, "Release job…", lambda: self._page_call("jobs", "release"), None, "release",
                  (Perm.RELEASE_JOBS,))
        v = m["View"]
        for _, items in RIBBON:
            for key, text, ico, perms, sc in items:
                if not key.startswith("@"):
                    self._act(v, text, lambda _=False, k=key: self.show_page(k), sc, ico, perms)
            v.addSeparator()
        self._act(v, "Full screen", self.toggle_full, "F11", "fit")
        r = m["Run"]
        self._act(r, "Start inspection", lambda: self._page_call("operate", "start"), "F5", "play",
                  (Perm.RUN_INSPECTION,))
        self._act(r, "Abort inspection", lambda: self._page_call("operate", "abort"), "Shift+F5", "stop",
                  (Perm.RUN_INSPECTION,))
        self._act(r, "Supervisor override", lambda: self._page_call("operate", "force"), "F8", "key",
                  (Perm.RUN_INSPECTION,))
        t = m["Tools"]
        self._act(t, "Calibration wizard", lambda: self.show_page("calibration"), None, "wizard", (Perm.CALIBRATE,))
        self._act(t, "Projector: test grid", lambda: self.projector.set_mode("VERIFY"), None, "grid",
                  (Perm.MANAGE_HARDWARE, Perm.CALIBRATE))
        self._act(t, "Projector: all zones", lambda: self.projector.set_mode("TEST"), None, "detection",
                  (Perm.MANAGE_HARDWARE, Perm.TEACH_DETECTIONS))
        self._act(t, "Projector: blank", lambda: self.projector.set_mode("BLANK"), None, "moon",
                  (Perm.MANAGE_HARDWARE,))
        self._act(t, "Projector: normal guidance", lambda: self.projector.set_mode("RUN"), None, "projector")
        t.addSeparator()
        self._act(t, "Purge old evidence…", self.purge_evidence, None, "trash", (Perm.MANAGE_SETTINGS,))
        h = m["Help"]
        self._act(h, "User guide", lambda: HelpDialog(self).exec(), "F1", "help")
        self._act(h, "Keyboard shortcuts", lambda: ShortcutsDialog(self, self._actions).exec(), "Ctrl+K", "info")
        self._act(h, "Message log", self.show_messages, None, "log")
        self._act(h, "About RANE VISTA", lambda: AboutDialog(self).exec(), None, "info")

    def _page_call(self, key: str, method: str) -> None:
        if key not in self._allowed:
            return
        self.show_page(key)
        fn = getattr(self.pages[key], method, None)
        if fn:
            fn()

    # ================================================================== session / navigation
    def apply_session(self) -> None:
        s = self.ctx.session
        self.title.user.setText(s.full_name if s else "")
        self.title.role.setText(s.role_code.title() if s else "")
        self.title.station.setText(self.ctx.cfg.station_code)
        allowed, first = set(), None
        for _, items in RIBBON:
            for key, _t, _i, perms, _sc in items:
                ok = (not perms) or bool(s and any(s.can(p) for p in perms))
                self.ribbon.buttons[key].setVisible(ok)
                if ok and not key.startswith("@"):
                    allowed.add(key)
                    first = first or key
        for sec, keys, sep in self.ribbon.sections:
            vis = any(not self.ribbon.buttons[k].isHidden() for k in keys)
            sec.setVisible(vis)
            if sep is not None:
                sep.setVisible(vis)
        for a in self._actions:
            perms = a.data() or ()
            a.setEnabled((not perms) or bool(s and any(s.can(p) for p in perms)))
        self._allowed = allowed
        if first is None:
            QMessageBox.warning(self, "RANE VISTA", "Your role has no access to any screen.")
            return
        start = "operate" if "operate" in allowed else first
        self._history, self._hpos = [], -1
        self.show_page(self._current if self._current in allowed else start)
        self._refresh_command()

    def show_page(self, key: str, record: bool = True) -> None:
        if key not in self._allowed:
            return
        if self._current and self._current != key:
            prev = self.pages[self._current]
            if hasattr(prev, "on_hide"):
                prev.on_hide()
        self._current = key
        self.ribbon.buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])
        if record:
            self._history = self._history[:self._hpos + 1] + [key]
            self._hpos = len(self._history) - 1
        self.title.back.setEnabled(self._hpos > 0)
        self.title.fwd.setEnabled(self._hpos < len(self._history) - 1)
        page = self.pages[key]
        if hasattr(page, "on_show"):
            try:
                page.on_show()
            except Exception as exc:
                log.exception("Page %s failed to load", key)
                error(self, f"Could not load {key}: {exc}")
        self._refresh_mode()

    def go_back(self) -> None:
        if self._hpos > 0:
            self._hpos -= 1
            self.show_page(self._history[self._hpos], record=False)

    def go_forward(self) -> None:
        if self._hpos < len(self._history) - 1:
            self._hpos += 1
            self.show_page(self._history[self._hpos], record=False)

    def ribbon_action(self, key: str) -> None:
        (HelpDialog if key == "@help" else AboutDialog)(self).exec()

    def set_edit_job(self, job_id) -> None:
        if job_id == self.edit_job_id:
            return
        self.edit_job_id = job_id
        try:
            self.ctx.settings_repo.set("last_job_id", job_id, self.ctx.session.user_id if self.ctx.session else None)
            self.ctx.settings["last_job_id"] = job_id
        except Exception:
            log.debug("Could not persist last job")
        self._refresh_command()
        self.job_context_changed.emit(job_id)

    def _refresh_command(self) -> None:
        job = self.ctx.jobs.get(self.edit_job_id) if self.edit_job_id else None
        if job is None:
            self.edit_job_id = None
        self.title.command.setText(f"  {job['code']}  ·  {job['name']}".replace("&", "&&") if job
                                   else "  Search jobs, steps, detections and commands")
        self.ribbon.job_ctx.setText(f"Editing job<br><b style='color:{C['text']}'>{job['code']}</b>&nbsp;"
                                    f"<span style='color:{C['muted']}'>rev {job['revision']} · "
                                    f"{job['status'].lower()}</span>" if job else "")

    def open_palette(self) -> None:
        pal = CommandPalette(self)
        g = self.title.command.mapToGlobal(QPoint(0, 0))
        pal.move(g.x() - 40, g.y() + self.title.command.height() + 2)
        pal.exec()

    def maximize(self) -> None:
        """Frameless windows maximised by Windows cover the taskbar - fill the available area instead."""
        if not self._maxed:
            self._normal_geo = self.geometry()
        scr = self.screen() or QApplication.primaryScreen()
        self.showNormal()
        self.setGeometry(scr.availableGeometry())
        self._maxed = True
        self.title.max_btn.setIcon(icon("restore", C["ink"], 16))

    def toggle_max(self) -> None:
        if self._maxed:
            self._maxed = False
            self.showNormal()
            if self._normal_geo is not None:
                self.setGeometry(self._normal_geo)
            self.title.max_btn.setIcon(icon("max", C["ink"], 16))
        else:
            self.maximize()

    def toggle_full(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def mousePressEvent(self, e):  # noqa: N802  (frameless resize at the border)
        pos, r, m = e.position().toPoint(), self.rect(), 5
        edges = Qt.Edges()
        if pos.x() <= m:
            edges |= Qt.LeftEdge
        if pos.x() >= r.width() - m:
            edges |= Qt.RightEdge
        if pos.y() <= m:
            edges |= Qt.TopEdge
        if pos.y() >= r.height() - m:
            edges |= Qt.BottomEdge
        if edges and not self._maxed and self.windowHandle():
            self.windowHandle().startSystemResize(edges)

    # ================================================================== session
    def sign_out(self) -> None:
        if self.executor.mode == "RUNNING" and not confirm(self, "An inspection is running. Abort it and sign out?"):
            return
        self.executor.abort("Operator signed out")
        self.lock()

    def lock(self) -> None:
        self.ctx.logout()
        self.hide()
        while True:
            if LoginDialog(self.ctx).exec() != QDialog.Accepted:
                self.close()
                return
            if getattr(self.ctx.session, "must_change_password", False):
                if ChangePasswordDialog(self.ctx, forced=True).exec() != QDialog.Accepted:
                    self.ctx.logout()
                    continue
            break
        self.apply_session()
        self.show()
        self.maximize()
        self._touch()

    def reset_idle_timer(self) -> None:
        self._touch()

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    # ================================================================== file actions
    def export_package(self) -> None:
        if not self.edit_job_id:
            return error(self, "Select a job first (Jobs page or Ctrl+P).")
        job = self.ctx.jobs.get(self.edit_job_id)
        path, _ = QFileDialog.getSaveFileName(self, "Save job package",
                                              str(self.ctx.cfg.export_dir / f"{job['code']}_rev{job['revision']}.vjob"),
                                              "RANE VISTA job (*.vjob)")
        if path:
            Path(path).write_bytes(self.ctx.catalog.export_package(job["id"]))
            self.ctx.audit("JOB_PACKAGE_EXPORTED", "job", job["id"], {"file": Path(path).name})
            info(self, f"Job {job['code']} saved to\n{path}")

    def import_package(self) -> None:
        try:
            s = self.ctx.require(Perm.EDIT_JOBS)
        except PermissionDenied as exc:
            return error(self, str(exc))
        path, _ = QFileDialog.getOpenFileName(self, "Open job package", str(self.ctx.cfg.export_dir),
                                              "RANE VISTA job (*.vjob)")
        if not path:
            return
        code, ok = QInputDialog.getText(self, "Open job package", "Job code on this station:",
                                        text=Path(path).stem.split("_rev")[0])
        if not ok or not code.strip():
            return
        try:
            jid, warnings = self.ctx.catalog.import_package(Path(path).read_bytes(), s.user_id, code.strip())
        except Exception as exc:
            return error(self, f"Import failed: {exc}")
        self.ctx.audit("JOB_PACKAGE_IMPORTED", "job", jid, {"file": Path(path).name, "warnings": warnings})
        info(self, "Job imported as DRAFT." + ("\n\n" + "\n".join(warnings) if warnings else ""))
        self.set_edit_job(jid)
        self.show_page("jobs")

    def export_data(self) -> None:
        try:
            self.ctx.require(Perm.EXPORT_REPORTS)
        except PermissionDenied as exc:
            return error(self, str(exc))
        ExportDialog(self).exec()

    def purge_evidence(self) -> None:
        try:
            self.ctx.require(Perm.MANAGE_SETTINGS)
        except PermissionDenied as exc:
            return error(self, str(exc))
        if confirm(self, f"Delete evidence images older than {self.ctx.settings['evidence_retention_days']} days?"):
            n = self.ctx.purge_old_evidence()
            self.ctx.audit("EVIDENCE_PURGED", "evidence", "", {"files": n})
            info(self, f"{n} image(s) deleted.")

    # ================================================================== messages
    def add_message(self, text: str, level: str = "info", source: str = "System") -> None:
        m = {"ts": datetime.now(), "text": text, "level": level, "source": source}
        if self.messages and self.messages[0]["text"] == text and \
                (m["ts"] - self.messages[0]["ts"]).total_seconds() < 5:
            return                                    # de-duplicate bursts (e.g. camera reconnect loop)
        self.messages.insert(0, m)
        del self.messages[500:]
        self.message_added.emit(m)
        recent = [x for x in self.messages[:100] if (datetime.now() - x["ts"]).total_seconds() < 3600]
        self.status.set_messages(sum(x["level"] == "error" for x in recent),
                                 sum(x["level"] == "warn" for x in recent))

    def show_messages(self) -> None:
        MessageLogDialog(self).exec()

    # ================================================================== hardware wiring
    def setup_scanner(self) -> None:
        s = self.ctx.settings
        if self.serial_scanner:
            self.serial_scanner.stop()
            self.serial_scanner = None
        self.wedge.enabled = s.get("scanner_mode", "KEYBOARD") == "KEYBOARD"
        if s.get("scanner_mode") == "SERIAL" and s.get("scanner_port"):
            self.serial_scanner = SerialScanner(s["scanner_port"], int(s.get("scanner_baud", 9600)))
            self.serial_scanner.scanned.connect(self._on_scan)
            self.serial_scanner.error.connect(lambda msg: self.add_message(msg, "error", "Scanner"))
            self.serial_scanner.start()

    def _on_scan(self, code: str) -> None:
        self.scanned_any.emit(code)
        self.status.scan.setText(f"Last scan {code}")
        ex = self.executor
        if self._current != "operate" and ex.mode != "RUNNING":
            return
        if ex.mode != "RUNNING" and self.ctx.settings.get("job_select_mode") == "BARCODE_AUTO":
            job = self.ctx.find_job_for_barcode(code)
            if job and (ex.job is None or ex.job["id"] != job["id"]):
                self.pages["operate"].select_job(job["id"])
            elif job is None and ex.job is None:
                self.add_message(f"No released job matches barcode {code}", "warn", "Job selection")
        ex.on_barcode(code)

    def _on_states(self, states: dict) -> None:
        self.last_states = states
        self.projector.on_states(states)
        self.executor.on_states(states)

    def _on_frame(self, frame) -> None:
        page = self.pages.get(self._current)
        if page is not None and hasattr(page, "on_frame"):
            page.on_frame(frame)

    def _on_status(self, st: dict) -> None:
        cam = self.status.cam
        if st.get("camera") == "error":
            cam.setText("  Camera offline  ")
            cam.setObjectName("ChipNg")
            cam.setToolTip(st.get("error", ""))
            self.add_message(st.get("error", "Camera error"), "error", "Camera")
        elif "fps" in st:
            vis = " · vision camera" if self.worker.vision_cam is not None else ""
            if getattr(self.ctx.cfg, "camera_backend", "realsense") == "sim":
                cam.setText(f"  Simulated camera ({st['fps']:.0f} FPS){vis}  ")
                cam.setObjectName("ChipWarn")
            else:
                cam.setText(f"  3D sensor {st['fps']:.0f} FPS{vis}  ")
                cam.setObjectName("ChipOk" if st["fps"] >= 10 else "ChipWarn")
        cam.style().unpolish(cam)
        cam.style().polish(cam)

    def _refresh_mode(self) -> None:
        ex = self.executor
        if ex.mode == "RUNNING":
            self.status.mode.setText(f"Run mode · inspecting {ex.job['code']}")
        elif ex.job is not None:
            self.status.mode.setText(f"Run mode · {ex.job['code']} ready")
        else:
            self.status.mode.setText("Edit mode")

    def _tick(self) -> None:
        cal = self.ctx.calibration
        self.status.cal.setText(f"Calibrated · {cal.rms_px:.2f} px" if cal else "Not calibrated")
        rec = self.ctx.recorder
        self.status.db.setText(f"Database offline · {rec.pending} queued" if rec.failures
                               else ("Database · writing" if rec.pending else "Database connected"))
        s = self.ctx.session
        limit = float(self.ctx.settings.get("idle_logout_min", 15)) * 60
        engineering = s is not None and any(s.can(p) for p in (Perm.EDIT_JOBS, Perm.TEACH_DETECTIONS,
                                                                Perm.MANAGE_USERS, Perm.MANAGE_SETTINGS))
        if engineering and self.executor.mode != "RUNNING" and time.monotonic() - self._last_activity > limit:
            self.ctx.audit("IDLE_SIGN_OUT", "user", s.user_id, {})
            self.lock()

    def closeEvent(self, e):  # noqa: N802
        if self.executor.mode == "RUNNING" and not confirm(self, "An inspection is running. Abort it and exit?"):
            e.ignore()
            return
        self.executor.abort("Application closed")
        self.ctx.audit("APP_CLOSED", "station", self.ctx.cfg.station_code, {})
        self.worker.stop()
        if self.serial_scanner:
            self.serial_scanner.stop()
        self.projector.close()
        self.ctx.shutdown()
        e.accept()
