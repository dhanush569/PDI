"""Step type catalogue (all Arkite step types supported by the inspection module).

Each spec defines: label, whether it is a container, which detection types it accepts and the
editable parameters (key, label, kind, default, options).  kind: int|float|bool|str|choice|text
"""
from __future__ import annotations

CONTAINERS = ("NUMBERED", "COMPOSITE")

STEP_SPECS: dict[str, dict] = {
    "NUMBERED": {"label": "Numbered step", "container": True, "detection": None,
                 "help": "Logical operation shown to the operator as a numbered step; children run in order.",
                 "params": []},
    "COMPOSITE": {"label": "Composite step", "container": True, "detection": None,
                  "help": "Group of child steps executed in sequence or in any order, optionally repeated.",
                  "params": [("order", "Child order", "choice", "SEQUENTIAL", ["SEQUENTIAL", "ANY_ORDER"]),
                             ("repeat", "Repeat count", "int", 1, (1, 100))]},
    "PLACE": {"label": "Place object", "container": False, "detection": ("OBJECT",),
              "help": "Completes when the object is detected in its zone.",
              "params": [("timeout_s", "Warn after (s, 0 = never)", "int", 0, (0, 3600))]},
    "TAKE": {"label": "Take object", "container": False, "detection": ("OBJECT",),
             "help": "Completes when the object has been removed from its zone.",
             "params": [("timeout_s", "Warn after (s, 0 = never)", "int", 0, (0, 3600))]},
    "ACTIVITY": {"label": "Activity", "container": False, "detection": ("ACTIVITY",),
                 "help": "Completes when the operator performs the activity in the zone.",
                 "params": [("count", "Required repetitions", "int", 1, (1, 50)),
                            ("timeout_s", "Warn after (s, 0 = never)", "int", 0, (0, 3600))]},
    "PRESS": {"label": "Press virtual button", "container": False, "detection": ("BUTTON",),
              "help": "Completes when the projected button is pressed.", "params": []},
    "QUALITY_CHECK": {"label": "Quality check", "container": False, "detection": ("QUALITY",),
                      "help": "Vision check against taught classes. Choose what happens on a bad result.",
                      "params": [("on_fail", "On bad / missing result", "choice", "REWORK",
                                  ["REWORK", "FAIL_RUN", "ASK_SUPERVISOR"]),
                                 ("save_evidence", "Save evidence image", "bool", True, None),
                                 ("timeout_s", "Warn after (s, 0 = never)", "int", 0, (0, 3600))]},
    "WAIT_BARCODE": {"label": "Scan barcode", "container": False, "detection": None,
                     "help": "Waits for a scan. Can select the job variant from the barcode pattern.",
                     "params": [("variable", "Store in variable", "str", "BARCODE", None),
                                ("select_variant", "Select variant from barcode", "bool", True, None),
                                ("regex", "Accept only (regex, empty = any)", "str", "", None),
                                ("duplicate_policy", "Already passed barcode", "choice", "STATION_DEFAULT",
                                 ["STATION_DEFAULT", "BLOCK", "WARN", "ALLOW"])]},
    "SNAPSHOT": {"label": "Snapshot", "container": False, "detection": None,
                 "help": "Stores an evidence image of the bench for traceability.",
                 "params": [("source", "Camera", "choice", "COLOR", ["COLOR", "VISION", "IR"])]},
    "SET_VARIABLE": {"label": "Set variable", "container": False, "detection": None,
                     "help": "Sets a variable; {{NAME}} inserts another variable. Optionally logged as a result.",
                     "params": [("variable", "Variable", "str", "RESULT", None),
                                ("value", "Value", "str", "PASS", None),
                                ("record", "Record in traceability", "bool", True, None)]},
    "DELAY": {"label": "Delay", "container": False, "detection": None, "help": "Waits a fixed time.",
              "params": [("ms", "Duration (ms)", "int", 1000, (0, 600000))]},
    "OPERATOR_DIALOG": {"label": "Operator decision", "container": False, "detection": None,
                        "help": "Asks the operator to choose (e.g. gauge OK / NOT OK). NG options fail the part.",
                        "params": [("question", "Question", "str", "Gauge result?", None),
                                   ("options", "Options (comma separated)", "str", "OK,NOT OK", None),
                                   ("ng_options", "Options that mean NG", "str", "NOT OK", None),
                                   ("variable", "Store answer in variable", "str", "", None)]},
    "WORK_INSTRUCTION": {"label": "Work instruction", "container": False, "detection": None,
                         "help": "Shows the instruction until the operator confirms on screen.", "params": []},

    # ---------------------------------------------------------------- General (extra)
    "EXECUTE_TASK": {"label": "Execute task", "container": False, "detection": None,
                     "help": "Runs another task or process step immediately.",
                     "params": [("task_id", "Task identifier", "str", "", None)]},
    "JOB_CONTROL": {"label": "Job control", "container": False, "detection": None,
                    "help": "Controls job flow: pause, abort, repeat, or navigate.",
                    "params": [("action", "Action", "choice", "PAUSE", ["PAUSE", "ABORT", "REPEAT", "RESTART"])]},
    "PROCESS_CONTROL": {"label": "Process control", "container": False, "detection": None,
                        "help": "Signals an external process or PLC.",
                        "params": [("signal", "Signal name", "str", "", None),
                                   ("value", "Value", "str", "1", None)]},
    "RESETTER": {"label": "Resetter", "container": False, "detection": None,
                 "help": "Resets the state of one or more detections.",
                 "params": []},
    "SWITCH_JOB_OPTION": {"label": "Switch job option", "container": False, "detection": None,
                           "help": "Branches the run based on the selected job option/variant.",
                           "params": [("option", "Option name", "str", "", None)]},
    "SWITCH_STEP": {"label": "Switch step", "container": False, "detection": None,
                    "help": "Conditionally jumps to a different step based on a variable value.",
                    "params": [("variable", "Variable", "str", "", None),
                               ("value", "Match value", "str", "", None)]},

    # ---------------------------------------------------------------- Variables
    "COUNTER": {"label": "Counter", "container": False, "detection": None,
                "help": "Increments or resets a numeric counter variable.",
                "params": [("variable", "Counter variable", "str", "COUNT", None),
                           ("action", "Action", "choice", "INCREMENT", ["INCREMENT", "RESET"]),
                           ("limit", "Alert at (0 = never)", "int", 0, (0, 99999))]},
    "FIND_REPLACE_STRING": {"label": "Find and Replace String", "container": False, "detection": None,
                             "help": "Finds and replaces text in a variable.",
                             "params": [("variable", "Variable", "str", "", None),
                                        ("find", "Find", "str", "", None),
                                        ("replace", "Replace with", "str", "", None)]},
    "FUNCTION": {"label": "Function", "container": False, "detection": None,
                 "help": "Evaluates a mathematical or string function and stores the result.",
                 "params": [("expression", "Expression", "str", "", None),
                            ("result_variable", "Store result in", "str", "RESULT", None)]},
    "SPLIT_STRING_INDEX": {"label": "Split String by Index", "container": False, "detection": None,
                            "help": "Splits a string at a fixed character index.",
                            "params": [("variable", "Variable", "str", "", None),
                                       ("index", "Split at index", "int", 0, (0, 9999)),
                                       ("left_var", "Left part variable", "str", "LEFT", None),
                                       ("right_var", "Right part variable", "str", "RIGHT", None)]},
    "SPLIT_STRING_ARG": {"label": "Split String by Argument", "container": False, "detection": None,
                          "help": "Splits a string using a delimiter character.",
                          "params": [("variable", "Variable", "str", "", None),
                                     ("delimiter", "Delimiter", "str", ",", None),
                                     ("index", "Part index (0-based)", "int", 0, (0, 100))]},
    "STRING_FORMATTER": {"label": "String Formatter", "container": False, "detection": None,
                          "help": "Formats a string template with variable substitution.",
                          "params": [("template", "Template (use {{VAR}})", "str", "", None),
                                     ("result_variable", "Store result in", "str", "RESULT", None)]},
    "STRING_PARSER": {"label": "String Parser", "container": False, "detection": None,
                      "help": "Parses a string using a regex and captures named groups as variables.",
                      "params": [("variable", "Variable", "str", "", None),
                                 ("regex", "Regex pattern", "str", "", None)]},
    "VARIABLE_CHANGED": {"label": "Variable Changed", "container": False, "detection": None,
                          "help": "Waits until a variable changes value.",
                          "params": [("variable", "Variable", "str", "", None),
                                     ("timeout_s", "Timeout (s, 0 = never)", "int", 0, (0, 3600))]},
    "VARIABLE_LOGGER": {"label": "Variable Logger", "container": False, "detection": None,
                         "help": "Records the current value of one or more variables for traceability.",
                         "params": [("variables", "Variables (comma-separated)", "str", "", None)]},

    # ---------------------------------------------------------------- Screen Buttons
    "BACK": {"label": "Back", "container": False, "detection": None,
             "help": "Shows a 'Back' button on the operator screen.", "params": []},
    "JOB_CLOSE_BUTTON": {"label": "Job Close Button", "container": False, "detection": None,
                          "help": "Shows a button allowing the operator to close/end the job.", "params": []},
    "NEXT_BUTTON": {"label": "Next Button", "container": False, "detection": None,
                    "help": "Shows a 'Next' button that advances to the next step.", "params": []},
    "PREVIOUS_BUTTON": {"label": "Previous Button", "container": False, "detection": None,
                         "help": "Shows a 'Previous' button that returns to the prior step.", "params": []},
    "START": {"label": "Start", "container": False, "detection": None,
              "help": "Shows a 'Start' button to begin the job run.", "params": []},

    # ---------------------------------------------------------------- Communication
    "SEND_COMMUNICATION": {"label": "Send Communication", "container": False, "detection": None,
                            "help": "Sends a message, e-mail, or external notification.",
                            "params": [("channel", "Channel", "choice", "EMAIL",
                                        ["EMAIL", "WEBHOOK", "TCP", "MQTT"]),
                                       ("destination", "Destination / URL", "str", "", None),
                                       ("message", "Message body", "text", "", None)]},
    "WAIT_COMMUNICATION": {"label": "Wait Communication", "container": False, "detection": None,
                            "help": "Pauses until an incoming message or signal is received.",
                            "params": [("channel", "Channel", "choice", "WEBHOOK",
                                        ["WEBHOOK", "TCP", "MQTT"]),
                                       ("variable", "Store payload in variable", "str", "", None),
                                       ("timeout_s", "Timeout (s, 0 = never)", "int", 0, (0, 3600))]},

    # ---------------------------------------------------------------- Extra / File
    "FILE_MOVER": {"label": "File mover", "container": False, "detection": None,
                   "help": "Moves or copies a file on the local filesystem.",
                   "params": [("source", "Source path (supports {{VAR}})", "str", "", None),
                               ("destination", "Destination path", "str", "", None),
                               ("action", "Action", "choice", "MOVE", ["MOVE", "COPY"])]},
    "READ_FROM_FILE": {"label": "Read from File", "container": False, "detection": None,
                       "help": "Reads content from a file and stores it in a variable.",
                       "params": [("path", "File path", "str", "", None),
                                  ("variable", "Store in variable", "str", "FILE_CONTENT", None)]},
    "SOUND": {"label": "Sound", "container": False, "detection": None,
              "help": "Plays an audio file or a built-in alert tone.",
              "params": [("sound", "Sound", "choice", "BEEP", ["BEEP", "OK", "NG", "ALERT", "CUSTOM"]),
                         ("path", "Custom file path", "str", "", None)]},
    "WRITE_TO_FILE": {"label": "Write to File", "container": False, "detection": None,
                      "help": "Writes a value or variable to a text file (append or overwrite).",
                      "params": [("path", "File path", "str", "", None),
                                 ("content", "Content (supports {{VAR}})", "str", "", None),
                                 ("mode", "Write mode", "choice", "APPEND", ["APPEND", "OVERWRITE"])]},
}

