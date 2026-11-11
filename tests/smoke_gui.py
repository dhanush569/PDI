"""Headless GUI smoke test: every page, key pop-ups and one simulated inspection driven through the UI.
QT_QPA_PLATFORM=offscreen VISTA_TEST_ENV=... python tests/smoke_gui.py [screenshot_dir]"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from vista.app_context import AppContext  # noqa: E402
from vista.core.config import load_config  # noqa: E402
from vista.db.database import Database  # noqa: E402
from vista.demo import seed_demo  # noqa: E402
from vista.hardware.simulated_camera import SimulatedCamera  # noqa: E402
from vista.ui.theme import apply_theme  # noqa: E402

shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
app = QApplication(sys.argv)
apply_theme(app)
os.environ["DB_SCHEMA"] = "vista_gui"
default_env = Path(__file__).resolve().parents[1] / ".env"
cfg = load_config(Path(os.environ.get("VISTA_TEST_ENV", default_env if default_env.exists() else "/tmp/test.env")))
db = Database(cfg.db)
db.open()
db.execute("DROP SCHEMA IF EXISTS vista_gui CASCADE")
db.migrate()
ctx = AppContext(cfg, db)
ctx.create_first_admin("admin", "Sanjay Kumar", "Vista2026x")
ctx.login("admin", "Vista2026x")
cam = SimulatedCamera()
cam.start()
job_id = seed_demo(ctx, cam)
from vista.ui.main_window import RIBBON, MainWindow  # noqa: E402

win = MainWindow(ctx, cam)
win.resize(1600, 950)
win.show()
win.maximize()
win.set_edit_job(job_id)


def pump(sec):
    t = time.monotonic() + sec
    while time.monotonic() < t:
        app.processEvents()
        time.sleep(0.01)


def shot(name, w=None):
    if shots:
        (w or win).grab().save(str(shots / f"{name}.png"))


def popup_shot(name, dlg):
    dlg.show()
    pump(0.4)
    shot(name, dlg)
    dlg.reject()


pump(1.5)
for _, items in RIBBON:
    for key, *_ in items:
        if key.startswith("@"):
            continue
        win.show_page(key)
        pump(0.7)
        shot(key)
        print("page ok:", key)

win.show_page("steps")
pump(0.3)
win.pages["steps"].select_step(next(iter(win.pages["steps"].items)))
pump(0.3)
shot("steps_selected")
win.show_page("detections")
dp = win.pages["detections"]
dp.select_detection(next(d["id"] for d in dp.rows if d["det_type"] == "QUALITY"))
pump(0.8)
shot("detections_quality")
dp.select_detection(next(d["id"] for d in dp.rows if d["name"] == "P POINT GAUGE"))
pump(0.8)
shot("detections_object")
dp.tabs.setCurrentIndex(2)
pump(0.4)
shot("detections_projector")
dp.tabs.setCurrentIndex(0)

from vista.ui.dialogs import LoginDialog  # noqa: E402
from vista.ui.dialogs_extra import AboutDialog, CommandPalette, ExportDialog  # noqa: E402
from vista.ui.pages.connectivity import DatabasePopup, ProjectorPopup  # noqa: E402

popup_shot("popup_login", LoginDialog(ctx))
popup_shot("popup_export", ExportDialog(win))
popup_shot("popup_projector", ProjectorPopup(win.pages["connectivity"]))
popup_shot("popup_database", DatabasePopup(win.pages["connectivity"]))
popup_shot("popup_about", AboutDialog(win))
pal = CommandPalette(win)
pal.edit.setText("check")
popup_shot("popup_palette", pal)
m = win.title.menu_buttons["File"].menu()
m.popup(win.title.menu_buttons["File"].mapToGlobal(win.title.menu_buttons["File"].rect().bottomLeft()))
pump(0.3)
shot("menu_file")
m.hide()

# ---- inspection through the operator page
win.show_page("operate")
op = win.pages["operate"]
op.select_job(job_id)
pump(0.5)
op.start()
pump(0.5)
cam.set_present("JOB LOADING", True); pump(1.0)
cam.set_present("BARCODE SCANNER", False); pump(0.8)
win._on_scan("LH555555"); pump(0.5)
cam.set_present("BARCODE SCANNER", True); pump(1.2)
cam.set_present("P POINT GAUGE", False); pump(0.8)
shot("operate_running")
cam.set_hand((270, 215, 70, 50)); pump(0.9)
cam.set_hand(None); pump(0.7)
cam.set_present("P POINT GAUGE", True); pump(0.8)
cam.set_present("THREAD RING GO GAUGE", False); pump(0.8)
shot("operate_dialog")
win.executor.answer_dialog(op._pending_dialog["node"], "OK"); pump(0.4)
cam.set_present("THREAD RING GO GAUGE", True); pump(0.8)
cam.set_present("JOB LOADING", False); pump(1.0)
print("result:", win.executor.last_result)
shot("operate_result")
assert win.executor.last_result["result"] == "PASS"
ctx.recorder.flush()
win.show_page("dashboard"); pump(0.6)
shot("dashboard_filled")
win.worker.stop()
print("GUI SMOKE OK")
os._exit(0)
