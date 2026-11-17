"""Unit tests for all Arkite step types and execution modes in Executor."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication

from vista.engine.executor import Executor, Node, ACTIVE, DONE, SKIPPED, PENDING
from vista.vision.detectors import DetState


class DummyRecorder:
    def start_run(self, *a, **k): pass
    def identity(self, *a, **k): pass
    def step_result(self, *a, **k): pass
    def finish_run(self, *a, **k): pass


class DummyEngine:
    def set_watch(self, *a, **k): pass
    def meta(self): return {}


class DummySession:
    user_id = 1
    username = "admin"


class DummyCtx:
    def __init__(self):
        self.settings = {"reset_enabled": True}
        self.audits = []
        self.recorder = DummyRecorder()
        self.engine = DummyEngine()
        self.session = DummySession()

    def audit(self, action, target_type, target_id, details=None):
        self.audits.append((action, target_type, target_id, details))


def make_executor():
    app = QCoreApplication.instance() or QCoreApplication([])
    ctx = DummyCtx()
    ex = Executor(ctx)
    return ex


def test_composite_one_arbitrary():
    ex = make_executor()
    root_step = {"id": 1, "name": "Composite One", "step_type": "COMPOSITE", "params": {"order": "ONE_ARBITRARY"}, "parent_id": None, "sort_order": 0}
    c1_step = {"id": 2, "name": "Child 1", "step_type": "COUNTER", "params": {"variable": "C1", "action": "INCREMENT"}, "parent_id": 1, "sort_order": 0}
    c2_step = {"id": 3, "name": "Child 2", "step_type": "WORK_INSTRUCTION", "params": {}, "parent_id": 1, "sort_order": 1}

    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], [root_step, c1_step, c2_step])
    ex.start_run()
    # Child 1 (COUNTER) executes immediately on tick, completing Child 1 and thus completing the ONE_ARBITRARY composite
    ex._advance()
    assert ex.variables.get("C1") == "1"
    assert ex.mode == "RESULT" or ex.root.state == DONE
    assert ex.root.children[0].children[0].state == DONE
    assert ex.root.children[0].children[1].state == SKIPPED


def test_composite_any_order():
    ex = make_executor()
    root_step = {"id": 1, "name": "Composite Any", "step_type": "COMPOSITE", "params": {"order": "ANY_ORDER"}, "parent_id": None, "sort_order": 0}
    c1_step = {"id": 2, "name": "Child 1", "step_type": "COUNTER", "params": {"variable": "C1", "action": "INCREMENT"}, "parent_id": 1, "sort_order": 0}
    c2_step = {"id": 3, "name": "Child 2", "step_type": "COUNTER", "params": {"variable": "C2", "action": "INCREMENT"}, "parent_id": 1, "sort_order": 1}

    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], [root_step, c1_step, c2_step])
    ex.start_run()
    ex._advance()
    assert ex.variables.get("C1") == "1"
    assert ex.variables.get("C2") == "1"
    assert ex.mode == "RESULT" or ex.root.state == DONE


def test_composite_simultaneous():
    ex = make_executor()
    root_step = {"id": 1, "name": "Composite Sim", "step_type": "COMPOSITE", "params": {"order": "SIMULTANEOUS"}, "parent_id": None, "sort_order": 0}
    c1_step = {"id": 2, "name": "Child 1", "step_type": "COUNTER", "params": {"variable": "C1", "action": "INCREMENT"}, "parent_id": 1, "sort_order": 0}
    c2_step = {"id": 3, "name": "Child 2", "step_type": "COUNTER", "params": {"variable": "C2", "action": "INCREMENT"}, "parent_id": 1, "sort_order": 1}

    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], [root_step, c1_step, c2_step])
    ex.start_run()
    ex._advance()
    assert ex.variables.get("C1") == "1"
    assert ex.variables.get("C2") == "1"
    assert ex.mode == "RESULT" or ex.root.state == DONE


def test_variables_execution():
    ex = make_executor()
    steps = [
        {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
        {"id": 2, "name": "Set", "step_type": "SET_VARIABLE", "params": {"variable": "RAW", "value": "A,B,C"}, "parent_id": 1, "sort_order": 0},
        {"id": 3, "name": "SplitArg", "step_type": "SPLIT_STRING_ARG", "params": {"variable": "RAW", "delimiter": ",", "index": 1}, "parent_id": 1, "sort_order": 1},
        {"id": 4, "name": "SplitIdx", "step_type": "SPLIT_STRING_INDEX", "params": {"variable": "RAW", "index": 2, "left_var": "L", "right_var": "R"}, "parent_id": 1, "sort_order": 2},
        {"id": 5, "name": "Fmt", "step_type": "STRING_FORMATTER", "params": {"template": "Val_{{RAW}}", "result_variable": "FMT"}, "parent_id": 1, "sort_order": 3},
        {"id": 6, "name": "Repl", "step_type": "FIND_REPLACE_STRING", "params": {"variable": "FMT", "find": "Val_", "replace": "Final_"}, "parent_id": 1, "sort_order": 4},
        {"id": 7, "name": "Func", "step_type": "FUNCTION", "params": {"expression": "10 * 5 + 3", "result_variable": "MATH"}, "parent_id": 1, "sort_order": 5},
        {"id": 8, "name": "CntInc", "step_type": "COUNTER", "params": {"variable": "NUM", "action": "INCREMENT"}, "parent_id": 1, "sort_order": 6},
        {"id": 9, "name": "CntRst", "step_type": "COUNTER", "params": {"variable": "NUM", "action": "RESET"}, "parent_id": 1, "sort_order": 7},
    ]
    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], steps)
    ex.start_run()
    for _ in range(15):
        ex._advance()

    assert ex.variables.get("RAW") == "A,B,C"
    assert ex.variables.get("RAW_PART_1") == "B"
    assert ex.variables.get("L") == "A,"
    assert ex.variables.get("R") == "B,C"
    assert ex.variables.get("FMT") == "Final_A,B,C"
    assert ex.variables.get("MATH") == "53"
    assert ex.variables.get("NUM") == "0"


def test_file_operations():
    ex = make_executor()
    with tempfile.TemporaryDirectory() as td:
        test_file = os.path.join(td, "test.txt")
        copy_file = os.path.join(td, "copy.txt")
        steps = [
            {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
            {"id": 2, "name": "Write", "step_type": "WRITE_TO_FILE", "params": {"path": test_file, "content": "Hello Vista"}, "parent_id": 1, "sort_order": 0},
            {"id": 3, "name": "Read", "step_type": "READ_FROM_FILE", "params": {"path": test_file, "variable": "FILE_VAL"}, "parent_id": 1, "sort_order": 1},
            {"id": 4, "name": "Move", "step_type": "FILE_MOVER", "params": {"source": test_file, "destination": copy_file, "action": "COPY"}, "parent_id": 1, "sort_order": 2},
        ]
        ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], steps)
        ex.start_run()
        for _ in range(6):
            ex._advance()

        assert ex.variables.get("FILE_VAL") == "Hello Vista"
        assert os.path.exists(copy_file)
        with open(copy_file, "r") as f:
            assert f.read() == "Hello Vista"


def test_screen_buttons():
    ex = make_executor()
    steps = [
        {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
        {"id": 2, "name": "NextBtn", "step_type": "NEXT_BUTTON", "params": {}, "parent_id": 1, "sort_order": 0},
        {"id": 3, "name": "WorkInst", "step_type": "WORK_INSTRUCTION", "params": {}, "parent_id": 1, "sort_order": 1},
    ]
    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], steps)
    ex.start_run()
    ex._advance()

    leaves = ex.active_leaves()
    assert len(leaves) == 1 and leaves[0].type == "NEXT_BUTTON"
    ex.confirm_instruction()
    ex._advance()

    leaves = ex.active_leaves()
    assert len(leaves) == 1 and leaves[0].type == "WORK_INSTRUCTION"
    ex.confirm_instruction()
    ex._advance()
    assert ex.mode == "RESULT"


def test_expected_cycle_time_variables():
    ex = make_executor()
    job = {
        "id": 10,
        "code": "TEST",
        "name": "Test Job",
        "revision": 1,
        "status": "RELEASED",
        "settings": {"cycle_time_expected_s": 75},
    }
    steps = [
        {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
        {"id": 2, "name": "Work", "step_type": "WORK_INSTRUCTION", "params": {}, "parent_id": 1, "sort_order": 0},
    ]
    ex.load_job(job, [], steps)
    ex.start_run()
    assert ex.variables.get("Expected Cycle Time") == "00:01:15"
    assert ex.variables.get("Expected Cycle Time Sec") == "75"


def test_counter_decrement():
    ex = make_executor()
    steps = [
        {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
        {"id": 2, "name": "Set", "step_type": "SET_VARIABLE", "params": {"variable": "ITEMS", "value": "5"}, "parent_id": 1, "sort_order": 0},
        {"id": 3, "name": "Dec", "step_type": "COUNTER", "params": {"variable": "ITEMS", "action": "DECREMENT"}, "parent_id": 1, "sort_order": 1},
    ]
    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], steps)
    ex.start_run()
    for _ in range(5):
        ex._advance()
    assert ex.variables.get("ITEMS") == "4"


def test_split_string_extended():
    ex = make_executor()
    steps = [
        {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
        {"id": 2, "name": "Set", "step_type": "SET_VARIABLE", "params": {"variable": "TXT", "value": "PART-12345-REV2"}, "parent_id": 1, "sort_order": 0},
        {"id": 3, "name": "SplitIdx", "step_type": "SPLIT_STRING_INDEX", "params": {"variable": "TXT", "index": 5, "length": 5, "left_var": "EXTRACT", "right_var": "REST"}, "parent_id": 1, "sort_order": 1},
        {"id": 4, "name": "SplitArg", "step_type": "SPLIT_STRING_ARG", "params": {"variable": "TXT", "delimiter": "-", "index": 1, "result_variable": "MID", "remainder_variable": "REM"}, "parent_id": 1, "sort_order": 2},
    ]
    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], steps)
    ex.start_run()
    for _ in range(8):
        ex._advance()
    assert ex.variables.get("EXTRACT") == "12345"
    assert ex.variables.get("REST") == "-REV2"
    assert ex.variables.get("MID") == "12345"
    assert ex.variables.get("REM") == "REV2"


def test_material_picking_step():
    ex = make_executor()
    steps = [
        {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
        {"id": 2, "name": "PickMat", "step_type": "MATERIAL", "params": {"detection_id": 101, "items_count": 2}, "parent_id": 1, "sort_order": 0},
    ]
    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], steps)
    ex.start_run()
    ex._advance()

    leaf = ex.active_leaves()[0]
    assert leaf.type == "MATERIAL"
    assert leaf.state == ACTIVE

    # Simulate 1 pick - should remain ACTIVE
    st = DetState(det_id=101, name="Bin_A", det_type="PICKING_BIN", state="IDLE", counter=1)
    ex.on_states({101: st})
    assert leaf.state == ACTIVE

    # Simulate 2nd pick - should complete step
    st2 = DetState(det_id=101, name="Bin_A", det_type="PICKING_BIN", state="IDLE", counter=2)
    ex.on_states({101: st2})
    assert leaf.state == DONE


def test_variable_changed_condition():
    ex = make_executor()
    steps = [
        {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
        {"id": 2, "name": "SetInit", "step_type": "SET_VARIABLE", "params": {"variable": "STATUS", "value": "READY"}, "parent_id": 1, "sort_order": 0},
        {"id": 3, "name": "WaitChange", "step_type": "VARIABLE_CHANGED", "params": {"variable": "STATUS", "condition": "EQUALS", "target_value": "GO"}, "parent_id": 1, "sort_order": 1},
    ]
    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], steps)
    ex.start_run()
    for _ in range(3):
        ex._advance()

    leaves = ex.active_leaves()
    assert len(leaves) == 1 and leaves[0].type == "VARIABLE_CHANGED"
    assert leaves[0].state == ACTIVE

    # Update variable to GO
    ex.variables["STATUS"] = "GO"
    ex._advance()
    assert leaves[0].state == DONE


def test_job_control_actions():
    # Test GO_TO_STEP
    ex = make_executor()
    steps = [
        {"id": 1, "name": "Num", "step_type": "NUMBERED", "params": {}, "parent_id": None, "sort_order": 0},
        {"id": 2, "name": "Step1", "step_type": "JOB_CONTROL", "params": {"action": "GO_TO_STEP", "target_step": "Step3"}, "parent_id": 1, "sort_order": 0},
        {"id": 3, "name": "Step2", "step_type": "COUNTER", "params": {"variable": "SKIPPED_VAR", "action": "INCREMENT"}, "parent_id": 1, "sort_order": 1},
        {"id": 4, "name": "Step3", "step_type": "SET_VARIABLE", "params": {"variable": "REACHED", "value": "YES"}, "parent_id": 1, "sort_order": 2},
    ]
    ex.load_job({"id": 10, "code": "TEST", "name": "Test Job", "revision": 1, "status": "RELEASED"}, [], steps)
    ex.start_run()
    for _ in range(5):
        ex._advance()
    assert ex.variables.get("REACHED") == "YES"
    assert "SKIPPED_VAR" not in ex.variables


if __name__ == "__main__":
    test_composite_one_arbitrary()
    test_composite_any_order()
    test_composite_simultaneous()
    test_variables_execution()
    test_file_operations()
    test_screen_buttons()
    test_expected_cycle_time_variables()
    test_counter_decrement()
    test_split_string_extended()
    test_material_picking_step()
    test_variable_changed_condition()
    test_job_control_actions()
    print("ALL STEPS EXECUTOR TESTS PASSED!")
