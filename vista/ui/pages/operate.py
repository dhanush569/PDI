"""Operator screen: steps | live instruction + camera | part, result and counters."""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from vista.core.security import Perm
from vista.engine.steps import operator_text
from vista.ui.dialogs import authorise
from vista.ui.icons import icon
from vista.ui.theme import C, font
from vista.ui.widgets.common import ImageView, Page, button, confirm, frame_view, label

GLYPH = {"PENDING": ("○", C["faint"]), "ACTIVE": ("◉", C["accent"]), "SKIPPED": ("–", C["faint"]),
         "FAILED": ("✗", C["ng"])}
RESULT_GLYPH = {"OK": ("✓", C["ok"]), "INFO": ("✓", C["ok"]), "NG": ("✗", C["ng"]), "FORCED": ("⚑", C["warn"]),
                None: ("✓", C["ok"])}


def _card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(6)
    if title:
        lay.addWidget(label(title, "H2"))
    return f, lay


class OperatePage(Page):
    def __init__(self, ctx, executor, main):
        super().__init__()
        self.ctx, self.ex, self.main = ctx, executor, main
        self._items: dict[int, QTreeWidgetItem] = {}
        self._built_for = None
        self._pending_dialog: dict | None = None
        self.counts = {"PASS": 0, "FAIL": 0}
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ---------------------------------------------------------------- action bar
        bar, bl = _card()
        bl.setContentsMargins(10, 8, 10, 8)
        row = QHBoxLayout()
        self.job_combo = QComboBox()
        self.job_combo.setMinimumWidth(340)
        self.job_combo.currentIndexChanged.connect(self._job_selected)
        self.variant_combo = QComboBox()
        self.variant_combo.setMinimumWidth(180)
        self.start_btn = button("Start inspection", "primary", self.start, "F5", ico="play")
        self.abort_btn = button("Abort", "danger", self.abort, "Shift+F5", ico="stop")
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("Type barcode if the scanner fails")
        self.manual.setFixedWidth(240)
        self.manual.addAction(icon("barcode", C["faint"], 16), QLineEdit.LeadingPosition)
        self.manual.returnPressed.connect(self._manual_barcode)
        self.force_btn = button("Supervisor override", None, self.force, "F8 - force the active step(s)", ico="key")
        for w in (label("Job", "Muted"), self.job_combo, label("Variant", "Muted"), self.variant_combo,
                  self.start_btn, self.abort_btn):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.manual)
        row.addWidget(self.force_btn)
        bl.addLayout(row)
        root.addWidget(bar)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)
        # ---------------------------------------------------------------- steps
        left, ll = _card("Steps")
        self.progress = label("", "Muted")
        ll.addWidget(self.progress)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setColumnWidth(0, 30)
        self.tree.setTreePosition(1)
        self.tree.setFont(font(10))
        self.tree.setStyleSheet("QTreeWidget { border: none; }")
        ll.addWidget(self.tree, 1)
        split.addWidget(left)
        # ---------------------------------------------------------------- centre
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        self.instruction = QLabel("Select a job to begin")
        self.instruction.setFont(font(22, display=True))
        self.instruction.setWordWrap(True)
        self.instruction.setMinimumHeight(92)
        self._style_instruction("idle")
        cl.addWidget(self.instruction)
        self.step_no = label("", "Muted")
        cl.addWidget(self.step_no)
        vf, vl = _card()
        vl.setContentsMargins(4, 4, 4, 4)
        self.view = ImageView(placeholder="Waiting for camera")
        vl.addWidget(self.view)
        cl.addWidget(vf, 1)
        self.choice_box = QFrame()
        self.choice_box.setObjectName("Panel")
        self.choice_lay = QHBoxLayout(self.choice_box)
        self.choice_lay.setContentsMargins(12, 8, 12, 8)
        cl.addWidget(self.choice_box)
        self.choice_box.hide()
        split.addWidget(center)
        # ---------------------------------------------------------------- right
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        part, pl = _card("Part")
        self.barcode_lbl = QLabel("-")
        self.barcode_lbl.setFont(font(19, display=True))
        self.barcode_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.variant_lbl = label("Variant -", "Muted")
        pl.addWidget(self.barcode_lbl)
        pl.addWidget(self.variant_lbl)
        trow = QHBoxLayout()
        clock = QLabel()
        clock.setPixmap(icon("clock", C["muted"], 18).pixmap(18, 18))
        trow.addWidget(clock)
        self.timer_lbl = QLabel("0.0 s")
        self.timer_lbl.setFont(font(22, display=True))
        trow.addWidget(self.timer_lbl)
        trow.addStretch(1)
        self.expected_lbl = label("", "Muted")
        trow.addWidget(self.expected_lbl)
        pl.addLayout(trow)
        rl.addWidget(part)
        self.result_lbl = QLabel("READY")
        self.result_lbl.setAlignment(Qt.AlignCenter)
        self.result_lbl.setFont(font(26, True, display=True))
        self.result_lbl.setMinimumHeight(96)
        self._style_result("READY")
        rl.addWidget(self.result_lbl)
        cnt = QHBoxLayout()
        self.c_pass, self.c_fail, self.c_rate = self._counter("Pass", C["ok"]), self._counter("Fail", C["ng"]), \
            self._counter("Pass rate", C["primary"])
        for w in (self.c_pass[0], self.c_fail[0], self.c_rate[0]):
            cnt.addWidget(w)
        rl.addLayout(cnt)
        msg, ml = _card("Messages")
        self.log = QListWidget()
        self.log.setStyleSheet("QListWidget { border: none; }")
        ml.addWidget(self.log)
        rl.addWidget(msg, 1)
        split.addWidget(right)
        split.setSizes([380, 820, 330])

        ex = self.ex
        ex.tree_changed.connect(self.refresh_tree)
        ex.message.connect(self.add_message)
        ex.dialog_requested.connect(self.show_choice)
        ex.supervisor_requested.connect(self.request_supervisor)
        ex.identity_changed.connect(self.show_identity)
        ex.run_finished.connect(self.run_finished)
        ex.run_started.connect(lambda _: self._style_result("RUN"))
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(200)
        self._restored = False

    def _counter(self, title: str, color: str):
        f = QFrame()
        f.setObjectName("Panel")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        v = QLabel("0")
        v.setFont(font(18, display=True))
        v.setStyleSheet(f"color: {color};")
        lay.addWidget(v)
        lay.addWidget(label(title, "Muted"))
        return f, v

    # ================================================================== lifecycle
    def on_show(self) -> None:
        self.reload_jobs()
        self.load_counts()
        can = bool(self.ctx.session and self.ctx.session.can(Perm.RUN_INSPECTION))
        self.start_btn.setEnabled(can)
        self.job_combo.setEnabled(can)
        if not self._restored and self.ex.job is None and self.ctx.settings.get("remember_last_job"):
            self._restored = True
            last = self.ctx.settings.get("last_run_job_id") or self.ctx.settings.get("last_job_id")
            if last and self.job_combo.findData(last) > 0:
                self.select_job(last)

    def reload_jobs(self) -> None:
        cur = self.ex.job["id"] if self.ex.job else None
        self.job_combo.blockSignals(True)
        self.job_combo.clear()
        auto = self.ctx.settings.get("job_select_mode") == "BARCODE_AUTO"
        self.job_combo.addItem("- scan a part to select the job -" if auto else "- select job -", None)
        for j in self.ctx.jobs.released():
            self.job_combo.addItem(icon("job", C["ink"], 16), f"{j['code']}  -  {j['name']}  (rev {j['revision']})",
                                   j["id"])
        self.job_combo.setCurrentIndex(max(0, self.job_combo.findData(cur) if cur else 0))
        self.job_combo.blockSignals(False)

    def select_job(self, job_id: int) -> None:
        idx = self.job_combo.findData(job_id)
        if idx < 0:
            self.reload_jobs()
            idx = self.job_combo.findData(job_id)
        if idx >= 0:
            self.job_combo.setCurrentIndex(idx)
            if self.ex.job is None or self.ex.job["id"] != job_id:
                self._job_selected()

    def load_counts(self) -> None:
        try:
            k = self.ctx.runs.kpis(datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0))
            self.counts = {"PASS": k["pass"] or 0, "FAIL": k["fail"] or 0}
        except Exception:
            pass
        self._show_counts()

    def _show_counts(self) -> None:
        tot = self.counts["PASS"] + self.counts["FAIL"]
        self.c_pass[1].setText(str(self.counts["PASS"]))
        self.c_fail[1].setText(str(self.counts["FAIL"]))
        self.c_rate[1].setText(f"{100 * self.counts['PASS'] / tot:.0f}%" if tot else "-")

    # ================================================================== run control
    def _job_selected(self) -> None:
        job_id = self.job_combo.currentData()
        if self.ex.mode == "RUNNING":
            if not confirm(self, "An inspection is running. Abort it and change the job?"):
                self.reload_jobs()
                return
        if job_id is None:
            self.ex.unload()
            self.ctx.load_engine(None)
            self.variant_combo.clear()
            self.expected_lbl.setText("")
            return
        job = self.ctx.jobs.get(job_id)
        variants = self.ctx.jobs.variants(job_id)
        warnings = self.ctx.load_engine(job_id)
        self.ex.load_job(job, variants, self.ctx.jobs.steps(job_id))
        self.variant_combo.clear()
        self.variant_combo.addItem("Auto (from barcode)" if variants else "-", None)
        for v in variants:
            if v.get("is_active", True):
                self.variant_combo.addItem(f"{v['code']} - {v['name']}", v["code"])
        self.expected_lbl.setText(f"expected {job.get('expected_cycle_s') or '-'} s")
        try:
            self.ctx.settings_repo.set("last_run_job_id", job_id, self.ctx.session.user_id if self.ctx.session else None)
            self.ctx.settings_repo.set("last_job_id", job_id, self.ctx.session.user_id if self.ctx.session else None)
            self.ctx.settings["last_run_job_id"] = job_id
            self.ctx.settings["last_job_id"] = job_id
        except Exception:
            pass
        self.ctx.audit("JOB_LOADED", "job", job_id, {"code": job["code"], "revision": job["revision"]})
        for w in warnings:
            self.add_message(f"Detection problem: {w}", "error")
        self.add_message(f"Job {job['code']} rev {job['revision']} loaded", "info")
        self.main._refresh_mode()

    def start(self) -> None:
        if self.ctx.calibration is None:
            self.add_message("Workstation is not calibrated - detections cannot be evaluated.", "error")
        self.ex.start_run(self.variant_combo.currentData())

    def abort(self) -> None:
        if self.ex.mode == "RUNNING" and confirm(self, "Abort the current inspection? It is recorded as ABORTED."):
            self.ex.abort("Aborted by operator")

    def force(self) -> None:
        leaves = self.ex.active_leaves()
        if not leaves:
            return
        names = ", ".join(n.step["name"] for n in leaves)
        s = authorise(self, self.ctx, Perm.OVERRIDE_STEP, f"Force the active step(s): {names}")
        if s:
            self.ex.force_active(s.user_id, s.username)

    def _manual_barcode(self) -> None:
        code = self.manual.text().strip()
        self.manual.clear()
        if code:
            self.ctx.audit("MANUAL_BARCODE", "barcode", code, {})
            self.main._on_scan(code)

    # ================================================================== executor feedback
    def refresh_tree(self) -> None:
        ex = self.ex
        if ex.root is None:
            self.tree.clear()
            self._items, self._built_for = {}, None
            self.instruction.setText("Select a job to begin")
            self._style_instruction("idle")
            self.progress.setText("")
            return
        if self._built_for is not ex.root:
            self.tree.clear()
            self._items = {}
            self._built_for = ex.root

            def add(node, parent_item):
                for c in node.children:
                    it = QTreeWidgetItem(["", f"{c.number} {c.step['name']}".strip()])
                    it.setFont(0, font(12, True))
                    if c.type == "NUMBERED":
                        it.setFont(1, font(10, True))
                    (parent_item.addChild(it) if parent_item else self.tree.addTopLevelItem(it))
                    self._items[id(c)] = it
                    add(c, it)
            add(ex.root, None)
            self.tree.expandAll()
        active_item, done, total = None, 0, 0
        for n in ex.root.walk():
            it = self._items.get(id(n))
            if it is None:
                continue
            if not n.is_container:
                total += 1
                done += n.state in ("DONE", "SKIPPED")
            g, col = RESULT_GLYPH.get(n.result, RESULT_GLYPH[None]) if n.state == "DONE" else \
                GLYPH.get(n.state, GLYPH["PENDING"])
            if n.state == "ACTIVE" and n.ng_recorded:
                g, col = "✗", C["ng"]
            it.setText(0, g)
            it.setForeground(0, QColor(col))
            it.setForeground(1, QColor(C["faint"] if n.state in ("SKIPPED", "PENDING") else C["text"]))
            it.setBackground(1, QColor(C["accent_soft"]) if n.state == "ACTIVE" and not n.is_container
                             else QColor(0, 0, 0, 0))
            if n.state == "ACTIVE" and not n.is_container and active_item is None:
                active_item = it
        self.progress.setText(f"{done} of {total} steps done" if ex.mode == "RUNNING" else "")
        if active_item is not None:
            self.tree.scrollToItem(active_item)
        self._update_instruction()

    def _update_instruction(self) -> None:
        ex = self.ex
        if ex.mode == "RUNNING":
            leaves = ex.active_leaves()
            texts = [operator_text(n.step) for n in leaves]
            ng = any(n.ng_recorded for n in leaves)
            more = f"\n+ {len(texts) - 4} more" if len(texts) > 4 else ""
            self.instruction.setText("\n".join(texts[:4]) + more if texts else "…")
            self._style_instruction("ng" if ng else "todo")
            num = ex.active_numbered()
            self.step_no.setText(f"Step {num.number} {num.step['name']}" if num else "")
            if not any(n.type == "OPERATOR_DIALOG" for n in leaves):
                self._hide_choice()
            if any(n.type == "WORK_INSTRUCTION" for n in leaves):
                self._show_confirm()
        elif ex.mode == "READY":
            self.instruction.setText("Press Start or scan a part to begin" if ex.job else "Select a job to begin")
            self._style_instruction("idle")
            self.step_no.setText("")
            self._hide_choice()
        elif ex.mode == "RESULT" and ex.last_result:
            self.instruction.setText(f"Inspection {ex.last_result['result']}")
            self._style_instruction("ok" if ex.last_result["result"] == "PASS" else "ng")

    def show_identity(self, ident: dict) -> None:
        self.barcode_lbl.setText(ident.get("barcode") or "-")
        v = ident.get("variant")
        self.variant_lbl.setText(f"Variant {v} - {ident.get('variant_name')}" if v else "Variant -")

    def show_choice(self, req: dict) -> None:
        self._pending_dialog = req
        self._clear_choice()
        q = QLabel(req["question"])
        q.setFont(font(15, display=True))
        self.choice_lay.addWidget(q)
        self.choice_lay.addStretch(1)
        for opt in req["options"]:
            ng = any(w in opt.upper() for w in ("NOT", "NG", "FAIL", "NO"))
            b = button(opt, "danger" if ng else "ok", lambda _=False, o=opt: self._answer(o))
            b.setProperty("kind", "danger" if ng else "ok")
            b.setStyleSheet("font-size: 14pt; padding: 10px 26px;")
            b.setMinimumWidth(170)
            self.choice_lay.addWidget(b)
        self.choice_box.show()

    def _show_confirm(self) -> None:
        if self.choice_box.isVisible():
            return
        self._clear_choice()
        self.choice_lay.addStretch(1)
        b = button("Confirm - done", "primary", self.ex.confirm_instruction, ico="check_circle")
        b.setStyleSheet("font-size: 13pt; padding: 10px 26px;")
        self.choice_lay.addWidget(b)
        self.choice_box.show()

    def _answer(self, option: str) -> None:
        req = self._pending_dialog
        self._hide_choice()
        if req:
            self.ex.answer_dialog(req["node"], option)

    def _clear_choice(self) -> None:
        while self.choice_lay.count():
            it = self.choice_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _hide_choice(self) -> None:
        self._pending_dialog = None
        self._clear_choice()
        self.choice_box.hide()

    def request_supervisor(self, req: dict) -> None:
        s = authorise(self, self.ctx, Perm.OVERRIDE_STEP, f"Supervisor decision needed: {req['reason']}")
        if s is None:
            self.add_message("Supervisor decision pending - rework the part or call the supervisor.", "warn")
            return
        accept = confirm(self, f"{req['reason']}\n\nAccept this result (the part continues)?\nNo = reject the part.",
                         "Supervisor decision")
        self.ex.supervisor_decision(req["node"], accept, s.user_id, s.username)

    def run_finished(self, res: dict) -> None:
        self._style_result(res["result"])
        if res["result"] in self.counts:
            self.counts[res["result"]] += 1
        self._show_counts()

    def add_message(self, text: str, level: str = "info") -> None:
        col = {"error": C["ng"], "warn": C["warn"], "ok": C["ok"]}.get(level, C["text"])
        ico = {"error": "x_circle", "warn": "alert", "ok": "check_circle"}.get(level, "info")
        it = QListWidgetItem(icon(ico, col, 16), f"{datetime.now():%H:%M:%S}  {text}")
        it.setForeground(QColor(col))
        self.log.insertItem(0, it)
        while self.log.count() > 200:
            self.log.takeItem(self.log.count() - 1)

    # ================================================================== visuals
    def _style_instruction(self, kind: str) -> None:
        bg, fg, border = {"todo": (C["accent"], "#1B2129", C["accent"]), "ng": (C["ng"], "white", C["ng"]),
                          "ok": (C["ok"], "white", C["ok"])}.get(kind, ("white", C["muted"], C["line"]))
        self.instruction.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; border: 1px solid {border}; "
                                       f"border-radius: 6px; padding: 12px 18px; }}")

    def _style_result(self, res: str) -> None:
        bg, fg = {"PASS": (C["ok"], "white"), "FAIL": (C["ng"], "white"), "ABORTED": (C["warn"], "white"),
                  "RUN": (C["primary_soft"], C["primary"])}.get(res, ("white", C["muted"]))
        self.result_lbl.setText({"RUN": "INSPECTING"}.get(res, res))
        self.result_lbl.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; border: 1px solid {C['line']}; "
                                      "border-radius: 8px; }")

    def _tick(self) -> None:
        if self.ex.mode == "RUNNING":
            el = time.monotonic() - self.ex.run_t0
            lim = float((self.ex.job or {}).get("expected_cycle_s") or 0)
            self.timer_lbl.setText(f"{el:.1f} s")
            self.timer_lbl.setStyleSheet(f"color: {C['warn'] if lim and el > lim else C['text']};")

    def on_frame(self, frame) -> None:
        if not self.isVisible():
            return
        self.view.set_image(frame_view(frame, "COLOR"))
        meta = self.ctx.engine.meta()
        states = self.main.last_states
        ovs = []
        for n in self.ex.active_leaves():
            det = meta.get(n.step.get("detection_id"))
            if not det or det["sensor"] == "VISION" or not det.get("model", {}).get("zone_px"):
                continue
            st = states.get(det["id"])
            col = C["ng"] if st and st.state in ("NG", "MISSING") else C["accent"]
            ovs.append({"poly": det["model"]["zone_px"], "color": col, "fill": 0.25, "label": det["name"]})
        self.view.set_overlays(ovs)
