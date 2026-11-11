"""End-to-end test on the simulator + a real PostgreSQL database.

Requires env var VISTA_TEST_ENV pointing at a .env file for a TEST database (schema is dropped!).
Run:  python -m pytest tests -q   (or python tests/test_e2e_sim.py)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from vista.app_context import AppContext  # noqa: E402
from vista.core.config import load_config  # noqa: E402
from vista.db.database import Database  # noqa: E402
from vista.demo import seed_demo  # noqa: E402
from vista.engine.executor import Executor  # noqa: E402
from vista.hardware.simulated_camera import SimulatedCamera  # noqa: E402


class FakeWorker:
    def __init__(self):
        self.frame = None

    def latest(self):
        return self.frame


def build():
    default_env = Path(__file__).resolve().parents[1] / ".env"
    env = Path(os.environ.get("VISTA_TEST_ENV", default_env if default_env.exists() else "/tmp/test.env"))
    os.environ["DB_SCHEMA"] = "vista_test"
    cfg = load_config(env)
    assert cfg.db.schema == "vista_test"
    db = Database(cfg.db)
    db.open()
    db.execute("DROP SCHEMA IF EXISTS vista_test CASCADE")
    db.migrate()
    ctx = AppContext(cfg, db)
    ctx.create_first_admin("admin", "Admin", "Vista2026x")
    ctx.login("admin", "Vista2026x")
    return ctx


def test_full_flow():
    app = QCoreApplication.instance() or QCoreApplication([])
    ctx = build()
    cam = SimulatedCamera()
    cam.start()
    job_id = seed_demo(ctx, cam)
    warnings = ctx.load_engine(job_id)
    assert not warnings, warnings
    ctx.worker = FakeWorker()
    ex = Executor(ctx)
    msgs, dialogs = [], []
    ex.message.connect(lambda t, lvl: msgs.append((lvl, t)))
    ex.dialog_requested.connect(dialogs.append)
    job = ctx.jobs.get(job_id)
    assert job["status"] == "RELEASED"
    ex.load_job(job, ctx.jobs.variants(job_id), ctx.jobs.steps(job_id))

    def pump(sec=0.6):
        t_end = time.monotonic() + sec
        while time.monotonic() < t_end:
            f = cam.read()
            f.height_mm = ctx.calibration.height_map(f.depth_mm)
            ctx.worker.frame = f
            ex.on_states(ctx.engine.process(f))
            app.processEvents()

    def active():
        return [n.step["name"] for n in ex.active_leaves()]

    def run_once(barcode, gauge_answer):
        dialogs.clear()
        ex.start_run()
        pump(0.3)
        assert active() == ["Place Object JOB LOADING"], active()
        cam.set_present("JOB LOADING", True); pump()
        assert active() == ["Take Object BARCODE SCANNER"], active()
        cam.set_present("BARCODE SCANNER", False); pump()
        ex.on_barcode(barcode); pump(0.2)
        assert ex.variant and ex.variant["code"] == barcode[:2]
        cam.set_qc("BELLOW CLIP RH", "bad")                 # defect present before the checks start
        cam.set_present("BARCODE SCANNER", True); pump(1.0)
        assert active() == ["Check BELLOW CLIP RH"], active()   # LH passed, RH waits for rework
        cam.set_qc("BELLOW CLIP RH", "good"); pump(0.8)
        assert active() == ["Take Object P POINT GAUGE"], active()
        cam.set_present("P POINT GAUGE", False); pump()
        cam.set_hand((270, 215, 70, 50)); pump(0.8)
        cam.set_hand(None); pump(0.6)
        assert active() == ["Place Object P POINT GAUGE"], active()
        cam.set_present("P POINT GAUGE", True); pump()
        cam.set_present("THREAD RING GO GAUGE", False); pump()
        assert dialogs, "operator dialog expected"
        ex.answer_dialog(dialogs[-1]["node"], gauge_answer); pump(0.3)
        if gauge_answer == "OK":
            cam.set_present("THREAD RING GO GAUGE", True); pump()
            cam.set_present("JOB LOADING", False); pump()
        else:
            cam.set_present("THREAD RING GO GAUGE", True)
            cam.set_present("JOB LOADING", False); pump()
        return ex.last_result

    ctx.settings["auto_restart"] = False
    r1 = run_once("LH000123", "OK")
    assert r1["result"] == "PASS", (r1, msgs[-5:])
    assert r1["ng"] == 1
    ex._result_until = 0; pump(0.2)
    r2 = run_once("RH000456", "NOT OK")
    assert r2["result"] == "FAIL", (r2, msgs[-5:])
    ex._result_until = 0; pump(0.2)

    # duplicate barcode is blocked
    ex.start_run(); pump(0.2)
    cam.set_present("JOB LOADING", True); pump()
    cam.set_present("BARCODE SCANNER", False); pump()
    ex.on_barcode("LH000123"); pump(0.2)
    assert any("already PASSED" in m for _, m in msgs)
    assert active() == ["Wait for barcode"]
    ex.abort("test"); pump(0.2)
    cam.set_present("BARCODE SCANNER", True)
    cam.set_present("JOB LOADING", False)

    ctx.recorder.flush()
    runs = ctx.db.fetchall("SELECT barcode, result, variant_code, ng_count FROM inspection_run ORDER BY started_at")
    assert [r["result"] for r in runs] == ["PASS", "FAIL", "ABORTED"], runs
    assert runs[0]["variant_code"] == "LH" and runs[1]["variant_code"] == "RH"
    steps = ctx.runs.step_results(ctx.db.fetchone("SELECT id FROM inspection_run WHERE barcode='LH000123'")["id"])
    names = [s["step_name"] for s in steps]
    assert "Check BELLOW CLIP RH" in names and any(s["result"] == "NG" for s in steps)
    assert all(Path(s["evidence_path"]).exists() for s in steps if s["evidence_path"])
    ctx.shutdown()
    print("E2E OK:", runs)


if __name__ == "__main__":
    test_full_flow()
