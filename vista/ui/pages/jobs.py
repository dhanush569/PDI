"""Jobs page (Arkite layout): job cards on the left, Properties on the right."""
from __future__ import annotations

import csv
import io
import itertools
import re

import cv2
import numpy as np
from PySide6.QtCore import QSize, Qt, QTime, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QPlainTextEdit, QScrollArea, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout,
                               QWidget)

from vista.core.security import PermissionDenied, Perm
from vista.engine.steps import STEP_SPECS
from vista.ui.dialogs_extra import Popup
from vista.ui.icons import icon
from vista.ui.theme import C
from vista.ui.widgets.common import (Page, PropertiesPanel, SearchBox, button, chip, confirm, error, hrow, info,
                                     label, tool)

CODE_RE = re.compile(r"^[A-Za-z0-9_\-]{2,64}$")
STATUS_LEVEL = {"DRAFT": "warn", "RELEASED": "ok", "ARCHIVED": "idle"}


class OptionsPopup(Popup):
    """Arkite Job Options Configuration (Section 1.4).
    Configure product options (e.g. Fuel Type) and all possible values (e.g. Petrol, Diesel, Electric)."""
    def __init__(self, parent, options: list[dict], editable: bool):
        super().__init__(parent, "Job Options", "Define configurable product options and their possible values.",
                         "variants", 680)
        self.options = [dict(o) for o in options]
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Option Name", "Values (comma-separated)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(220)
        for opt in self.options:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(opt.get("name", "")))
            vals = opt.get("values", [])
            val_str = ", ".join(vals) if isinstance(vals, list) else str(vals)
            self.table.setItem(r, 1, QTableWidgetItem(val_str))
        self.body.addWidget(self.table)
        self.err = label("")
        self.err.setStyleSheet(f"color: {C['ng']}")
        self.body.addWidget(self.err)

        add = button("Add Option", None, self._add_option, ico="plus")
        rem = button("Remove", None, lambda: self.table.removeRow(self.table.currentRow()), ico="trash")
        save = button("Save Options", "primary", self.ok)
        for b in (add, rem, save):
            b.setEnabled(editable)
        self.footer.insertWidget(0, add)
        self.footer.insertWidget(1, rem)
        self.add_buttons(button("Cancel", None, self.reject), save)

    def _add_option(self) -> None:
        name, ok = QInputDialog.getText(self, "New Option", "Option Name (e.g. 'Fuel Type', 'Color'):")
        if not ok or not name.strip():
            return
        vals, ok = QInputDialog.getText(self, "Option Values", "Values (comma-separated, e.g. 'Petrol, Diesel, Electric'):")
        if not ok:
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name.strip()))
        self.table.setItem(r, 1, QTableWidgetItem(vals.strip()))

    def ok(self) -> None:
        out = []
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).text().strip() if self.table.item(r, 0) else ""
            vals_str = self.table.item(r, 1).text().strip() if self.table.item(r, 1) else ""
            if not name:
                continue
            vals = [v.strip() for v in vals_str.split(",") if v.strip()]
            out.append({"name": name, "values": vals})
        self.result_options = out
        self.accept()


