"""Keep validated leaves when another answer in the same extraction is invalid."""

import copy
from pathlib import Path

from .atomic import subset
from .ledger import Ledger
from .storage import canonical, digest


class PartialAtomicFailure(ValueError):
    """An invalid extraction is retained as unknown, never silently accepted."""

    def __init__(self, raw, receipt, invalid):
        self.raw, self.receipt, self.invalid = raw, receipt, invalid
        self.result = None
        super().__init__("One or more atomic facts failed evidence validation")


class AtomicLeaves:
    """Independent immutable leaf records behind a bounded extraction batch."""

    def __init__(self, request, ledger, validator):
        self.request, self.validator = request, validator
        self.group = digest(request)
        self.store = Ledger(Path(ledger) / "atomic-leaves")
        self.definitions = request["schema"]["properties"]["finding"]["properties"][
            "facts"
        ]["properties"]
        self.values, self.keys, self.questions = {}, {}, []

    def child(self, ids):
        request = copy.deepcopy(self.request)
        request["context"]["atomic_ids"] = ids
        request["context"]["fixed_observations"] = {}
        finding = request["schema"]["properties"]["finding"]["properties"]
        finding["facts"]["properties"] = {
            k: v for k, v in self.definitions.items() if k in ids
        }
        finding["facts"]["required"] = ids
        return request

    def key_request(self, atom):
        return {"contract": "atomic-leaf-v1", "group": self.group, "atom": atom}

    def load(self, allowed=None):
        for atom in self.definitions:
            key = digest(self.key_request(atom))
            if allowed is not None and key not in allowed:
                continue
            path = self.store.root / "judgments" / key / "result.json"
            if not path.exists():
                continue

            def validate(value):
                self.validator(value, self.child([atom]))

            record, _ = self.store.decide(self.key_request(atom), None, validate)
            self.values[atom] = record["result"]["finding"]["facts"][atom]
            self.questions.extend(record["result"]["questions"])
            self.keys[atom] = key

    @property
    def missing(self):
        return sorted(set(self.definitions) - self.values.keys())

    def model_request(self):
        request = self.child(self.missing)
        request["criterion"] = subset(request["criterion"], self.missing)
        request["context"]["retained_atomic_observations"] = {
            **self.request["context"]["fixed_observations"],
            **self.values,
        }
        return request

    def result(self):
        facts = copy.deepcopy(self.values)
        for atom in self.missing:
            fact = {
                "value": None,
                "note": "유효한 사실 관측이 없어 확인이 필요합니다.",
                "evidence": [],
            }
            for field in ("claim_quote", "counter_quote"):
                if field in self.definitions[atom]["properties"]:
                    fact[field] = ""
            facts[atom] = fact
        questions = {canonical(q): q for q in self.questions}
        return {
            "questions": [questions[k] for k in sorted(questions)],
            "finding": {"id": self.request["criterion"]["id"], "facts": facts},
        }

    def receipt(self):
        record = {
            "request": self.request,
            "group": self.group,
            "leaves": dict(self.keys),
            "unresolved": self.missing,
        }
        record["id"] = digest(record)
        return record

    def accept(self, raw, attempt):
        if (
            set(raw) != {"questions", "finding"}
            or set(raw["finding"]) != {"id", "facts"}
            or raw["finding"]["id"] != self.request["criterion"]["id"]
            or set(raw["finding"]["facts"]) != set(self.missing)
        ):
            raise ValueError("Atomic extraction differs from its dispatched schema")
        invalid = []
        for atom in self.missing:
            value = {
                "questions": raw["questions"],
                "finding": {
                    "id": raw["finding"]["id"],
                    "facts": {atom: raw["finding"]["facts"][atom]},
                },
            }
            try:
                self.validator(value, self.child([atom]))
            except ValueError as error:
                invalid.append({"atom": atom, "error": str(error)})
                continue
            record, _ = self.store.decide(
                self.key_request(atom),
                lambda path: (value, {"source_attempt": str(attempt)}),
                lambda v: self.validator(v, self.child([atom])),
            )
            self.values[atom] = record["result"]["finding"]["facts"][atom]
            self.questions.extend(raw["questions"])
            self.keys[atom] = digest(self.key_request(atom))
        if invalid:
            raise PartialAtomicFailure(self.result(), self.receipt(), invalid)
        return self.result()

    @classmethod
    def restore(cls, receipt, ledger, validator):
        if receipt["id"] != digest(
            {k: v for k, v in receipt.items() if k != "id"}
        ) or receipt["group"] != digest(receipt["request"]):
            raise ValueError("Partial extraction receipt integrity mismatch")
        leaves = cls(receipt["request"], ledger, validator)
        leaves.load(set(receipt["leaves"].values()))
        if leaves.keys != receipt["leaves"] or leaves.missing != receipt["unresolved"]:
            raise ValueError("Partial leaf records differ from their frozen receipt")
        return leaves.result()
