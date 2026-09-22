"""Public task selection and private post-run evaluation data, loaded separately."""
import json
from pathlib import Path

from core import IDENTIFIER, Task, parse_artifact

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "cases/catalog.json"
DEFAULT_CASE = "release-review"


def catalog():
    entries = json.loads(CATALOG.read_text())
    if not isinstance(entries, list) or not entries:
        raise ValueError("case catalog must be a nonempty list")
    found = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "title", "case", "expected"}:
            raise ValueError("invalid case catalog fields")
        case_id = entry["id"]
        if not isinstance(case_id, str) or not IDENTIFIER.fullmatch(case_id) or case_id in found:
            raise ValueError("case IDs must be valid and unique")
        found.add(case_id)
        if not isinstance(entry["title"], str) or not entry["title"].strip():
            raise ValueError("case title required")
        for name in ("case", "expected"):
            if not isinstance(entry[name], str):
                raise ValueError("case paths must be strings")
            path = (ROOT / entry[name]).resolve()
            if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
                raise ValueError("case files must exist inside peer_dag")
    return entries


def load_case(case_id=DEFAULT_CASE):
    entry = next((item for item in catalog() if item["id"] == case_id), None)
    if entry is None:
        raise ValueError(f"unknown case: {case_id}")
    case_path, expected_path = (ROOT / entry[name] for name in ("case", "expected"))
    task = Task.parse(json.loads(case_path.read_text()), root=True)
    if task.id != case_id:
        raise ValueError("catalog and task IDs differ")
    expected = json.loads(expected_path.read_text())
    parse_artifact(json.dumps({"summary": "Private evaluation data validation", "facts": expected,
                              "evidence": ["Never passed to the model"]}, allow_nan=False))
    if not expected:
        raise ValueError("case needs at least one post-run check")
    return task, expected, (CATALOG, case_path, expected_path)


def select_case(mode, requested=None, replay=None):
    recorded = None
    if mode == "replay" and replay is not None:
        with replay.open() as stream:
            for line in stream:
                record = json.loads(line)
                if record.get("event") == "run_start":
                    recorded = record["settings"].get("case_id", DEFAULT_CASE)
                    break
        if recorded is None:
            raise ValueError("replay is missing case settings")
        if requested is not None and requested != recorded:
            raise ValueError("replay case differs from requested case")
    chosen = requested or recorded or DEFAULT_CASE
    if mode == "demo" and chosen != DEFAULT_CASE:
        raise ValueError("the scripted demo only supports release-review; use plan or live for other cases")
    return chosen