class VariantsPopup(Popup):
    def __init__(self, parent, variants: list[dict], options: list[dict], editable: bool, default_cycle_s: int = 60):
        super().__init__(parent, "Job variants", "Configure variants, generate from options, or import/export CSV.",
                         "variants", 840)
        self.options = options or []
        self.default_cycle_s = default_cycle_s
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Code", "Name", "Part number", "Cycle Time (s)", "Barcode pattern", "Active"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(240)
        for v in variants:
            self._add(v)
        self.body.addWidget(self.table)
        self.err = label("")
        self.err.setStyleSheet(f"color: {C['ng']}")
        self.body.addWidget(self.err)

        add = button("Add variant", None, lambda: self._add({}), ico="plus")
        gen_all = button("Generate all", None, self.generate_all, ico="variants")
        exp_csv = button("Export CSV", None, self.export_csv, ico="save")
        imp_csv = button("Import CSV", None, self.import_csv, ico="open")
        rem = button("Remove", None, lambda: self.table.removeRow(self.table.currentRow()), ico="trash")
        save = button("Save variants", "primary", self.ok)
        for b in (add, gen_all, exp_csv, imp_csv, rem, save):
            b.setEnabled(editable)
        self.footer.insertWidget(0, add)
        self.footer.insertWidget(1, gen_all)
        self.footer.insertWidget(2, exp_csv)
        self.footer.insertWidget(3, imp_csv)
        self.footer.insertWidget(4, rem)
        self.add_buttons(button("Cancel", None, self.reject), save)

    def _add(self, v: dict) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        code = v.get("code") or ""
        name = v.get("name") or code
        pn = v.get("part_number") or ""
        cycle = str(v.get("expected_cycle_s") or "")
        regex = v.get("barcode_regex") or ""
        for c, val in enumerate((code, name, pn, cycle, regex)):
            self.table.setItem(r, c, QTableWidgetItem(val))
        chk = QTableWidgetItem()
        chk.setCheckState(Qt.Checked if v.get("is_active", True) else Qt.Unchecked)
        self.table.setItem(r, 5, chk)
        self.table.resizeColumnsToContents()

    def generate_all(self) -> None:
        """Section 1.5.5: Click 'Generate all' to auto-create variants for all possible combinations."""
        if not self.options or not all(o.get("values") for o in self.options):
            return self.err.setText("Define Job Options and their possible values first to generate variants.")
        self.err.setText("")
        opt_val_lists = [o["values"] for o in self.options]
        combos = list(itertools.product(*opt_val_lists))
        existing_codes = set()
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.text().strip():
                existing_codes.add(it.text().strip().upper())
        added = 0
        for combo in combos:
            code = "-".join(str(c).upper().replace(" ", "_") for c in combo)[:32]
            if code in existing_codes:
                continue
            name = " / ".join(str(c) for c in combo)
            self._add({"code": code, "name": name, "part_number": "", "expected_cycle_s": self.default_cycle_s, "is_active": True})
            existing_codes.add(code)
            added += 1
        self.err.setText(f"Generated {added} new variants from options.")

    def export_csv(self) -> None:
        """Section 1.6: Export to CSV (Code, Name, Part Number, Expected Cycle Time, Barcode Pattern, Active)."""
        path, _ = QFileDialog.getSaveFileName(self, "Export Variants to CSV", "variants.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Variant Code", "Variant Name", "Part Number", "Expected Cycle Time", "Barcode Pattern", "Active"])
                for r in range(self.table.rowCount()):
                    row = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else "") for c in range(5)]
                    active = "1" if self.table.item(r, 5).checkState() == Qt.Checked else "0"
                    writer.writerow(row + [active])
            self.err.setText(f"Exported {self.table.rowCount()} variants to {path}.")
        except Exception as exc:
            self.err.setText(f"Export error: {exc}")

    def import_csv(self) -> None:
        """Section 1.6: Import from CSV with auto-detected separators."""
        path, _ = QFileDialog.getOpenFileName(self, "Import Variants from CSV", "", "CSV / Text Files (*.csv *.txt)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                first_line = f.readline()
                sep = None
                if first_line.startswith("sep="):
                    sep = first_line.split("=", 1)[1].strip()
                    lines = f.readlines()
                else:
                    lines = [first_line] + f.readlines()
                text = "".join(lines)
                if not sep:
                    for candidate in [",", ";", "\t", "|", ":"]:
                        if candidate in lines[0]:
                            sep = candidate
                            break
                    sep = sep or ","
                reader = csv.reader(io.StringIO(text), delimiter=sep)
                rows = list(reader)
                if not rows:
                    return self.err.setText("CSV file is empty.")
                start_i = 1 if any("code" in str(col).lower() for col in rows[0]) else 0
                existing_rows = {}
                for r in range(self.table.rowCount()):
                    it = self.table.item(r, 0)
                    if it and it.text().strip():
                        existing_rows[it.text().strip().upper()] = r
                count = 0
                for row_data in rows[start_i:]:
                    if not row_data or not row_data[0].strip():
                        continue
                    code = row_data[0].strip()
                    name = row_data[1].strip() if len(row_data) > 1 else code
                    pn = row_data[2].strip() if len(row_data) > 2 else ""
                    cycle = row_data[3].strip() if len(row_data) > 3 else ""
                    regex = row_data[4].strip() if len(row_data) > 4 else ""
                    active = row_data[5].strip() not in ("0", "false", "False", "no", "No") if len(row_data) > 5 else True
                    if code.upper() in existing_rows:
                        r = existing_rows[code.upper()]
                        self.table.setItem(r, 1, QTableWidgetItem(name))
                        self.table.setItem(r, 2, QTableWidgetItem(pn))
                        self.table.setItem(r, 3, QTableWidgetItem(cycle))
                        self.table.setItem(r, 4, QTableWidgetItem(regex))
                        self.table.item(r, 5).setCheckState(Qt.Checked if active else Qt.Unchecked)
                    else:
                        self._add({"code": code, "name": name, "part_number": pn, "expected_cycle_s": cycle, "barcode_regex": regex, "is_active": active})
                        existing_rows[code.upper()] = self.table.rowCount() - 1
                    count += 1
                self.err.setText(f"Imported {count} variants from CSV.")
        except Exception as exc:
            self.err.setText(f"Import error: {exc}")

    def ok(self) -> None:
        out, codes = [], set()
        for r in range(self.table.rowCount()):
            g = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else "") for c in range(5)]
            if not g[0]:
                continue
            if not CODE_RE.match(g[0]) or g[0] in codes:
                return self.err.setText(f"Variant code '{g[0]}' is invalid or duplicated.")
            if g[4]:
                try:
                    re.compile(g[4])
                except re.error as exc:
                    return self.err.setText(f"Barcode pattern of {g[0]} is invalid: {exc}")
            codes.add(g[0])
            out.append({"code": g[0], "name": g[1] or g[0], "part_number": g[2],
                        "barcode_regex": g[4], "is_active": self.table.item(r, 5).checkState() == Qt.Checked})
        self.result_variants = out
        self.accept()


