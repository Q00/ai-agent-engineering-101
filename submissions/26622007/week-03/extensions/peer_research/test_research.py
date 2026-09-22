import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from common import ROOT, Outcome, Task
from research_audit import evaluate
from memory import MemoryContext, MemoryStore
from source_reader import OfficialRedirect, PageText, SourceReader, official
from web_transport import WebTransport, canonical_url, public_url

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
SOURCE = {"url": "https://github.com/pgvector/pgvector", "title": "pgvector", "retrieved_at": NOW.isoformat()}


def note(owner="A", ident="mem-aaaaaaaaaaaaaaaa", visibility="team", **values):
    return dict({"id": ident, "owner": owner, "scope": "test", "visibility": visibility,
                 "created_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=7)).isoformat(),
                 "goal": "RAG pgvector 검토", "summary": "필터 품질 확인 필요", "facts": {"backup_owner": "플랫폼팀"},
                 "sources": [SOURCE], "status": "model_note_unverified", "run_id": "before", "task_id": "old"}, **values)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MemoryStore(self.tmp.name)
        self.events = []
        self.emit = lambda event, **kw: self.events.append(dict(event=event, **kw))
        self.task = Task("current", "RAG pgvector 변경", "백업 기록")

    def context(self, **kw):
        return MemoryContext(self.store, "test", "new", {}, self.emit, now=NOW, **kw)

    def test_private_and_scope_and_expiry_isolation(self):
        self.store.append([note(visibility="private"), note(ident="other", scope="elsewhere"),
                           note(ident="expired", expires_at=(NOW - timedelta(seconds=1)).isoformat())])
        ctx = self.context()
        self.assertEqual(len(ctx.retrieve("A", "RAG")), 1)
        self.assertEqual(ctx.retrieve("B", "RAG"), [])
        self.assertEqual(ctx.handoff("A", self.task, "current"), [])
        self.assertEqual(ctx.retrieve("A", "unrelatedxyz"), [])

    def test_handoff_has_owner_and_provenance(self):
        self.store.append([note()])
        ctx = self.context()
        packet = ctx.handoff("A", self.task, "current")
        personal = ctx.personal("B", {"task": asdict(self.task), "source": {"goal": "RAG"}, "handoff_memory": packet}, "current", "execute")
        self.assertEqual(personal, [])
        self.assertEqual(ctx.delivered["B"], {note()["id"]})
        self.assertEqual(packet[0]["owner"], "A")
        self.assertEqual(packet[0]["sources"][0]["retrieved_at"], NOW.isoformat())

    def test_staging_is_invisible_until_fresh_process_and_idempotent(self):
        ctx = self.context()
        ctx.remember("A", self.task, "current", {"summary": "RAG 검사", "facts": {}, "evidence": [SOURCE["url"]]})
        self.assertEqual(ctx.retrieve("A", "RAG"), [])
        ctx.commit()
        ctx.commit()
        self.assertEqual(ctx.retrieve("A", "RAG"), [])
        code = "from memory import MemoryStore; import sys; print(len(MemoryStore(sys.argv[1]).snapshot()['A']))"
        output = subprocess.check_output([sys.executable, "-c", code, self.tmp.name], cwd=ROOT, text=True)
        self.assertEqual(output.strip(), "1")

    def test_sources_require_actual_retrieval_or_delivered_memory(self):
        self.store.append([note(visibility="private")])
        ctx = self.context()
        artifact = {"summary": "RAG", "facts": {}, "evidence": [SOURCE["url"]]}
        ctx.remember("B", self.task, "b", artifact)
        self.assertEqual(ctx.pending[0]["sources"], [])
        self.assertEqual(ctx.pending[0]["unverified_urls"], [SOURCE["url"]])
        ctx.personal("A", {"task": asdict(self.task), "source": {"goal": "RAG"}}, "a", "execute")
        ctx.remember("A", self.task, "a", artifact)
        self.assertEqual(ctx.pending[1]["sources"], [SOURCE])

    def test_disabled_neither_reads_nor_writes_existing_store(self):
        self.store.append([note()])
        before = (Path(self.tmp.name) / "A.jsonl").read_bytes()
        ctx = self.context(enabled=False)
        self.assertEqual(ctx.counts(), {"A": 0, "B": 0, "C": 0})
        ctx.remember("A", self.task, "current", {"summary": "new", "facts": {}, "evidence": ["new"]})
        ctx.commit()
        self.assertEqual((Path(self.tmp.name) / "A.jsonl").read_bytes(), before)

    def test_fresh_source_timestamp_wins_over_recalled_source(self):
        self.store.append([note()])
        ctx = self.context()
        ctx.personal("A", {"task": asdict(self.task), "source": {"goal": "RAG"}}, "a", "execute")
        fresh = dict(SOURCE, retrieved_at=(NOW + timedelta(hours=1)).isoformat())
        ctx.catalog[SOURCE["url"]] = fresh
        ctx.remember("A", self.task, "a", {"summary": "RAG", "facts": {}, "evidence": [SOURCE["url"]]})
        self.assertEqual(ctx.pending[0]["sources"], [fresh])

    def test_invalid_owner_cannot_write_a_file(self):
        with self.assertRaises(ValueError):
            self.store.append([note(owner="../escape")])
        with self.assertRaises(ValueError):
            self.context().retrieve("../escape", "RAG")


class TransportTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "config.json").read_text())
        self.config, self.search = config["transport"], config["search"]
        self.records = []

    def response(self):
        return {"choices": [{"message": {"content": "{}", "annotations": [
            {"type": "url_citation", "url_citation": SOURCE},
            {"type": "url_citation", "url_citation": {"url": "https://evil.example/a"}}]}}],
            "usage": {"cost": 0.008, "server_tool_use_details": {"web_search_requests": 2}}}

    def transport(self, opener):
        return WebTransport("test-key", self.config, self.search,
                            lambda event, **kw: self.records.append(dict(event=event, **kw)), opener, lambda delay: None)

    def test_real_usage_shape_filters_sources_and_never_logs_auth_header(self):
        bodies = []
        def opener(req, timeout):
            bodies.append(json.loads(req.data))
            return io.BytesIO(json.dumps(self.response()).encode())
        t = self.transport(opener)
        text, citations = asyncio.run(t.complete("A", "execute", [], "task", True))
        self.assertEqual(t.http_requests, 1)
        self.assertEqual(t.search_requests, 2)
        self.assertEqual(t.cost, 0.008)
        self.assertEqual(len(citations), 1)
        self.assertFalse(self.records[-1]["reported_above_requested_limit"])
        self.assertEqual(bodies[0]["tools"][0]["type"], "openrouter:web_search")
        self.assertEqual(bodies[0]["response_format"], {"type": "json_object"})
        self.assertNotIn("test-key", json.dumps(self.records))
        self.assertNotIn("Authorization", json.dumps(self.records))

    def test_network_failure_retries_with_bounded_config(self):
        attempts = []
        def opener(req, timeout):
            attempts.append(timeout)
            if len(attempts) == 1:
                raise URLError("temporary")
            return io.BytesIO(json.dumps(self.response()).encode())
        t = self.transport(opener)
        asyncio.run(t.complete("A", "propose", [], "task"))
        self.assertEqual(t.http_requests, 2)
        self.assertEqual(attempts, [45, 45])

    def test_domains_and_canonical_urls(self):
        self.assertFalse(public_url("https://qdrant.tech.evil.example", self.search["allowed_domains"]))
        self.assertFalse(public_url("https://user:secret@qdrant.tech/a", self.search["allowed_domains"]))
        self.assertFalse(public_url("http://qdrant.tech/a", self.search["allowed_domains"]))
        self.assertEqual(canonical_url(SOURCE["url"] + "/?tab=readme-ov-file#filtering"), SOURCE["url"])

    def test_heartbeat_does_not_bypass_response_deadline(self):
        t = self.transport(lambda req, timeout: io.BytesIO(b" "))
        with patch("web_transport.time.monotonic", side_effect=[0, 91, 92, 184]):
            with self.assertRaisesRegex(Exception, "timeout"):
                t.request([], False, lambda *a, **kw: None)


