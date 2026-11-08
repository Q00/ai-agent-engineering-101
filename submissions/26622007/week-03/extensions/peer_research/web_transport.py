"""OpenRouter server-side web search with original response and citation evidence."""
import asyncio
from datetime import datetime, timezone
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from common import CallError, ENDPOINT, redact


def public_url(url, domains):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and any(host == d or host.endswith("." + d) for d in domains))
    except (TypeError, ValueError):
        return False


def canonical_url(url):
    parsed = urlparse(url.rstrip(".,;"))
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm_") and k != "tab"]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"),
                       "", urlencode(query), ""))


class WebTransport:
    def __init__(self, key, config, search, emit, opener=urlopen, sleeper=time.sleep):
        self.key, self.config, self.search, self.emit = key, config, search, emit
        self.opener, self.sleeper = opener, sleeper
        self.http_requests = self.search_requests = self.cost_missing = 0
        self.search_usage_missing = 0
        self.cost = 0.0

    def request(self, messages, use_web, collect):
        body = {k: self.config[k] for k in ("model", "temperature", "max_tokens", "reasoning", "provider", "response_format")}
        body.update(messages=messages, stream=False)
        if use_web:
            body["tools"] = [{"type": "openrouter:web_search", "parameters": self.search}]
        for attempt in range(1, self.config["max_attempts"] + 1):
            deadline = time.monotonic() + self.config["response_deadline_seconds"]
            collect("http_request", attempt=attempt, payload=body)
            request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode(),
                              headers={"Authorization": "Bearer " + self.key,
                                       "Content-Type": "application/json",
                                       "X-OpenRouter-Title": "AX Peer Research"})
            try:
                with self.opener(request, timeout=self.config["timeout_seconds"]) as response:
                    chunks, size = [], 0
                    while True:
                        # read1 returns an available chunk, so heartbeat bytes cannot keep read-all alive forever.
                        chunk = response.read1(min(65536, 2_000_001 - size))
                        if time.monotonic() > deadline:
                            raise TimeoutError("response deadline exceeded")
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > 2_000_000:
                            raise CallError("response size limit exceeded")
                    raw = b"".join(chunks).decode("utf-8")
                collect("http_response", attempt=attempt, raw_response=redact(raw, self.key))
                data = json.loads(raw)
                if "error" in data:
                    raise CallError("provider error envelope; see original response")
                content = data["choices"][0]["message"].get("content")
                if not isinstance(content, str):
                    raise CallError("no text response; server tool may not have completed")
                return data
            except HTTPError as exc:
                with exc:
                    detail = redact(exc.read(10000).decode("utf-8", errors="replace"), self.key)
                collect("http_error", status=exc.code, detail=detail, attempt=attempt)
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == self.config["max_attempts"]:
                    raise CallError(f"HTTP {exc.code}; original error recorded") from None
            except (URLError, TimeoutError, OSError):
                collect("http_error", status="transport", attempt=attempt)
                if attempt == self.config["max_attempts"]:
                    raise CallError("transport error or timeout") from None
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                raise CallError("malformed provider response") from None
            self.sleeper(self.config["retry_delay_seconds"] * 2 ** (attempt - 1))
        raise CallError("attempt budget exhausted")

    async def complete(self, worker, phase, messages, task_id, use_web=False):
        records = []
        def collect(event, **fields):
            records.append((event, {"observed_at": datetime.now(timezone.utc).isoformat(), **fields}))
        job = asyncio.create_task(asyncio.to_thread(self.request, messages, use_web, collect))
        try:
            data = await asyncio.shield(job)
        except asyncio.CancelledError:
            try:
                await job
            except Exception:
                pass
            raise
        finally:
            for event, fields in records:
                self.emit(event, task_id=task_id, worker=worker, phase=phase, **fields)
                self.http_requests += event == "http_request"
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        if type(cost) in (int, float):
            self.cost += cost
        else:
            self.cost_missing += 1
        details = usage.get("server_tool_use_details") or usage.get("server_tool_use") or {}
        count = details.get("web_search_requests")
        if type(count) is int:
            self.search_requests += count
        elif use_web:
            self.search_usage_missing += 1
        message = data["choices"][0]["message"]
        citations = []
        for annotation in message.get("annotations") or []:
            citation = annotation.get("url_citation", {})
            if annotation.get("type") == "url_citation" and public_url(citation.get("url", ""), self.search["allowed_domains"]):
                citations.append({"url": citation["url"], "title": str(citation.get("title", "")),
                                  "excerpt": str(citation.get("content", ""))[:700],
                                  "retrieved_at": datetime.now(timezone.utc).isoformat()})
        self.emit("web_usage", task_id=task_id, worker=worker, phase=phase, requested=use_web,
                  reported_search_requests=count, citations=citations, usage=usage,
                  requested_max_uses=self.search["max_uses"] if use_web else None,
                  reported_above_requested_limit=bool(use_web and type(count) is int and count > self.search["max_uses"]),
                  model=data.get("model"), provider=data.get("provider"))
        return message["content"], citations
