"""Append-only per-worker memories, scoped retrieval and explicit peer handoff."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import re

from common import fingerprint, redact
from web_transport import canonical_url

WORKERS = ("A", "B", "C")
URL = re.compile(r"https://[^\s<>\]\)\"']+")


def terms(text):
    return set(re.findall(r"[a-z0-9가-힣_]{2,}", text.lower()))


class MemoryStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self, exclusive):
        with (self.directory / ".memory.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def snapshot(self):
        result = {worker: [] for worker in WORKERS}
        with self.lock(False):
            for worker in WORKERS:
                path = self.directory / f"{worker}.jsonl"
                if path.exists():
                    for line in path.read_text(encoding="utf-8").splitlines():
                        row = json.loads(line)
                        if row["owner"] != worker:
                            raise ValueError("memory owner does not match its file")
                        result[worker].append(row)
        return result

    def append(self, records):
        if any(row.get("owner") not in WORKERS or row.get("visibility") not in ("team", "private") for row in records):
            raise ValueError("invalid memory owner or visibility")
        with self.lock(True):
            for owner in WORKERS:
                rows = [row for row in records if row["owner"] == owner]
                if not rows:
                    continue
                path = self.directory / f"{owner}.jsonl"
                existing = {json.loads(line)["id"] for line in path.read_text().splitlines()} if path.exists() else set()
                with path.open("a", encoding="utf-8") as stream:
                    for row in rows:
                        if row["id"] not in existing:
                            stream.write(redact(json.dumps(row, ensure_ascii=False, allow_nan=False)) + "\n")
                            existing.add(row["id"])
                    stream.flush()


class MemoryContext:
    def __init__(self, store, scope, run_id, catalog, emit, top_k=3, ttl_days=7,
                 enabled=True, now=None):
        self.store, self.scope, self.run_id, self.catalog, self.emit = store, scope, run_id, catalog, emit
        self.top_k, self.ttl_days, self.enabled = top_k, ttl_days, enabled
        self.now = now or datetime.now(timezone.utc)
        self.snapshot = store.snapshot() if enabled else {w: [] for w in WORKERS}
        self.pending = []
        self.recalled, self.shared = set(), set()
        self.delivered = {w: set() for w in WORKERS}

    def retrieve(self, owner, query, team_only=False):
        if owner not in WORKERS:
            raise ValueError("unknown memory owner")
        query_terms = terms(query)
        candidates = []
        for row in self.snapshot[owner]:
            if row["scope"] != self.scope or row.get("retracted", False):
                continue
            if datetime.fromisoformat(row["expires_at"]) <= self.now:
                continue
            if team_only and row["visibility"] != "team":
                continue
            score = len(query_terms & terms(row["goal"] + " " + row["summary"] + " " + json.dumps(row["facts"], ensure_ascii=False)))
            if score:
                candidates.append((score, row["created_at"], row["id"], row))
        selected = [item[-1] for item in sorted(candidates, key=lambda x: x[:3], reverse=True)[:self.top_k]]
        result = []
        for row in selected:
            item = {key: row[key] for key in ("id", "owner", "scope", "created_at", "expires_at", "status", "run_id", "task_id")}
            item["goal"] = row["goal"][:500]
            item["summary"] = row["summary"][:700]
            item["facts"] = {k: v[:250] if isinstance(v, str) else v for k,v in list(row["facts"].items())[:16]}
            item["sources"] = [{k: s[k] for k in ("url", "title", "retrieved_at")} for s in row["sources"][:3]]
            result.append(item)
        return result

    def personal(self, worker, payload, task_id, phase):
        found = self.retrieve(worker, payload["task"]["goal"] + " " + payload["source"]["goal"])
        ids = [row["id"] for row in found]
        handoff_ids = [row["id"] for row in payload.get("handoff_memory", [])]
        self.recalled.update(ids)
        self.delivered[worker].update(ids + handoff_ids)
        self.emit("memory_delivery", task_id=task_id, worker=worker, phase=phase,
                  personal_ids=ids, handoff_ids=handoff_ids, snapshot_only=True)
        return found

    def handoff(self, requester, task, task_id):
        found = self.retrieve(requester, task.goal + " " + task.acceptance, team_only=True)
        self.shared.update(row["id"] for row in found)
        self.emit("memory_handoff", task_id=task_id, owner=requester,
                  memory_ids=[row["id"] for row in found], visibility="team")
        return found

    def remember(self, worker, task, task_id, artifact):
        if not self.enabled:
            return
        urls = {canonical_url(url) for url in URL.findall(json.dumps(artifact, ensure_ascii=False))}
        available = dict(self.catalog)
        for rows in self.snapshot.values():
            for row in rows:
                # Only team sources or the owner's own private sources may enter this worker's record.
                if row["id"] in self.delivered[worker] and datetime.fromisoformat(row["expires_at"]) > self.now:
                    for source in row["sources"]:
                        # A fresh read in this run takes precedence over the same URL in an older note.
                        available.setdefault(canonical_url(source["url"]), source)
        record = {"id": "mem-" + fingerprint([self.run_id, task_id, worker])[:16],
                  "owner": worker, "scope": self.scope, "visibility": "team", "status": "model_note_unverified",
                  "run_id": self.run_id, "task_id": task_id, "created_at": self.now.isoformat(),
                  "expires_at": (self.now + timedelta(days=self.ttl_days)).isoformat(),
                  "goal": task.goal[:1500], "summary": artifact["summary"][:1800],
                  "facts": dict(list(artifact["facts"].items())[:30]),
                  "sources": [available[url] for url in sorted(urls) if url in available],
                  "unverified_urls": sorted(urls - set(available))}
        self.pending.append(record)
        self.emit("memory_staged", owner=worker, task_id=task_id, memory_id=record["id"],
                  status=record["status"], source_count=len(record["sources"]))

    def commit(self):
        if self.enabled:
            self.store.append(self.pending)
        self.emit("memory_committed", records=len(self.pending),
                  counts={w: sum(r["owner"] == w for r in self.pending) for w in WORKERS})

    def counts(self):
        return {w: len(rows) for w, rows in self.snapshot.items()}
