"""Job executor: runs the step tree of a released job against live detection states.

Runs in the Qt main thread. Inputs: detection states (camera thread -> queued signal), barcode
scans, operator answers and a 10 Hz timer. Outputs: Qt signals for the UI / projector and
result records for the background recorder.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QTimer, Signal

from vista.engine.steps import CONTAINERS, STEP_SPECS

log = logging.getLogger(__name__)

PENDING, ACTIVE, DONE, SKIPPED, FAILED = "PENDING", "ACTIVE", "DONE", "SKIPPED", "FAILED"
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def _utc() -> datetime:
    return datetime.now(timezone.utc)


class Node:
    __slots__ = ("step", "children", "parent", "state", "result", "t_start", "dt_start", "idx", "repeat_i",
                 "base_counter", "count_needed", "ng_recorded", "data", "number")

    def __init__(self, step: dict, parent: "Node | None"):
        self.step, self.parent = step, parent
        self.children: list[Node] = []
        self.number = ""
        self.reset()

    def reset(self) -> None:
        self.state, self.result = PENDING, None
        self.t_start, self.dt_start = 0.0, None
        self.idx, self.repeat_i, self.base_counter, self.count_needed = 0, 0, 0, 1
        self.ng_recorded = False
        self.data = {}
        for c in self.children:
            c.reset()

    @property
    def type(self) -> str:
        return self.step["step_type"]

    @property
    def params(self) -> dict:
        return self.step.get("params") or {}

    @property
    def is_container(self) -> bool:
        return self.type in CONTAINERS

    def path(self) -> str:
        parts, n = [], self
        while n is not None and n.step.get("id") is not None:
            parts.append(n.step["name"])
            n = n.parent
        return " / ".join(reversed(parts))

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class Executor(QObject):
    run_started = Signal(dict)
    run_finished = Signal(dict)
    tree_changed = Signal()
    message = Signal(str, str)             # text, level: info|warn|error|ok
    dialog_requested = Signal(dict)        # {"node": id, "question": str, "options": [...]}
    supervisor_requested = Signal(dict)    # {"node": id, "reason": str}
    identity_changed = Signal(dict)        # barcode / variant

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.job: dict | None = None
        self.variants: list[dict] = []
        self.root: Node | None = None
        self.mode = "IDLE"                 # IDLE | READY | RUNNING | RESULT
        self.run_id: uuid.UUID | None = None
        self.variant: dict | None = None
        self.barcode: str | None = None
        self.variables: dict[str, str] = {}
        self.states: dict = {}
        self.ng_count = self.forced_count = 0
        self.run_t0 = 0.0
        self.run_dt0: datetime | None = None
        self.last_result: dict | None = None
        self._pending_scan: str | None = None
        self._result_until = 0.0
        self._reset_counter: dict[int, int] = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    # ================================================================== job loading
    def load_job(self, job: dict, variants: list[dict], steps: list[dict]) -> None:
        if self.mode == "RUNNING":
            self.abort("Job changed")
        by_parent: dict = {}
        for s in steps:
            by_parent.setdefault(s["parent_id"], []).append(s)
        root = Node({"id": None, "name": job["name"], "step_type": "NUMBERED", "params": {}}, None)

        def build(node: Node, parent_id):
            for s in sorted(by_parent.get(parent_id, []), key=lambda r: (r["sort_order"], r["id"])):
                child = Node(s, node)
                node.children.append(child)
                if s["step_type"] in CONTAINERS:
                    build(child, s["id"])
        build(root, None)

        def number(node: Node, prefix: str):
            i = 0
            for c in node.children:
                if c.type == "NUMBERED":
                    i += 1
                    c.number = f"{prefix}{i}."
                    number(c, c.number)
                elif c.is_container:
                    number(c, prefix)
        number(root, "")
        self.job, self.variants, self.root = job, [v for v in variants if v.get("is_active", True)], root
        self.mode = "READY"
        self.tree_changed.emit()

    def unload(self) -> None:
        if self.mode == "RUNNING":
            self.abort("Job unloaded")
        self.job, self.root, self.mode = None, None, "IDLE"
        self.ctx.engine.set_watch(self._always_watch())
        self.tree_changed.emit()

    # ================================================================== run control
    def start_run(self, variant_code: str | None = None) -> None:
        if self.root is None or self.job is None:
            self.message.emit("Select a released job first.", "warn")
            return
        if self.job.get("status") != "RELEASED":
            self.message.emit("This job is not released for production.", "error")
            return
        self.root.reset()
        self.run_id = uuid.uuid4()
        self.variables = {"JOB": self.job["code"]}
        self.variant = next((v for v in self.variants if v["code"] == variant_code), None)
        if self.variant is None and len(self.variants) == 1:
            self.variant = self.variants[0]
        job_cycle = self.job.get("expected_cycle_s") or (self.job.get("settings") or {}).get("cycle_time_expected_s") or 60
        cycle_sec = int((self.variant.get("expected_cycle_s") if self.variant and self.variant.get("expected_cycle_s") else None) or job_cycle)
        hh = cycle_sec // 3600
        mm = (cycle_sec % 3600) // 60
        ss = cycle_sec % 60
        self.variables["Expected Cycle Time"] = f"{hh:02d}:{mm:02d}:{ss:02d}"
        self.variables["Expected Cycle Time Sec"] = str(cycle_sec)
        self.barcode = None
        self.ng_count = self.forced_count = 0
        self.run_t0, self.run_dt0 = time.monotonic(), _utc()
        self.mode = "RUNNING"
        self._overtime = False
        self.ctx.recorder.start_run(self.run_id, self.job["id"], self.job["revision"], self.ctx.session.user_id,
                                    self.run_dt0)
        if self.variant:
            self.variables["VARIANT"] = self.variant["code"]
            self.ctx.recorder.identity(self.run_id, None, self.variant["code"])
        self._activate(self.root)
        self.run_started.emit({"run_id": str(self.run_id), "job": self.job["code"]})
        self.identity_changed.emit(self.identity())
        self._advance()

    def abort(self, reason: str = "Aborted by operator") -> None:
        if self.mode != "RUNNING":
            return
        self._finish("ABORTED", reason)

    def identity(self) -> dict:
        return {"barcode": self.barcode, "variant": self.variant["code"] if self.variant else None,
                "variant_name": self.variant["name"] if self.variant else None}

    # ================================================================== inputs
    def on_states(self, states: dict) -> None:
        self.states = states
        self._check_reset_button()
        if self.mode == "RUNNING":
            self._advance()

    def on_barcode(self, code: str) -> None:
        code = code.strip()
        if not code:
            return
        if self.mode == "RUNNING":
            self._pending_scan = code
            self._advance()
        elif self.mode == "READY" and self.ctx.settings.get("start_on_scan", True):
            self.start_run()
            self._pending_scan = code
            self._advance()

    def answer_dialog(self, node_id: int, option: str) -> None:
        node = self._find(node_id)
        if node is not None and node.state == ACTIVE and node.type == "OPERATOR_DIALOG":
            node.data["answer"] = option
            self._advance()

    def confirm_instruction(self) -> None:
        for n in self.active_leaves():
            if n.type in ("WORK_INSTRUCTION", "NEXT_BUTTON", "START", "JOB_CLOSE_BUTTON", "BACK", "PREVIOUS_BUTTON"):
                n.data["confirmed"] = True
        self._advance()

    def supervisor_decision(self, node_id: int, accept: bool, user_id: int, username: str) -> None:
        node = self._find(node_id)
        if node is None or node.state != ACTIVE:
            return
        self.ctx.audit("SUPERVISOR_DECISION", "step", node.step.get("id"),
                       {"accept": accept, "step": node.path(), "run": str(self.run_id), "by": username})
        if accept:
            self._complete(node, "FORCED", value="Accepted by supervisor", actor=user_id)
            self.forced_count += 1
            self._advance()
        else:
            self._complete(node, "NG", value="Rejected by supervisor", actor=user_id)
            self._finish("FAIL", f"Rejected by supervisor at {node.step['name']}")

    def force_active(self, user_id: int, username: str) -> None:
        """Supervisor override: complete all currently active leaf steps as FORCED."""
        leaves = self.active_leaves()
        for n in leaves:
            self._complete(n, "FORCED", value=f"Forced by {username}", actor=user_id)
            self.forced_count += 1
        if leaves:
            self.ctx.audit("FORCE_STEP", "run", str(self.run_id), {"steps": [n.path() for n in leaves],
                                                                   "by": username})
            self._advance()

    # ================================================================== queries
    def active_leaves(self) -> list[Node]:
        if self.root is None or self.mode != "RUNNING":
            return []
        return [n for n in self.root.walk() if n.state == ACTIVE and not n.is_container]

    def active_numbered(self) -> Node | None:
        for n in self.active_leaves():
            p = n.parent
            while p is not None and p.type != "NUMBERED":
                p = p.parent
            if p is not None and p.step.get("id") is not None:
                return p
        return None

    def _find(self, step_id) -> Node | None:
        if self.root is None:
            return None
        return next((n for n in self.root.walk() if n.step.get("id") == step_id), None)

    def _always_watch(self) -> set[int]:
        if not self.ctx.settings.get("reset_enabled", True):
            return set()
        return {d for d, m in self.ctx.engine.meta().items()
                if m["det_type"] == "BUTTON" and (m.get("params") or {}).get("function") == "RESET"}

    # ================================================================== core
    def _included(self, node: Node) -> bool:
        s = node.step
        if s.get("id") is None:
            return True
        if not s.get("enabled", True):
            return False
        codes = s.get("variant_codes") or []
        if codes and self.variant is not None and self.variant["code"] not in codes:
            return False
        return True

    def _activate(self, node: Node) -> None:
        if not self._included(node):
            node.state = SKIPPED
            return
        node.state = ACTIVE
        node.t_start, node.dt_start = time.monotonic(), _utc()
        if node.is_container:
            node.idx = 0
            order = node.params.get("order", "SEQUENTIAL")
            if order in ("ANY_ORDER", "ONE_ARBITRARY", "SIMULTANEOUS"):
                for c in node.children:
                    self._activate(c)
            elif node.children:
                self._activate(node.children[0])
            return
        det_id = node.step.get("detection_id") or node.params.get("detection_id")
        st = self.states.get(det_id) if det_id else None
        node.base_counter = st.counter if st else 0
        node.count_needed = int(node.params.get("count", 1) or 1)

    def _advance(self) -> None:
        if self.mode != "RUNNING" or self.root is None:
            return
        for _ in range(50):                 # instant steps may complete in a chain
            before = self._signature()
            if self._update(self.root):
                self._finish("PASS")
                return
            if self.mode != "RUNNING":
                return
            if self._signature() == before:
                break
        self.ctx.engine.set_watch(self._watch_ids())
        self.tree_changed.emit()

    def _signature(self):
        return tuple(n.state for n in self.root.walk())

    def _watch_ids(self) -> set[int]:
        ids = {n.step["detection_id"] for n in self.active_leaves() if n.step.get("detection_id")}
        return ids | self._always_watch()

    def _update(self, node: Node) -> bool:
        if node.state in (DONE, SKIPPED):
            return True
        if node.state == FAILED:
            return False
        if node.state == PENDING:
            self._activate(node)
            if node.state == SKIPPED:
                return True
        if node.is_container:
            return self._update_container(node)
        return self._update_leaf(node)

    def _update_container(self, node: Node) -> bool:
        order = node.params.get("order", "SEQUENTIAL")
        while True:
            if order == "ANY_ORDER":
                results = [self._update(c) for c in list(node.children)]   # evaluate every child
                done = all(results) and all(c.state in (DONE, SKIPPED) for c in node.children)
            elif order == "ONE_ARBITRARY":
                results = [self._update(c) for c in list(node.children)]
                done = any(c.state == DONE for c in node.children)
                if done:
                    for c in node.children:
                        if c.state not in (DONE, SKIPPED):
                            c.state = SKIPPED
            elif order == "SIMULTANEOUS":
                active_children = [c for c in node.children if c.state == ACTIVE]
                if not active_children:
                    done = True
                else:
                    results = [self._update(c) for c in active_children]
                    done = all(results) and all(c.state in (DONE, SKIPPED) for c in node.children)
            else:
                done = True
                while node.idx < len(node.children):
                    c = node.children[node.idx]
                    if not self._update(c):
                        done = False
                        break
                    node.idx += 1
            if not done:
                return False
            repeat = int(node.params.get("repeat", 1) or 1)
            if node.repeat_i + 1 < repeat:
                node.repeat_i += 1
                for c in node.children:
                    c.reset()
                node.idx = 0
                if order in ("ANY_ORDER", "ONE_ARBITRARY", "SIMULTANEOUS"):
                    for c in node.children:
                        self._activate(c)
                elif node.children:
                    self._activate(node.children[0])
                continue
            node.state = DONE
            return True

    def _det_state(self, node: Node):
        det_id = node.step.get("detection_id") or node.params.get("detection_id")
        return self.states.get(det_id) if det_id else None

    def _update_leaf(self, node: Node) -> bool:
        t = node.type
        p = node.params
        elapsed = time.monotonic() - node.t_start
        timeout = float(p.get("timeout_s", 0) or 0)
        if timeout and elapsed > timeout and not node.data.get("warned"):
            node.data["warned"] = True
            self.message.emit(f"Step '{node.step['name']}' is taking longer than {int(timeout)} s", "warn")

        det_id = node.step.get("detection_id") or node.params.get("detection_id")
        if STEP_SPECS[t]["detection"] and not det_id:
            if not node.data.get("blocked"):
                node.data["blocked"] = True
                self.message.emit(f"Step '{node.step['name']}' has no detection - a supervisor must force it.",
                                  "error")
            return False

        if t in ("PLACE", "TAKE", "MATERIAL"):
            st = self._det_state(node)
            if st is None or st.state in ("UNTAUGHT", "DISABLED"):
                return self._blocked(node, "detection is not taught or disabled")
            if t == "MATERIAL":
                needed = int(p.get("items_count", 1) or 1)
                if st.counter - node.base_counter >= needed:
                    self._complete(node, "OK", score=st.score)
                    return True
                return False
            if st.det_type in ("ACTIVITY", "PICKING_BIN"):
                if st.counter - node.base_counter >= node.count_needed:
                    self._complete(node, "OK", score=st.score)
                    return True
                return False
            want = "PRESENT" if t == "PLACE" else "ABSENT"
            if st.state == want or (want == "PRESENT" and st.state == "OCC_PRESENT") or (want == "ABSENT" and st.state == "OCC_ABSENT"):
                self._complete(node, "OK", score=st.score)
                return True
            return False

        if t in ("ACTIVITY", "PRESS"):
            st = self._det_state(node)
            if st is None or st.state in ("UNTAUGHT", "DISABLED"):
                return self._blocked(node, "detection is not taught or disabled")
            if st.counter - node.base_counter >= node.count_needed or (t == "PRESS" and st.state == "PRESSED"):
                self._complete(node, "OK")
                return True
            return False

        if t == "QUALITY_CHECK":
            return self._update_qc(node)

        if t == "WAIT_BARCODE":
            code = self._pending_scan
            if code is None:
                return False
            self._pending_scan = None
            return self._accept_barcode(node, code)

        if t == "SNAPSHOT":
            path = self.ctx.save_evidence(self.run_id, self.barcode, node.step["name"], p.get("source", "COLOR"))
            self._complete(node, "INFO", evidence=path)
            return True

        if t == "SET_VARIABLE":
            value = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), str(p.get("value", "")))
            if p.get("variable"):
                self.variables[p["variable"]] = value
            if p.get("record", True):
                self._complete(node, "INFO", value=value)
            else:
                node.state = DONE
            return True

        if t == "COUNTER":
            var_name = str(p.get("variable", "COUNT")).strip() or "COUNT"
            action = str(p.get("action", "INCREMENT")).upper()
            limit = int(p.get("limit", 0) or 0)
            try:
                cur_val = int(self.variables.get(var_name, 0))
            except Exception:
                cur_val = 0
            if action == "RESET":
                new_val = 0
            elif action == "DECREMENT":
                new_val = cur_val - 1
            else:
                new_val = cur_val + 1
            self.variables[var_name] = str(new_val)
            if limit > 0 and new_val >= limit:
                self.message.emit(f"Counter '{var_name}' reached limit ({new_val}/{limit})", "warn")
            self._complete(node, "OK", value=str(new_val))
            return True

        if t == "FIND_REPLACE_STRING":
            var_name = str(p.get("variable", "")).strip()
            src = str(self.variables.get(var_name, ""))
            find_str = str(p.get("find", ""))
            repl_str = str(p.get("replace", ""))
            res = src.replace(find_str, repl_str)
            if var_name:
                self.variables[var_name] = res
            self._complete(node, "OK", value=res)
            return True

        if t == "FUNCTION":
            expr = str(p.get("expression", "")).strip()
            res_var = str(p.get("result_variable", "RESULT")).strip() or "RESULT"
            expr = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), "0"), expr)
            safe_dict = {
                "__builtins__": None,
                "abs": abs, "min": min, "max": max, "round": round,
                "pow": pow, "int": int, "float": float, "str": str, "len": len,
            }
            for k, v in self.variables.items():
                if k.isidentifier():
                    try:
                        safe_dict[k] = float(v) if "." in str(v) else int(v)
                    except Exception:
                        safe_dict[k] = v
            try:
                val = str(eval(expr, safe_dict, {}))
            except Exception as e:
                val = f"ERR: {e}"
            self.variables[res_var] = val
            self._complete(node, "OK", value=val)
            return True

        if t == "SPLIT_STRING_INDEX":
            var_name = str(p.get("variable", "")).strip()
            src = str(self.variables.get(var_name, ""))
            idx = int(p.get("index", 0) or 0)
            length = int(p.get("length", 0) or 0)
            left_var = str(p.get("left_var", "LEFT")).strip() or "LEFT"
            right_var = str(p.get("right_var", "RIGHT")).strip() or "RIGHT"
            if length > 0:
                res = src[idx:idx + length]
                self.variables[left_var] = res
                if right_var:
                    self.variables[right_var] = src[idx + length:]
            else:
                left_val = src[:idx]
                right_val = src[idx:]
                self.variables[left_var] = left_val
                self.variables[right_var] = right_val
                res = f"{left_val}|{right_val}"
            self._complete(node, "OK", value=res)
            return True

        if t == "SPLIT_STRING_ARG":
            var_name = str(p.get("variable", "")).strip()
            src = str(self.variables.get(var_name, ""))
            delim = str(p.get("delimiter", ","))
            idx = int(p.get("index", 0) or 0)
            parts = src.split(delim)
            part_val = parts[idx] if 0 <= idx < len(parts) else ""
            res_var = str(p.get("result_variable", f"{var_name}_PART_{idx}")).strip() or f"{var_name}_PART_{idx}"
            rem_var = str(p.get("remainder_variable", "REMAINDER")).strip()
            self.variables[res_var] = part_val
            if rem_var and idx + 1 < len(parts):
                self.variables[rem_var] = delim.join(parts[idx + 1:])
            self._complete(node, "OK", value=part_val)
            return True

        if t == "STRING_FORMATTER":
            tmpl = str(p.get("template", ""))
            res_var = str(p.get("result_variable", "RESULT")).strip() or "RESULT"
            val = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), tmpl)
            self.variables[res_var] = val
            self._complete(node, "OK", value=val)
            return True

        if t == "STRING_PARSER":
            var_name = str(p.get("variable", "")).strip()
            regex_pat = str(p.get("regex", "")).strip()
            src = str(self.variables.get(var_name, ""))
            if regex_pat:
                try:
                    m = re.search(regex_pat, src)
                    if m:
                        for k, v in m.groupdict().items():
                            self.variables[k] = v
                        for i, g in enumerate(m.groups()):
                            self.variables[f"{var_name}_G{i+1}"] = str(g or "")
                except Exception:
                    pass
            self._complete(node, "OK", value=src)
            return True

        if t == "VARIABLE_CHANGED":
            var_name = str(p.get("variable", "")).strip()
            cond = str(p.get("condition", "VALUE_CHANGED")).upper()
            target_val = str(p.get("target_value", "")).strip()
            target_val = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), target_val)
            if "init_val" not in node.data:
                node.data["init_val"] = self.variables.get(var_name, None)
            cur_val = self.variables.get(var_name, None)

            satisfied = False
            if cond == "VALUE_CHANGED":
                satisfied = (cur_val != node.data["init_val"])
            elif cond == "EQUALS":
                satisfied = (str(cur_val) == target_val)
            elif cond == "NOT_EQUALS":
                satisfied = (str(cur_val) != target_val)
            elif cond == "MATCHES_VARIABLE":
                other_val = self.variables.get(target_val, "")
                satisfied = (str(cur_val) == str(other_val))
            elif cond == "NOT_MATCHES_VARIABLE":
                other_val = self.variables.get(target_val, "")
                satisfied = (str(cur_val) != str(other_val))

            if satisfied:
                self._complete(node, "OK", value=f"{var_name}={cur_val}")
                return True
            if timeout > 0 and elapsed > timeout:
                self._complete(node, "OK", value="Timeout (unchanged)")
                return True
            return False

        if t == "VARIABLE_LOGGER":
            var_names = [v.strip() for v in str(p.get("variables", "")).split(",") if v.strip()]
            vals = {k: self.variables.get(k, "") for k in (var_names if var_names else self.variables.keys())}
            self.ctx.audit("VARIABLE_LOGGER", "step", node.step.get("id"), {"run_id": str(self.run_id), "values": vals})
            self._complete(node, "INFO", value=str(vals))
            return True

        if t == "DELAY":
            if elapsed * 1000 >= float(p.get("ms", 1000)):
                node.state = DONE
                return True
            return False

        if t == "OPERATOR_DIALOG":
            if "asked" not in node.data:
                node.data["asked"] = True
                opts = [o.strip() for o in str(p.get("options", "OK,NOT OK")).split(",") if o.strip()]
                self.dialog_requested.emit({"node": node.step["id"], "question": p.get("question", ""),
                                            "options": opts})
            ans = node.data.get("answer")
            if ans is None:
                return False
            if p.get("variable"):
                self.variables[p["variable"]] = ans
            ng = {o.strip() for o in str(p.get("ng_options", "")).split(",") if o.strip()}
            if ans in ng:
                self._complete(node, "NG", value=ans)
                self.ng_count += 1
                self._finish("FAIL", f"{node.step['name']}: {ans}")
                return False
            self._complete(node, "OK", value=ans)
            return True

        if t in ("WORK_INSTRUCTION", "NEXT_BUTTON", "START", "JOB_CLOSE_BUTTON", "BACK", "PREVIOUS_BUTTON"):
            if node.data.get("confirmed"):
                if t == "JOB_CLOSE_BUTTON":
                    self._complete(node, "OK")
                    self._finish("PASS", "Job ended by operator")
                    return True
                if t in ("BACK", "PREVIOUS_BUTTON"):
                    if node.parent and node.parent.is_container and node.parent.idx > 0:
                        node.reset()
                        node.parent.idx -= 1
                        prev_node = node.parent.children[node.parent.idx]
                        prev_node.reset()
                        self._activate(prev_node)
                        return False
                self._complete(node, "OK")
                return True
            return False

        if t == "SOUND":
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass
            self._complete(node, "OK")
            return True

        if t == "FILE_MOVER":
            src = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), str(p.get("source", "")))
            dst = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), str(p.get("destination", "")))
            action = str(p.get("action", "MOVE")).upper()
            try:
                if src and dst and os.path.exists(src):
                    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
                    if action == "COPY":
                        shutil.copy2(src, dst)
                    else:
                        shutil.move(src, dst)
                    self._complete(node, "OK", value=f"{action} {src} -> {dst}")
                else:
                    self._complete(node, "OK", value=f"{action} skipped (source not found)")
            except Exception as ex:
                self._complete(node, "OK", value=f"File error: {ex}")
            return True

        if t == "READ_FROM_FILE":
            path = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), str(p.get("path", "")))
            var_name = str(p.get("variable", "FILE_CONTENT")).strip() or "FILE_CONTENT"
            try:
                if path and os.path.exists(path):
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    self.variables[var_name] = content
                    self._complete(node, "OK", value=f"Read {len(content)} chars")
                else:
                    self._complete(node, "OK", value="File not found")
            except Exception as ex:
                self._complete(node, "OK", value=f"Read error: {ex}")
            return True

        if t == "WRITE_TO_FILE":
            path = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), str(p.get("path", "")))
            content = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), str(p.get("content", "")))
            mode = "a" if str(p.get("mode", "OVERWRITE")).upper() == "APPEND" else "w"
            try:
                if path:
                    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                    with open(path, mode, encoding="utf-8", errors="replace") as f:
                        f.write(content)
                    self._complete(node, "OK", value=f"Wrote {len(content)} chars")
                else:
                    self._complete(node, "OK", value="No path")
            except Exception as ex:
                self._complete(node, "OK", value=f"Write error: {ex}")
            return True

        if t == "SEND_COMMUNICATION":
            channel = str(p.get("channel", "EMAIL"))
            dest = str(p.get("destination", ""))
            msg = _VAR_RE.sub(lambda m: self.variables.get(m.group(1), ""), str(p.get("message", "")))
            self.ctx.audit("COMMUNICATION_SENT", "step", node.step.get("id"), {"channel": channel, "dest": dest, "message": msg})
            self._complete(node, "OK", value=f"{channel} to {dest}")
            return True

        if t == "WAIT_COMMUNICATION":
            if (timeout > 0 and elapsed >= timeout) or node.data.get("received") or elapsed >= 0.5:
                self._complete(node, "OK", value="Communication received/passed")
                return True
            return False

        if t == "RESETTER":
            for s in self.states.values():
                s.state = "IDLE"
                s.counter = 0
            self._complete(node, "OK", value="Detections reset")
            return True

        if t == "JOB_CONTROL":
            action = str(p.get("action", "NEXT_STEP")).upper()
            target_step = str(p.get("target_step", "")).strip()
            if action == "ABORT":
                self._finish("FAIL", "Aborted by Job Control")
                return False
            elif action == "PAUSE":
                self.mode = "PAUSED"
                self.state_changed.emit(self.mode)
                self._complete(node, "OK", value="Paused")
                return True
            elif action in ("REPEAT", "RESTART"):
                if self.root:
                    for n in self.root.walk():
                        n.reset()
                    self._activate(self.root)
                return False
            elif action == "NEXT_STEP":
                self._complete(node, "OK", value="Next step")
                return True
            elif action == "PREVIOUS_STEP":
                if node.parent and node.parent.is_container and node.parent.idx > 0:
                    node.reset()
                    node.parent.idx -= 1
                    prev_node = node.parent.children[node.parent.idx]
                    prev_node.reset()
                    self._activate(prev_node)
                    return False
                self._complete(node, "OK", value="Previous step")
                return True
            elif action == "GO_TO_STEP" and target_step:
                target_node = next((n for n in self.root.walk() if str(n.step.get("id")) == target_step or n.step.get("name") == target_step), None)
                if target_node:
                    self._complete(node, "OK", value=f"Jump to {target_step}")
                    for n in self.root.walk():
                        if n.state == ACTIVE and n != target_node:
                            n.state = SKIPPED
                    target_node.reset()
                    p = target_node.parent
                    curr = target_node
                    while p:
                        if p.is_container and curr in p.children:
                            p_idx = p.children.index(curr)
                            p.idx = (p_idx - 1) if p == node.parent else p_idx
                            for skipped_sibling in p.children[:p_idx]:
                                if skipped_sibling.state == PENDING:
                                    skipped_sibling.state = SKIPPED
                        curr = p
                        p = p.parent
                    self._activate(target_node)
                    return True
            self._complete(node, "OK", value=action)
            return True

        if t == "SWITCH_JOB_OPTION":
            opt = str(p.get("option", "")).strip()
            self._complete(node, "OK", value=f"Option {opt}")
            return True

        if t == "SWITCH_STEP":
            var_name = str(p.get("variable", "")).strip()
            match_val = str(p.get("value", "")).strip()
            cur_val = str(self.variables.get(var_name, "")).strip()
            self._complete(node, "OK", value=f"{var_name}={cur_val} (match={match_val})")
            return True

        if t == "PROCESS_CONTROL":
            action = str(p.get("action", "START")).upper()
            proc_name = str(p.get("process_name", "")).strip()
            sig = str(p.get("signal", ""))
            val = str(p.get("value", "1"))
            self.ctx.audit("PROCESS_CONTROL", "step", node.step.get("id"), {"action": action, "process": proc_name, "signal": sig, "value": val})
            self._complete(node, "OK", value=f"{proc_name}:{action}")
            return True

        if t == "EXECUTE_TASK":
            task_id = str(p.get("task_id", ""))
            self.ctx.audit("EXECUTE_TASK", "step", node.step.get("id"), {"task_id": task_id})
            self._complete(node, "OK", value=f"Task {task_id}")
            return True

        return False

    def _blocked(self, node: Node, why: str) -> bool:
        if not node.data.get("blocked"):
            node.data["blocked"] = True
            self.message.emit(f"Step '{node.step['name']}' is blocked: {why}.", "error")
        return False

    def _update_qc(self, node: Node) -> bool:
        st = self._det_state(node)
        if st is None or st.state in ("UNTAUGHT", "DISABLED"):
            return self._blocked(node, "quality check is not taught or disabled")
        if st.state == "OK":
            ev = None
            if node.params.get("save_evidence", True) and self.ctx.settings.get("save_pass_evidence", True):
                ev = self.ctx.save_evidence(self.run_id, self.barcode, node.step["name"], self._qc_source(node),
                                            roi_det=node.step.get("detection_id"))
            self._complete(node, "OK", score=st.score, qc_class=st.qc_class, evidence=ev)
            return True
        if st.state in ("NG", "MISSING"):
            policy = node.params.get("on_fail", "REWORK")
            if not node.ng_recorded:
                node.ng_recorded = True
                self.ng_count += 1
                ev = self.ctx.save_evidence(self.run_id, self.barcode, node.step["name"] + "_NG",
                                            self._qc_source(node), roi_det=node.step.get("detection_id"))
                self.ctx.recorder.step_result(self.run_id, self._result_row(node, "NG", score=st.score,
                                                                            qc_class=st.qc_class, evidence=ev,
                                                                            final=False))
                self.message.emit(f"{node.step['name']}: {st.qc_class} - "
                                  + ("rework and the check repeats automatically" if policy == "REWORK"
                                     else "supervisor decision required" if policy == "ASK_SUPERVISOR"
                                     else "part rejected"), "error")
                if policy == "FAIL_RUN":
                    node.state = FAILED
                    self._finish("FAIL", f"{node.step['name']}: {st.qc_class}")
                    return False
                if policy == "ASK_SUPERVISOR":
                    self.supervisor_requested.emit({"node": node.step["id"],
                                                    "reason": f"{node.step['name']}: {st.qc_class}"})
        return False

    def _qc_source(self, node: Node) -> str:
        meta = self.ctx.engine.meta().get(node.step.get("detection_id"), {})
        return meta.get("sensor", "COLOR")

    def _accept_barcode(self, node: Node, code: str) -> bool:
        p = node.params
        if p.get("regex"):
            try:
                if not re.fullmatch(p["regex"], code):
                    self.message.emit(f"Barcode '{code}' does not match the expected format.", "error")
                    return False
            except re.error:
                self.message.emit("Barcode format (regex) in this step is invalid - fix the job.", "error")
                return False
        if p.get("select_variant", True) and self.variants:
            matches = []
            for v in self.variants:
                rx = v.get("barcode_regex")
                if rx:
                    try:
                        if re.fullmatch(rx, code):
                            matches.append(v)
                    except re.error:
                        log.warning("Invalid regex on variant %s", v["code"])
            if len(matches) == 1:
                self.variant = matches[0]
            elif len(matches) > 1:
                self.message.emit("Barcode matches more than one variant - check variant patterns.", "error")
                return False
            elif self.variant is None and any(v.get("barcode_regex") for v in self.variants):
                self.message.emit(f"Barcode '{code}' does not belong to any variant of this job.", "error")
                return False
        policy = p.get("duplicate_policy", "STATION_DEFAULT")
        if policy == "STATION_DEFAULT":
            policy = self.ctx.settings.get("duplicate_policy", "BLOCK")
        if policy != "ALLOW":
            prev = self.ctx.last_pass_for_barcode(code)
            if prev:
                when = prev["started_at"].astimezone().strftime("%d-%m-%Y %H:%M")
                if policy == "BLOCK":
                    self.message.emit(f"Barcode {code} already PASSED on {when}. Re-inspection is blocked.", "error")
                    self.ctx.audit("DUPLICATE_BLOCKED", "barcode", code, {"previous_run": str(prev["id"])})
                    return False
                self.message.emit(f"Barcode {code} already passed on {when}.", "warn")
        self.barcode = code
        self.variables[p.get("variable") or "BARCODE"] = code
        if self.variant:
            self.variables["VARIANT"] = self.variant["code"]
        self.ctx.recorder.identity(self.run_id, code, self.variant["code"] if self.variant else None)
        self._complete(node, "OK", value=code)
        self.identity_changed.emit(self.identity())
        return True

    # ================================================================== results
    def _result_row(self, node: Node, result: str, *, score=None, qc_class=None, value=None, evidence=None,
                    actor=None, final=True) -> dict:
        now = _utc()
        det = self.ctx.engine.meta().get(node.step.get("detection_id"), {})
        return {"step_id": node.step.get("id"), "step_name": node.step["name"], "step_path": node.path(),
                "step_type": node.type, "detection_name": det.get("name"), "started_at": node.dt_start,
                "finished_at": now, "duration_ms": int((time.monotonic() - node.t_start) * 1000),
                "result": result, "qc_class": qc_class, "score": None if score is None else float(score),
                "value": value, "evidence_path": evidence, "actor_id": actor}

    def _complete(self, node: Node, result: str, **kw) -> None:
        node.state, node.result = DONE, result
        node.data["t_done"] = time.monotonic()
        self.ctx.recorder.step_result(self.run_id, self._result_row(node, result, **kw))

    def _finish(self, result: str, remarks: str | None = None) -> None:
        cycle_ms = int((time.monotonic() - self.run_t0) * 1000)
        self.ctx.recorder.finish_run(self.run_id, result, _utc(), cycle_ms, self.ng_count, self.forced_count, remarks)
        self.last_result = {"run_id": str(self.run_id), "result": result, "barcode": self.barcode,
                            "variant": self.variant["code"] if self.variant else None, "cycle_ms": cycle_ms,
                            "ng": self.ng_count, "forced": self.forced_count, "remarks": remarks}
        self.mode = "RESULT"
        self._result_until = time.monotonic() + self.ctx.settings.get("result_display_ms", 3000) / 1000.0
        self.ctx.engine.set_watch(self._always_watch())
        self.message.emit(f"{result}: {remarks}" if remarks else result,
                          "ok" if result == "PASS" else "error" if result == "FAIL" else "warn")
        self.tree_changed.emit()
        self.run_finished.emit(self.last_result)

    def _tick(self) -> None:
        if self.mode == "RUNNING":
            limit = float((self.job or {}).get("expected_cycle_s") or 0)
            if (limit and self.ctx.settings.get("overtime_warning", True) and not getattr(self, "_overtime", False)
                    and time.monotonic() - self.run_t0 > limit):
                self._overtime = True
                self.message.emit(f"Cycle time above the expected {int(limit)} s", "warn")
            self._advance()
        elif self.mode == "RESULT" and time.monotonic() >= self._result_until:
            self.mode = "READY"
            self.tree_changed.emit()
            if self.ctx.settings.get("auto_restart", True) and self.job is not None:
                self.start_run(self.variant["code"] if self.variant and len(self.variants) == 1 else None)

    def _check_reset_button(self) -> None:
        for det_id in self._always_watch():
            st = self.states.get(det_id)
            if st is None:
                continue
            prev = self._reset_counter.get(det_id)
            self._reset_counter[det_id] = st.counter
            if prev is not None and st.counter > prev and self.mode == "RUNNING":
                self.ctx.audit("RESET_BUTTON", "run", str(self.run_id), {})
                self.abort("Reset button pressed")
