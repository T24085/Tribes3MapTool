"""Validate conversion workflows and journal the real steps their operators run."""

from dataclasses import dataclass
from pathlib import Path
import re
from time import monotonic
from uuid import uuid4

if __package__:
    from .conversion_rules import checksum, read, write
    from .profile_exchange import active_profile
    from .source_profiles import NAMES, T2, config_name, selected_profile
else:
    from conversion_rules import checksum, read, write
    from profile_exchange import active_profile
    from source_profiles import NAMES, T2, config_name, selected_profile


DEFAULT = Path(__file__).with_name("tribes2_pipeline.json")
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
REQUIRED = {
    "reader": {"decode": ()},
    "draft": {
        "verify": (), "plan": ("verify",), "textures": ("plan",), "terrain": ("plan",),
        "objectives": ("terrain", "textures"), "lighting": ("terrain",), "save": ("objectives", "lighting"),
    },
    "host": {"preflight": (), "cook": ("preflight",), "package": ("cook",), "stage": ("package",)},
}
OPTIONAL = {
    "reader": {},
    "draft": {
        "weather": (("terrain",), "save"),
        "lighting-stills": (("save",), None),
        "showcase": (("save",), None),
        "contact-sheets": (("showcase",), None),
        "check-source-classes": (("verify",), "plan"),
        "check-ctf": (("objectives",), "save"),
    },
    "host": {"check-minimap": (("package",), "stage")},
}


def validate_pipeline(document):
    if (not isinstance(document, dict) or set(document) != {"format", "profile", "revision", *REQUIRED}
            or type(document["format"]) is not int or document["format"] != 1 or document["profile"] not in NAMES
            or type(document["revision"]) is not int or document["revision"] < 1):
        raise ValueError("Invalid map pipeline profile.")
    for phase, required in REQUIRED.items():
        steps = document[phase]
        if not isinstance(steps, list) or not len(required) <= len(steps) <= 48:
            raise ValueError(f"Invalid {phase} pipeline step count.")
        seen_ids, seen_operations = set(), set()
        for step in steps:
            if not isinstance(step, dict) or step.keys() - {"id", "operation", "label", "options"}:
                raise ValueError("Invalid pipeline step.")
            ident, operation, label = (step.get(key) for key in ("id", "operation", "label"))
            if (not isinstance(ident, str) or not ID.fullmatch(ident) or ident in seen_ids
                    or not isinstance(operation, str) or not isinstance(label, str)
                    or not label.isprintable() or not label.strip() or len(label) > 100):
                raise ValueError("Invalid or repeated pipeline step ID or label.")
            options = step.get("options", {})
            if operation == "check-source-classes":
                if (not isinstance(options, dict) or set(options) != {"classes"}
                        or not isinstance(options["classes"], list) or not 1 <= len(options["classes"]) <= 32
                        or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name)
                               for name in options["classes"])):
                    raise ValueError("Source class checks need a list of class names.")
            elif options != {}:
                raise ValueError(f"The {operation} step does not take options.")
            if operation in seen_operations and not operation.startswith("check-"):
                raise ValueError(f"Repeated pipeline operation: {operation}.")
            if operation in required:
                if not set(required[operation]) <= seen_operations:
                    raise ValueError(f"Pipeline step {operation} is out of dependency order.")
            elif operation in OPTIONAL[phase]:
                after, before = OPTIONAL[phase][operation]
                if not set(after) <= seen_operations or before in seen_operations:
                    raise ValueError(f"Pipeline step {operation} is out of dependency order.")
            else:
                raise ValueError(f"Unknown {phase} pipeline operation: {operation}.")
            seen_ids.add(ident)
            seen_operations.add(operation)
        if missing := required.keys() - seen_operations:
            raise ValueError(f"Missing required {phase} pipeline operations: {', '.join(sorted(missing))}.")
    return document


def pipeline_config(library=None, *, snapshot=None, recorded=False, source_profile=None, bundle=None):
    source_profile = selected_profile(source_profile, snapshot)
    if bundle is None and not recorded and snapshot is None:
        bundle = active_profile(library, source_profile)
    if snapshot is not None and "pipeline" in snapshot:
        document = snapshot["pipeline"]
    elif bundle and not recorded:
        document = bundle["pipeline"]
    else:
        override = library / config_name(source_profile, "pipeline") if library is not None else None
        if not recorded and override is not None and override.is_file():
            document = read(override, 64 * 1024)
        else:
            document = read(DEFAULT, 64 * 1024)
            if source_profile != T2:
                document["profile"] = source_profile
                document["reader"][0]["label"] = f"Read {NAMES[source_profile]} source"
                document["draft"] = [step for step in document["draft"] if step["operation"] != "weather"]
    document = validate_pipeline(document)
    if document["profile"] != source_profile:
        raise ValueError("The pipeline does not match the source profile.")
    return document


def operation_order(document, phase):
    return [(step["operation"], step.get("options", {})) for step in document[phase]]


@dataclass(frozen=True)
class Outcome:
    status: str
    detail: str


class PipelineRun:
    def __init__(self, document, phase, *, library=None, path=None, title="", draft=None):
        self.document = document
        self.path = path
        if self.path is None and library is not None:
            self.path = library / "PipelineRuns" / ("run-" + uuid4().hex[:12] + ".json")
        self.report = {"format": 1, "phase": phase, "title": title, "draft": draft,
                       "profile_sha256": checksum(document), "steps": [
            {**step, "status": "pending", "detail": "", "seconds": None} for step in document[phase]
        ]}

    def flush(self):
        if self.path is not None:
            write(self.path, self.report)

    def execute(self, operators):
        self.flush()
        for step in self.report["steps"]:
            step["status"] = "running"
            self.flush()
            start = monotonic()
            try:
                result = operators[step["operation"]](step.get("options", {}))
            except Exception as error:
                step.update(status="failed", detail=str(error)[-750:], seconds=round(monotonic()-start, 3))
                self.flush()
                raise
            if result is None:
                result = Outcome("passed", "")
            step.update(status=result.status, detail=result.detail, seconds=round(monotonic()-start, 3))
            self.flush()
            if result.status == "cancelled":
                return False
        return True