class JobCard(QFrame):
    clicked = Signal(int)
    double_clicked = Signal(int)
    variants_clicked = Signal(int)
    delete_clicked = Signal(int)

    def __init__(self, s: dict, thumb: QPixmap | None):
        super().__init__()
        self.job_id = s["id"]
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(76)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 12, 8)
        grip = QLabel("⠿")
        grip.setStyleSheet(f"color: {C['faint']}; font-size: 12pt;")
        lay.addWidget(grip)
        img = QLabel()
        img.setFixedSize(56, 56)
        img.setAlignment(Qt.AlignCenter)
        if thumb is not None:
            img.setPixmap(thumb.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            img.setPixmap(icon("job", C["ink"], 34).pixmap(34, 34))
        img.setStyleSheet("background: #F1F4F8; border-radius: 6px;")
        lay.addWidget(img)
        col = QVBoxLayout()
        col.setSpacing(2)
        top = QHBoxLayout()
        name = QLabel(f"<b>{s['code']}</b>&nbsp;&nbsp;<span style='color:{C['muted']}'>{s['name']}</span>")
        top.addWidget(name)
        top.addWidget(chip(f"{s['status'].title()} · rev {s['revision']}", STATUS_LEVEL.get(s["status"], "idle")))
        if s["untaught"]:
            top.addWidget(chip(f"{s['untaught']} untaught", "ng"))
        top.addStretch(1)
        col.addLayout(top)
        bottom = QHBoxLayout()
        vlink = QLabel(f"<a href='#' style='color:{C['primary']}'>{s['variants']} variants</a>")
        vlink.linkActivated.connect(lambda _: self.variants_clicked.emit(self.job_id))
        bottom.addWidget(vlink)
        last = s["last_run"].astimezone().strftime("%d-%m-%Y %H:%M") if s["last_run"] else "never"
        meta = QLabel(f"{s['steps']} steps · {s['detections']} detections · cycle {s['expected_cycle_s']} s · "
                      f"last run {last}")
        meta.setObjectName("Muted")
        bottom.addSpacing(10)
        bottom.addWidget(meta)
        bottom.addStretch(1)
        col.addLayout(bottom)
        lay.addLayout(col, 1)
        self.del_btn = tool("archive", "Archive job", lambda: self.delete_clicked.emit(self.job_id))
        lay.addWidget(self.del_btn, 0, Qt.AlignTop)

    def set_selected(self, on: bool) -> None:
        self.setObjectName("CardSel" if on else "Card")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):  # noqa: N802
        self.clicked.emit(self.job_id)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.double_clicked.emit(self.job_id)


