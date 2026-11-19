"""Read cited public official documents; a plausible URL alone is not retrieval evidence."""
import asyncio
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from memory import URL
from web_transport import canonical_url, public_url


def official(url):
    if not public_url(url, ("github.com", "postgresql.org", "qdrant.tech")):
        return False
    host, path = urlparse(url).hostname, urlparse(url).path
    return ((host == "github.com" and (path == "/pgvector/pgvector" or path.startswith("/pgvector/pgvector/")))
            or (host in ("postgresql.org", "www.postgresql.org") and path.startswith("/docs/"))
            or (host in ("qdrant.tech", "www.qdrant.tech") and path.startswith("/documentation/")))


class OfficialRedirect(HTTPRedirectHandler):
    max_redirections = 3
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official(newurl):
            raise ValueError("redirect outside official documentation")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden, self.in_title, self.title, self.parts = 0, False, [], []
        self.refresh = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh":
            match = re.fullmatch(r"\s*0\s*;\s*url\s*=\s*(.+)", attrs.get("content", ""), re.I)
            if match:
                self.refresh = match[1].strip("\"'")
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, text):
        if not self.hidden and text.strip():
            self.parts.append(text.strip())
        if self.in_title:
            self.title.append(text.strip())


class SourceReader:
    def __init__(self, catalog, emit, config, opener=None):
        self.catalog, self.emit, self.config = catalog, emit, config
        self.opener = opener or build_opener(OfficialRedirect()).open
        self.slots, self.jobs = asyncio.Semaphore(config["max_parallel"]), {}
        self.requests = 0

    def read_page(self, url, deadline):
        req = Request(url, headers={"User-Agent": "AX-Course-Source-Verification/1.0", "Accept": "text/html,text/plain"})
        with self.opener(req, timeout=self.config["timeout_seconds"]) as response:
            if not official(response.geturl()):
                raise ValueError("response outside official documentation")
            content_type = response.headers.get("Content-Type", "")
            if not any(t in content_type for t in ("text/html", "text/plain")):
                raise ValueError("unsupported source content type")
            chunks, size = [], 0
            while True:
                chunk = response.read1(min(65536, self.config["max_bytes"] + 1 - size))
                if time.monotonic() > deadline:
                    raise TimeoutError("document deadline exceeded")
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > self.config["max_bytes"]:
                    raise ValueError("document size limit exceeded")
            raw = b"".join(chunks)
            page = PageText()
            page.feed(raw.decode("utf-8", errors="replace"))
            if page.refresh:
                target = urljoin(response.geturl(), page.refresh)
                if not official(target):
                    raise ValueError("HTML refresh outside official documentation")
                return {"refresh": target}
            text = re.sub(r"\s+", " ", " ".join(page.parts))
            if len(text) < 80:
                raise ValueError("document text is empty or too short")
            return {"url": url, "resolved_url": response.geturl(), "title": " ".join(page.title)[:300],
                    "excerpt": text[:700], "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "retrieval_method": "official_http_get", "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": size, "status": response.status}

    def read(self, url):
        deadline = time.monotonic() + self.config["timeout_seconds"]
        current, redirects = url, []
        for _ in range(3):
            result = self.read_page(current, deadline)
            if "refresh" not in result:
                return dict(result, url=url, html_redirects=redirects)
            current = result["refresh"]
            if current in redirects or current == url:
                raise ValueError("HTML refresh cycle")
            redirects.append(current)
        raise ValueError("HTML refresh limit exceeded")

    async def fetch(self, url, task_id, worker):
        async with self.slots:
            self.requests += 1
            self.emit("source_request", task_id=task_id, worker=worker, url=url)
            job = asyncio.create_task(asyncio.to_thread(self.read, url))
            try:
                source = await asyncio.shield(job)
                self.catalog[url] = source
                self.emit("source_response", task_id=task_id, worker=worker, source=source)
            except asyncio.CancelledError:
                try:
                    await job
                except Exception:
                    pass
                raise
            except Exception as exc:
                self.emit("source_error", task_id=task_id, worker=worker, url=url, error=type(exc).__name__ + ": " + str(exc)[:300])

    async def verify(self, artifact, task_id, worker):
        urls = sorted({canonical_url(u) for item in artifact["evidence"] for u in URL.findall(item)})
        pending = []
        for url in urls:
            if url in self.catalog:
                continue
            if not official(url):
                self.emit("source_rejected", task_id=task_id, worker=worker, url=url, reason="outside official documents")
                continue
            if url not in self.jobs:
                if len(self.jobs) >= self.config["max_requests"]:
                    self.emit("source_rejected", task_id=task_id, worker=worker, url=url, reason="request budget")
                    continue
                self.jobs[url] = asyncio.create_task(self.fetch(url, task_id, worker))
            pending.append(self.jobs[url])
        if pending:
            await asyncio.gather(*pending)