LEAF_TYPES = tuple(k for k, v in STEP_SPECS.items() if not v["container"])


def default_params(step_type: str) -> dict:
    return {key: default for key, _, _, default, _ in STEP_SPECS[step_type]["params"]}


def default_name(step_type: str, detection_name: str | None = None) -> str:
    verb = {"PLACE": "Place", "TAKE": "Take", "ACTIVITY": "Activity", "PRESS": "Press",
            "QUALITY_CHECK": "Check"}.get(step_type)
    if verb and detection_name:
        return f"{verb} {detection_name}"
    return STEP_SPECS[step_type]["label"]


def operator_text(step: dict) -> str:
    """Instruction shown/projected for a leaf step when no custom instruction is set."""
    if step.get("instruction"):
        return step["instruction"]
    d = step.get("detection_name") or ""
    t = step["step_type"]
    p = step.get("params") or {}
    return {
        "PLACE": f"Place {d}", "TAKE": f"Take {d}", "ACTIVITY": f"Perform {d}", "PRESS": f"Press {d}",
        "QUALITY_CHECK": f"Check {d}", "WAIT_BARCODE": "Scan the part barcode", "SNAPSHOT": "Capturing image",
        "DELAY": "Please wait", "OPERATOR_DIALOG": p.get("question", "Choose"), "WORK_INSTRUCTION": step["name"],
        "SET_VARIABLE": step["name"],
    }.get(t, step["name"])