class JobsPage(Page):
    def __init__(self, ctx, main):
        super().__init__()
        self.ctx, self.main = ctx, main
        self.cards: dict[int, JobCard] = {}
        self.job: dict | None = None
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        left = QFrame()
        left.setObjectName("Panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 8, 8)
        self.b_new = button("New Job", "primary", self.new_job, ico="plus")
        self.t_dup = tool("copy", "Duplicate job", self.duplicate)
        self.t_var = tool("variants", "Variants", lambda: self.edit_variants(self.job["id"]) if self.job else None)
        self.t_rel = tool("release", "Release revision", self.release)
        self.t_steps = tool("steps", "Open steps (double-click a job)", lambda: main.show_page("steps"))
        self.t_open = tool("open", "Open job package (Ctrl+O)", main.import_package)
        self.t_save = tool("save", "Save job package (Ctrl+S)", main.export_package)
        self.search = SearchBox("Search for jobs")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self.filter)
        self.show_arch = QCheckBox("Archived")
        self.show_arch.toggled.connect(self.reload)
        ll.addLayout(hrow(self.b_new, self.t_dup, self.t_var, self.t_rel, self.t_steps, self.t_open, self.t_save,
                          None, self.show_arch, self.search))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.list_w = QWidget()
        self.list_w.setStyleSheet("background: white;")
        self.list_lay = QVBoxLayout(self.list_w)
        self.list_lay.setContentsMargins(2, 4, 2, 4)
        self.list_lay.setSpacing(6)
        self.scroll.setWidget(self.list_w)
        ll.addWidget(self.scroll, 1)
        root.addWidget(left, 1)
        self.props = PropertiesPanel()
        root.addWidget(self.props)
        main.job_context_changed.connect(lambda jid: self._select(jid, emit=False) if self.isVisible() else None)

    # ================================================================== list
    def can_edit(self) -> bool:
        return bool(self.ctx.session and self.ctx.session.can(Perm.EDIT_JOBS))

    def on_show(self) -> None:
        self.reload()

    def reload(self) -> None:
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.cards = {}
        self.summaries = self.ctx.catalog.summaries(self.show_arch.isChecked())
        for s in self.summaries:
            thumb = None
            if s["has_image"]:
                data = self.ctx.catalog.image(s["id"])
                if data:
                    pm = QPixmap()
                    pm.loadFromData(data)
                    thumb = pm
            card = JobCard(s, thumb)
            card.clicked.connect(self._select)
            card.double_clicked.connect(lambda jid: (self._select(jid), self.main.show_page("steps")))
            card.variants_clicked.connect(self.edit_variants)
            card.delete_clicked.connect(self.archive)
            card.del_btn.setEnabled(self.can_edit())
            self.list_lay.addWidget(card)
            self.cards[s["id"]] = card
        if not self.summaries:
            empty = label("No jobs yet. Create one with New Job, or open a job package (Ctrl+O).", "Muted")
            empty.setAlignment(Qt.AlignCenter)
            self.list_lay.addWidget(empty)
        self.list_lay.addStretch(1)
        edit = self.can_edit()
        for w in (self.b_new, self.t_dup, self.t_open):
            w.setEnabled(edit)
        self.t_rel.setEnabled(bool(self.ctx.session and self.ctx.session.can(Perm.RELEASE_JOBS)))
        target = self.main.edit_job_id if self.main.edit_job_id in self.cards else (
            self.summaries[0]["id"] if self.summaries else None)
        self._select(target)
        self.filter(self.search.text())

    def filter(self, text: str) -> None:
        t = text.lower().strip()
        for s in self.summaries:
            self.cards[s["id"]].setVisible(not t or t in s["code"].lower() or t in s["name"].lower())

    def _select(self, job_id, emit: bool = True) -> None:
        for jid, c in self.cards.items():
            c.set_selected(jid == job_id)
        self.job = self.ctx.jobs.get(job_id) if job_id else None
        if emit and job_id:
            self.main.set_edit_job(job_id)
        self._properties()

    # ================================================================== properties
    def _properties(self) -> None:
        p = self.props
        p.clear()
        j = self.job
        if j is None:
            p.heading("Job", "Select a job to see its properties.")
            p.end()
            return
        edit = self.can_edit()
        p.heading("Job", f"ID {j['id']} · {j['status'].title()} · revision {j['revision']}")
        name_row = QWidget()
        nl = QHBoxLayout(name_row)
        nl.setContentsMargins(0, 0, 0, 0)
        self.f_name = QLineEdit(j["name"])
        nl.addWidget(self.f_name, 1)
        comment_btn = tool("dialog", "Add internal notes / comments", self._edit_notes)
        nl.addWidget(comment_btn)
        p.field("Name", name_row)
        img_row = QWidget()
        il = QHBoxLayout(img_row)
        il.setContentsMargins(0, 0, 0, 0)
        self.img_lbl = QLabel()
        self.img_lbl.setFixedSize(QSize(120, 72))
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setStyleSheet(f"border: 1px solid {C['line']}; border-radius: 4px; background: #F7F8FA;")
        data = self.ctx.catalog.image(j["id"])
        if data:
            pm = QPixmap()
            pm.loadFromData(data)
            self.img_lbl.setPixmap(pm.scaled(118, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.img_lbl.setText("No image")
        il.addWidget(self.img_lbl)
        il.addWidget(tool("open", "Choose image…", self.pick_image))
        il.addWidget(tool("close", "Remove image", self.clear_image))
        il.addStretch(1)
        p.field("Image", img_row)
        self.f_pid = p.field("Product identifier", QLineEdit(j.get("product_identifier") or ""))
        self.f_pn = p.field("Part number", QLineEdit(j["part_number"] or ""))
        self.f_cycle = QTimeEdit()
        self.f_cycle.setDisplayFormat("HH:mm:ss")
        secs = int(j.get("expected_cycle_s") or 60)
        self.f_cycle.setTime(QTime(secs // 3600, (secs % 3600) // 60, secs % 60))
        p.field("Expected cycle time", self.f_cycle)
        self.f_desc = QPlainTextEdit(j["description"] or "")
        self.f_desc.setFixedHeight(60)
        p.field("Description", self.f_desc)

        p.heading("Options")
        settings = dict(j.get("settings") or {})
        opts = settings.get("job_options") or []
        opt_row = QWidget()
        ol = QHBoxLayout(opt_row)
        ol.setContentsMargins(0, 0, 0, 0)
        if opts:
            for o in opts[:4]:
                cnt = len(o.get("values", []))
                ol.addWidget(chip(f"{o['name']} ({cnt})", "info"))
        else:
            ol.addWidget(label("No options defined", "Muted"))
        ol.addStretch(1)
        ol.addWidget(button("Configure options", None, lambda: self.edit_options(j["id"]), ico="variants"))
        p.body.addWidget(opt_row)

        p.heading("Variants")
        vs = self.ctx.jobs.variants(j["id"])
        vrow = QWidget()
        vl = QHBoxLayout(vrow)
        vl.setContentsMargins(0, 0, 0, 0)
        for v in vs[:6]:
            vl.addWidget(chip(v["code"], "info" if v["is_active"] else "idle"))
        vl.addStretch(1)
        vl.addWidget(button("Edit variants", None, lambda: self.edit_variants(j["id"]), ico="variants"))
        p.body.addWidget(vrow)
        n_steps = len(self.ctx.jobs.steps(j["id"]))
        rel = j["released_at"].astimezone().strftime("%d-%m-%Y %H:%M") if j["released_at"] else "never"
        p.body.addWidget(label(f"{n_steps} steps · released {rel}", "Muted"))
        hint = QFrame()
        hint.setObjectName("Hint")
        hl = QHBoxLayout(hint)
        hl.setContentsMargins(8, 6, 8, 6)
        hl.addWidget(label("Any change returns a released job to Draft. A second person must release it again "
                           "before operators run it.", "Muted", wrap=True))
        p.body.addWidget(hint)
        for w in (self.f_name, self.f_pid, self.f_pn, self.f_cycle, self.f_desc):
            w.setEnabled(edit)
        p.end()
        save = button("Save", "primary", self.save_job, ico="save")
        save.setEnabled(edit)
        rel_b = button("Release", None, self.release, ico="release")
        rel_b.setEnabled(bool(self.ctx.session and self.ctx.session.can(Perm.RELEASE_JOBS))
                         and j["status"] == "DRAFT")
        revert_b = button("Revert to Draft", None, self.revert_to_draft, ico="draft")
        revert_b.setEnabled(bool(self.ctx.session and (self.ctx.session.can(Perm.RELEASE_JOBS) or self.ctx.session.can(Perm.EDIT_JOBS)))
                            and j["status"] == "RELEASED")
        p.footer.addWidget(rel_b)
        p.footer.addWidget(revert_b)
        p.footer.addStretch(1)
        p.footer.addWidget(save)

    # ================================================================== actions
    def _guard(self, perm=Perm.EDIT_JOBS):
        try:
            return self.ctx.require(perm)
        except PermissionDenied as exc:
            error(self, str(exc))
            return None

    def new_job(self) -> None:
        s = self._guard()
        if not s:
            return
        code, ok = QInputDialog.getText(self, "New job", "Job code (letters, digits, - _):")
        if not ok or not code.strip():
            return
        if not CODE_RE.match(code.strip()):
            return error(self, "Job code may contain letters, digits, '-' and '_' (2-64 characters).")
        name, ok = QInputDialog.getText(self, "New job", "Job name:")
        if not ok:
            return
        try:
            jid = self.ctx.jobs.create(code.strip(), name.strip() or code.strip(), "", "", s.user_id)
        except Exception as exc:
            return error(self, f"Could not create job: {exc}")
        self.ctx.audit("JOB_CREATED", "job", jid, {"code": code})
        self.main.set_edit_job(jid)
        self.reload()

    def duplicate(self) -> None:
        s = self._guard()
        if not s or not self.job:
            return
        code, ok = QInputDialog.getText(self, "Duplicate job", "Code for the copy:", text=self.job["code"] + "-COPY")
        if not ok or not CODE_RE.match(code.strip()):
            return
        try:
            jid = self.ctx.jobs.duplicate(self.job["id"], code.strip(), self.job["name"] + " (copy)", s.user_id)
        except Exception as exc:
            return error(self, f"Duplicate failed: {exc}")
        self.ctx.audit("JOB_DUPLICATED", "job", jid, {"from": self.job["code"]})
        self.main.set_edit_job(jid)
        self.reload()

    def _edit_notes(self) -> None:
        s = self._guard()
        if not s or not self.job:
            return
        settings = dict(self.job.get("settings") or {})
        cur_notes = settings.get("notes") or self.job.get("description") or ""
        text, ok = QInputDialog.getMultiLineText(self, "Internal Job Notes", "Internal notes for this job:", cur_notes)
        if ok and self._guard():
            settings["notes"] = text.strip()
            self.ctx.jobs.update(self.job["id"], self.job["name"], self.job.get("part_number") or "", text.strip(), settings, s.user_id)
            self.ctx.jobs.mark_draft(self.job["id"], s.user_id)
            self.reload()

    def edit_options(self, job_id: int) -> None:
        j = self.ctx.jobs.get(job_id)
        settings = dict(j.get("settings") or {})
        current_options = settings.get("job_options") or []
        dlg = OptionsPopup(self, current_options, self.can_edit())
        if dlg.exec() == QDialog.Accepted and self._guard():
            settings["job_options"] = dlg.result_options
            self.ctx.jobs.update(job_id, j["name"], j.get("part_number") or "", j.get("description") or "", settings, self.ctx.session.user_id)
            self.ctx.jobs.mark_draft(job_id, self.ctx.session.user_id)
            self.ctx.audit("OPTIONS_CHANGED", "job", job_id, {"options": dlg.result_options})
            self.reload()

    def edit_variants(self, job_id: int) -> None:
        j = self.ctx.jobs.get(job_id)
        settings = dict(j.get("settings") or {})
        options = settings.get("job_options") or []
        default_cycle = int(j.get("expected_cycle_s") or 60)
        dlg = VariantsPopup(self, self.ctx.jobs.variants(job_id), options, self.can_edit(), default_cycle)
        if dlg.exec() == QDialog.Accepted and self._guard():
            self.ctx.jobs.save_variants(job_id, dlg.result_variants)
            self.ctx.jobs.mark_draft(job_id, self.ctx.session.user_id)
            self.ctx.audit("VARIANTS_CHANGED", "job", job_id, {"variants": dlg.result_variants})
            self.reload()

    def save_job(self) -> None:
        s = self._guard()
        if not s or not self.job:
            return
        t = self.f_cycle.time()
        secs = t.hour() * 3600 + t.minute() * 60 + t.second()
        if not self.f_name.text().strip():
            return error(self, "Name is required.")
        self.ctx.catalog.update_props(self.job["id"], self.f_name.text().strip(), self.f_pid.text().strip(),
                                      self.f_pn.text().strip(), self.f_desc.toPlainText().strip(), max(1, secs),
                                      s.user_id)
        self.ctx.audit("JOB_UPDATED", "job", self.job["id"], {"name": self.f_name.text(), "cycle_s": secs})
        self.main._refresh_command()
        self.reload()

    def pick_image(self) -> None:
        s = self._guard()
        if not s or not self.job:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Job image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return error(self, "The file is not a readable image.")
        scale = 360 / max(img.shape[:2])
        if scale < 1:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", img)
        self.ctx.catalog.set_image(self.job["id"], buf.tobytes(), s.user_id)
        self.ctx.audit("JOB_IMAGE_SET", "job", self.job["id"], {})
        self.reload()

    def clear_image(self) -> None:
        s = self._guard()
        if s and self.job:
            self.ctx.catalog.set_image(self.job["id"], None, s.user_id)
            self.reload()

    def release(self) -> None:
        s = self._guard(Perm.RELEASE_JOBS)
        if not s or not self.job:
            return
        j = self.ctx.jobs.get(self.job["id"])
        if j["status"] == "RELEASED":
            return info(self, "This revision is already released.")
        if (self.ctx.settings.get("require_independent_release") and j["updated_by"] == s.user_id
                and s.role_code != "ADMIN"):
            return error(self, "Segregation of duties: the job must be released by someone other than its last "
                               "editor.")
        steps = self.ctx.jobs.steps(j["id"])
        problems = [x["name"] for x in steps if STEP_SPECS.get(x["step_type"], {}).get("detection")
                    and not x["detection_id"]]
        if problems:
            return error(self, "Cannot release - steps without detection:\n" + "\n".join(problems[:15]))
        if not steps:
            return error(self, "Cannot release a job without steps.")
        untaught = [d["name"] for d in self.ctx.detections.list_for(j["id"]) if d["enabled"] and not d["taught_at"]]
        if untaught and not confirm(self, "Not taught yet:\n" + "\n".join(untaught[:15]) + "\n\nRelease anyway?"):
            return
        if not confirm(self, f"Release {j['code']} as revision {j['revision'] + 1}? Operators run it immediately."):
            return
        rev = self.ctx.jobs.release(j["id"], s.user_id)
        self.ctx.audit("JOB_RELEASED", "job", j["id"], {"code": j["code"], "revision": rev})
        self.main._refresh_command()
        self.reload()

    def revert_to_draft(self) -> None:
        s = self._guard(Perm.EDIT_JOBS)
        if not s or not self.job:
            return
        j = self.ctx.jobs.get(self.job["id"])
        if j["status"] != "RELEASED":
            return info(self, "This job is already in Draft.")
        if not confirm(self, f"Revert job '{j['code']}' to Draft? Operators will not be able to run it until it is re-released."):
            return
        self.ctx.jobs.mark_draft(j["id"], s.user_id)
        self.ctx.audit("JOB_REVERTED_TO_DRAFT", "job", j["id"], {"code": j["code"]})
        self.main._refresh_command()
        self.reload()

    def archive(self, job_id: int) -> None:
        if not self._guard():
            return
        j = self.ctx.jobs.get(job_id)
        if confirm(self, f"Archive job {j['code']}? It disappears from the operator job list; inspection history "
                         "is kept."):
            self.ctx.jobs.archive(job_id, self.ctx.session.user_id)
            self.ctx.audit("JOB_ARCHIVED", "job", job_id, {})
            if self.main.edit_job_id == job_id:
                self.main.set_edit_job(None)
            self.reload()