class SourceTests(unittest.TestCase):
    def test_html_refresh_page_is_recognized(self):
        page = PageText()
        page.feed('<meta http-equiv=refresh content="0; url=https://qdrant.tech/documentation/search/search/">')
        self.assertEqual(page.refresh, "https://qdrant.tech/documentation/search/search/")

    def test_html_refresh_cycle_is_rejected(self):
        reader = SourceReader({}, lambda *a, **kw: None, {"max_parallel": 1, "timeout_seconds": 12})
        with patch.object(reader, "read_page", return_value={"refresh": SOURCE["url"]}):
            with self.assertRaisesRegex(ValueError, "cycle"):
                reader.read(SOURCE["url"])

    def test_official_boundary_and_redirect(self):
        self.assertTrue(official(SOURCE["url"]))
        self.assertFalse(official("https://github.com/attacker/docs"))
        self.assertFalse(official("https://qdrant.tech.evil.example/documentation/"))
        with self.assertRaises(ValueError):
            OfficialRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example")

    def test_fetch_is_real_evidence_and_cached(self):
        class Response(io.BytesIO):
            headers = {"Content-Type": "text/html"}
            status = 200
            def geturl(self):
                return SOURCE["url"]
        calls, catalog, events = [], {}, []
        def opener(req, timeout):
            calls.append(req.full_url)
            return Response(("<title>pgvector</title><script>ignore</script><p>" + "Vector filtering docs " * 20 + "</p>").encode())
        reader = SourceReader(catalog, lambda event, **kw: events.append(dict(event=event, **kw)),
                              {"max_parallel": 1, "max_requests": 2, "timeout_seconds": 12, "max_bytes": 2000}, opener)
        async def run():
            artifact = {"evidence": [SOURCE["url"], SOURCE["url"] + "#filtering", "https://evil.example"]}
            await reader.verify(artifact, "task", "A")
            await reader.verify(artifact, "task", "B")
        asyncio.run(run())
        self.assertEqual(calls, [SOURCE["url"]])
        self.assertEqual(catalog[SOURCE["url"]]["retrieval_method"], "official_http_get")
        self.assertNotIn("ignore", catalog[SOURCE["url"]]["excerpt"])
        self.assertTrue(any(e["event"] == "source_rejected" for e in events))


class AuditTests(unittest.TestCase):
    def test_unretrieved_url_and_invented_memory_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx = MemoryContext(MemoryStore(folder), "test", "run", {}, lambda *a, **kw: None, enabled=False)
            facts = {"recommended_storage": "pgvector", "internal_decision_id": "unknown", "backup_owner": "unknown",
                     "measured_latency": "not_measured", "unresolved": "test", "memory_refs": "mem-aaaaaaaaaaaaaaaa", "external_saas_allowed": False}
            outcome = Outcome("succeeded", "A", {"summary": "표|" * 700, "facts": facts, "evidence": [SOURCE["url"], "memory:mem-aaaaaaaaaaaaaaaa"]})
            result = evaluate(outcome, json.loads((ROOT / "expected.json").read_text()), ctx, {}, [], Task("root", "RAG", "check"), True)
            self.assertTrue(result["checks"]["decision_id"])
            self.assertFalse(result["checks"]["no_unretrieved_evidence_urls"])
            self.assertFalse(result["checks"]["memory_refs_delivered"])
            self.assertFalse(result["passed"])

    def test_followup_input_does_not_contain_answer_key(self):
        case = (ROOT / "cases/02-revise.json").read_text()
        expected = json.loads((ROOT / "expected.json").read_text())
        self.assertNotIn(expected["internal_decision_id"], case)
        self.assertNotIn(expected["backup_owner"], case)


if __name__ == "__main__":
    unittest.main()
