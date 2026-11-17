"""Locked, immutable judgments and explicit instructor policy answers."""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from .storage import canonical, digest, read_json, write_new


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class UnfinishedJudgment(ValueError):
    """An earlier attempt is incomplete and must not trigger an implicit call."""

    def __init__(self, attempt):
        self.attempt = attempt
        super().__init__("Unfinished judgment; use explicit retry-failed")


class Ledger:
    """Cache successful validated results; failed attempts require explicit retry."""

    def __init__(self, root):
        self.root = Path(root)

    def decide(self, request, compute, validate, retry_failed=False):
        key = digest(request)
        root = self.root / "judgments" / key
        with locked(root / ".lock"):
            completed = root / "result.json"
            if completed.exists():
                record = read_json(completed)
                if record["request"] != request or record["id"] != digest(
                    {k: v for k, v in record.items() if k != "id"}
                ):
                    raise ValueError("Judgment ledger integrity mismatch")
                validate(record["result"])
                return record, True
            attempts = sorted(root.glob("attempt-*"))
            if attempts and not retry_failed:
                raise UnfinishedJudgment(attempts[-1])
            attempt = root / f"attempt-{len(attempts) + 1:03d}"
            write_new(attempt / "request.json", request)
            try:
                result, metadata = compute(attempt)
                validate(result)
            except Exception as error:
                write_new(
                    attempt / "failure.json", {"error_type": type(error).__name__}
                )
                raise
            record = {"request": request, "result": result, "metadata": metadata}
            record["id"] = digest(record)
            staged = attempt / "validated.json"
            write_new(staged, record)
            # Atomic visibility: a killed writer never leaves a partial cache hit.
            os.link(staged, completed)
            return record, False


class QuestionBox:
    def __init__(self, root):
        self.root = Path(root) / "questions"

    def equivalent_keys(self, key):
        keys = {key}
        links = [
            read_json(p)["questions"]
            for p in (self.root / "equivalences").glob("*.json")
        ]
        while True:
            extended = keys | {k for group in links if keys & set(group) for k in group}
            if extended == keys:
                return sorted(keys)
            keys = extended

    def effective_answer(self, key):
        answers = [
            read_json(self.root / k / "answer.json")
            for k in self.equivalent_keys(key)
            if (self.root / k / "answer.json").exists()
        ]
        if len({a["value"] for a in answers}) > 1:
            raise ValueError("Equivalent policies have conflicting answers")
        return answers[0] if answers else None

    def link(self, keys, reviewer):
        """Only an instructor can declare differently phrased questions equivalent."""
        if len(set(keys)) < 2 or not reviewer.strip():
            raise ValueError("Link needs at least two questions and a reviewer")
        with locked(self.root / ".policy.lock"):
            questions = [
                read_json(self.root / k / "question.json")
                for k in keys
                if len(k) == 64 and all(c in "0123456789abcdef" for c in k)
            ]
            if (
                len(questions) != len(keys)
                or len({tuple(sorted(q["options"])) for q in questions}) != 1
            ):
                raise ValueError("Equivalent questions need identical answer options")
            answers = [self.effective_answer(k) for k in keys]
            if len({a["value"] for a in answers if a}) > 1:
                raise ValueError("Resolve conflicting answers before linking questions")
            entry = {"questions": sorted(set(keys)), "reviewer": reviewer}
            path = self.root / "equivalences" / f"{digest(entry)}.json"
            if not path.exists():
                write_new(path, entry)
        return entry

    def register(self, question, task, waiter=None):
        key = digest(question)
        root = self.root / key
        with locked(root / ".lock"):
            path = root / "question.json"
            if not path.exists():
                write_new(path, question)
            waiting = {"task": task, "waiter": waiter or task}
            pending = root / "waiters" / f"{digest(waiting)}.json"
            if not pending.exists():
                write_new(pending, waiting)
        return key

    def policies(self, task):
        with locked(self.root / ".policy.lock"):
            return self._policies(task)

    def _policies(self, task):
        policies = {}
        for root in sorted(self.root.glob("*")):
            if not (root / "question.json").exists():
                continue
            if not any(
                read_json(p)["task"] == task for p in (root / "waiters").glob("*.json")
            ):
                continue
            question = read_json(root / "question.json")
            policies[question["policy"]] = {
                "question": question,
                "answer": self.effective_answer(root.name),
            }
        return policies

    def answer(self, key, value, reviewer):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Use the full question SHA256")
        with locked(self.root / ".policy.lock"):
            for linked in self.equivalent_keys(key):
                self.answer_one(linked, value, reviewer)

    def answer_one(self, key, value, reviewer):
        root = self.root / key
        with locked(root / ".lock"):
            question = read_json(root / "question.json")
            if value not in question["options"] or not reviewer.strip():
                raise ValueError("Answer must be an offered option with a reviewer")
            path = root / "answer.json"
            previous = read_json(path) if path.exists() else None
            if previous and previous["value"] == value:
                return
            answer = {"value": value, "reviewer": reviewer, "previous": previous}
            # Policy revisions are append-only; the pointer is an atomic view.
            write_new(root / "answers" / f"{digest(answer)}.json", answer)
            temporary = root / "answer.tmp"
            temporary.write_text(canonical(answer), encoding="utf-8")
            temporary.replace(path)

    def list(self):
        with locked(self.root / ".policy.lock"):
            return self._list()

    def tasks(self):
        return sorted(
            {read_json(p)["task"] for p in self.root.glob("*/waiters/*.json")}
        )

    def _list(self):
        return [
            {
                "id": root.name,
                "question": read_json(root / "question.json"),
                "answer": self.effective_answer(root.name),
                "equivalent_questions": self.equivalent_keys(root.name),
                "waiting_tasks": len(list((root / "waiters").glob("*.json"))),
            }
            for root in sorted(self.root.glob("*"))
            if (root / "question.json").exists()
        ]
