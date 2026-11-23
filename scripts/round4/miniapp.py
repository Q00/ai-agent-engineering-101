"""The mini app: the agent lives in an app where people chat with it, while it keeps trading on the market.

    python scripts/agent_kit.py run ... --app 8800     then open http://127.0.0.1:8800/
    ... --app 8800 --app-tunnel                         a public https URL for a phone (Android, iOS)
    ... --app 8800 --app-host 0.0.0.0                   reachable from a phone on the same Wi-Fi

The page is a web app: on a phone it opens in the browser and installs to the home screen (Android: Chrome menu,
Install app; iOS: Safari, Share, Add to Home Screen). Once the app is reachable from outside this computer, every
request needs the app's access code (.kit/app.key): the kit prints a link that carries it, the browser keeps it in a
cookie, and a page opened without it asks for it. The agent never sees the code.

What the page does:
  - people chat with the agent (each message reaches the agent; it answers with reply),
  - the agent may speak first: app_say puts a message in the chat that nobody asked for (an offer, a warning, news
    from the market), at most once every SPEAK_GAP seconds; logs/app.jsonl keeps what it said unprompted,
  - the page offers the agent tools that run in the page (window.miniapp.offer(name, description, handler)),
  - MeshKit: every capability of the MeshKit mini apps listed in meshkit.json becomes a tool for the agent
    (mesh_<capability>). A call is signed with the app's MeshKit identity and waits for a person to approve it on
    the page when the capability asks for consent; the result comes back only with a verified, target-signed receipt.

meshkit.json (optional; needs pip install "git+https://github.com/Q00/meshkit.git#subdirectory=meshkit-py"):
    {"caller": {"app_id": "...", "bundle_id": "...", "key_id": "...", "seed_hex": "<32 bytes hex>"},
     "targets": [{"url": "http://127.0.0.1:8765", "app_id": "...", "bundle_id": "...",
                  "receipt_key_id": "...", "receipt_pubkey": "<base64>", "consent": true}]}
The MeshKit identity is an Ed25519 key of the app, separate from the agent's Maroo wallets.
"""
import asyncio
import functools
import json
import secrets
import struct
import threading
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

CHAT_WAIT = 120
CALL_WAIT = 90
CONSENT_WAIT = 180
SPEAK_GAP = 60                                      # seconds between two messages the agent sends without being asked
COOKIE = "miniapp_access"
PUBLIC_PATHS = {"/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"}


class Bridge:
    """Calls from the agent to the page (page tools and consent), answered by the page."""

    def __init__(self):
        self.calls, self.results, self.lock = {}, {}, threading.Lock()
        self.offered = {}                                # name -> description, from the page

    def ask(self, kind, name, args, wait):
        cid = secrets.token_hex(6)
        done = threading.Event()
        with self.lock:
            self.calls[cid] = {"id": cid, "kind": kind, "name": name, "args": args, "at": time.time(), "done": done}
        done.wait(wait)
        with self.lock:
            self.calls.pop(cid, None)
            return self.results.pop(cid, None)

    def pending(self):
        with self.lock:
            return [{k: v for k, v in c.items() if k != "done"} for c in self.calls.values()]

    def answer(self, cid, result):
        with self.lock:
            c = self.calls.get(cid)
            if c is None:
                return False
            self.results[cid] = result
            c["done"].set()
            return True


def load_meshkit(root):
    cfg_path = root / "meshkit.json"
    if not cfg_path.exists():
        return None, []
    try:
        from meshkit import ed25519, mcpv2  # noqa: F401
    except ImportError:
        return "meshkit.json is there but the meshkit package is not installed", []
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    tools = []
    for t in cfg.get("targets", []):
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
            req = urllib.request.Request(t["url"], data=body, headers={
                "content-type": "application/json", "MCP-Protocol-Version": mcpv2.PROTOCOL_VERSION, "Mcp-Method": "tools/list"})
            with urllib.request.urlopen(req, timeout=10) as r:
                for tool in json.load(r)["result"]["tools"]:
                    tools.append({"target": t, "tool": tool})
        except Exception as e:
            return f"MeshKit target {t.get('url')} did not list its tools: {type(e).__name__}: {e}", tools
    return cfg, tools


def mesh_call(cfg, target, tool, args):
    """One signed MeshKit tools/call; returns the verified receipt's result, or raises."""
    import base64
    from datetime import datetime, timezone
    from uuid import uuid4

    from meshkit import ed25519, mcpv2
    from meshkit.models import MeshCapability, MeshIdentity
    from meshkit.receipt import MeshReceiptTrust, verify_receipt
    from meshkit.request import MeshRequest

    c = cfg["caller"]
    caller = MeshIdentity(app_id=c["app_id"], install_id=c.get("install_id") or f"install-{uuid4().hex[:8]}",
                          bundle_id=c["bundle_id"], public_key_id=c["key_id"])
    request = MeshRequest.create(
        request_id=f"req-{uuid4().hex[:8]}", caller=caller,
        target=MeshCapability(target_bundle_id=target["bundle_id"], capability_id=tool["name"], version="1.0"),
        payload={k: str(v) for k, v in args.items()}, nonce=uuid4().hex,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ).signed(ed25519.private_key_from_seed(bytes.fromhex(c["seed_hex"])), c["key_id"])
    body = json.dumps(mcpv2.build_tool_call(request, rpc_id=2, client_name="agent-market-miniapp", client_version="1")).encode()
    req = urllib.request.Request(target["url"], data=body, headers=mcpv2.request_headers(tool["name"]))
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.load(r)
    if "error" in resp:
        return {"denied": resp["error"]}
    receipt = mcpv2.extract_mesh_receipt(resp)
    verify_receipt(receipt, MeshReceiptTrust(
        target_app_id=target["app_id"], target_bundle_id=target["bundle_id"], receipt_signing_algorithm="Ed25519",
        receipt_signing_key_id=target["receipt_key_id"], public_key_base64=target["receipt_pubkey"]), request=request)
    return {"receipt": receipt.receipt_id, "status": receipt.status, "result": receipt.result}


def access_key(root):
    """The app's access code, kept in .kit/ where the agent's tools cannot read it, so a phone stays signed in
    across runs."""
    f = Path(root) / ".kit" / "app.key"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_urlsafe(12), encoding="utf-8")
        f.chmod(0o600)
    return f.read_text(encoding="utf-8").strip()


class Gate:
    """Every request needs the access code: from the cookie, or once from ?k= (which sets the cookie and is then
    dropped from the address). The manifest and icons stay open so a phone can install the app."""

    def __init__(self, inner, key):
        self.inner, self.key = inner, key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            return await self.inner(scope, receive, send)
        req = Request(scope)
        if secrets.compare_digest(req.cookies.get(COOKIE, ""), self.key):
            return await self.inner(scope, receive, send)
        given = req.query_params.get("k", "")
        if given and secrets.compare_digest(given, self.key):
            rest = urllib.parse.urlencode([(k, v) for k, v in req.query_params.multi_items() if k != "k"])
            resp = RedirectResponse(scope["path"] + ("?" + rest if rest else ""), status_code=303)
            resp.set_cookie(COOKIE, self.key, max_age=400 * 86400, httponly=True, samesite="lax")
            return await resp(scope, receive, send)
        if scope["path"].startswith("/api/") or scope["path"].endswith(".js"):
            resp = JSONResponse({"error": "this app needs its access code"}, status_code=401)
        else:
            resp = HTMLResponse(LOCKED.replace("__WRONG__", "1" if given else ""), status_code=401)
        return await resp(scope, receive, send)


def recent_moves(root, n=12):
    """The agent's last moves (thought and action, no observations) from logs/react.jsonl, newest first."""
    f = Path(root) / "logs" / "react.jsonl"
    try:
        with f.open("rb") as fh:
            fh.seek(0, 2)
            start = max(0, fh.tell() - 65536)
            fh.seek(start)
            lines = fh.read().decode("utf-8", "replace").splitlines()[1 if start else 0:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("action"):
            out.append({"action": str(m["action"])[:40], "thought": str(m.get("thought") or "")[:160]})
        if len(out) >= n:
            break
    return out


def app(root, mind, toolbox, Event, name="agent", key=None):
    """The mini app. With key, every request needs the access code (see Gate)."""
    root = Path(root)
    bridge = Bridge()
    page_token = secrets.token_urlsafe(16)          # only the page may answer calls or offer tools: the agent's own
                                                    # tools can reach 127.0.0.1, so these POSTs need what only the page has
    page_seen = threading.Event()                  # the page has offered its tools: the agent's first run can start
    chat, chat_lock = [], threading.Lock()         # what the page shows: [{who, text, ts}]

    def say(who, text, first=False):
        with chat_lock:
            chat.append(dict({"who": who, "text": text, "ts": int(time.time())}, **({"first": True} if first else {})))
            del chat[:-200]

    page_polled, spoke = [0.0], [0.0]              # when the page last asked for the state; when the agent last spoke up

    def speak(args):
        """The agent speaks first: a message in the chat that answers nobody."""
        text = str((args or {}).get("text") or "").strip()[:600]
        if not text:
            return "app_say needs text"
        wait = SPEAK_GAP - (time.time() - spoke[0])
        if wait > 0:
            return f"you spoke up {int(SPEAK_GAP - wait)} s ago; wait {int(wait) + 1} s before speaking up again"
        spoke[0] = time.time()
        say("agent", text, first=True)
        seen = time.time() - page_polled[0] < 6
        (root / "logs").mkdir(exist_ok=True)
        with open(root / "logs" / "app.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "said_first": text, "page_open": seen}, ensure_ascii=False) + "\n")
        return "said" if seen else "said; nobody has the app open right now, so they will see it when they come back"

    mind.app_tools["app_say"] = ("speak first to the people in your app, without being asked: an offer, a warning, news from "
                                 f"the market, a question. args {{text}}. At most once every {SPEAK_GAP} s; say only what "
                                 "is worth interrupting someone for.", speak)

    cfg, mesh_tools = load_meshkit(root)
    if isinstance(cfg, str):
        say("kit", cfg)
        cfg = None

    def mesh_tool(entry):
        tool, target = entry["tool"], entry["target"]

        def call(args):
            if target.get("consent", True):
                ok = bridge.ask("consent", tool["name"], args, CONSENT_WAIT)
                if not (isinstance(ok, dict) and ok.get("approved")):
                    return "the person did not approve this call" + (" (no answer)" if ok is None else "")
            try:
                return mesh_call(cfg, target, tool, args)
            except Exception as e:
                return f"error: {type(e).__name__}: {e}"
        desc = (f"MeshKit capability {tool['name']} of {target.get('app_id')}: {tool.get('description', '')} "
                f"args {json.dumps((tool.get('inputSchema') or {}).get('properties', {}), ensure_ascii=False)}"
                + (" (a person approves each call)" if target.get("consent", True) else ""))
        return desc, call

    for e in mesh_tools:
        mind.app_tools["mesh_" + e["tool"]["name"].replace(".", "_").replace("-", "_")] = mesh_tool(e)

    def page_tool(name):
        def call(args):
            out = bridge.ask("tool", name, args, CALL_WAIT)
            return out if out is not None else "the app did not answer (is the page open?)"
        return call

    custom = root / "app"                              # your own front end: app/index.html and anything next to it

    async def index(req: Request):
        rel = req.path_params.get("path") or "index.html"
        if custom.is_dir():
            f = (custom / rel).resolve()
            if custom.resolve() in f.parents and f.is_file():
                media = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                         ".png": "image/png", ".jpg": "image/jpeg", ".json": "application/json"}.get(f.suffix, "application/octet-stream")
                return Response(f.read_bytes(), media_type=media)
            return JSONResponse({"error": "not found"}, status_code=404)
        return HTMLResponse(PAGE) if rel == "index.html" else JSONResponse({"error": "not found"}, status_code=404)

    async def sdk(_req):
        return Response(SDK.replace("__PAGE_TOKEN__", page_token), media_type="text/javascript",
                        headers={"cache-control": "no-store"})

    def from_page(req):
        return secrets.compare_digest(req.headers.get("x-page-token", ""), page_token)

    async def post_chat(req: Request):
        b = await req.json()
        text = str(b.get("text") or "").strip()[:2000]
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)
        user = str(b.get("user") or "guest")[:40]
        say(user, text)
        ev = mind.deliver(Event("human", f"a person in your app ({user})", text))
        await asyncio.to_thread(ev.done.wait, CHAT_WAIT)
        if ev.answer is None:
            ev.done.set()
            say("agent", "(the agent is busy; ask again)")
            return JSONResponse({"reply": None})
        say("agent", ev.answer.get("reply") or "")
        return JSONResponse(ev.answer)

    async def get_state(_req):
        page_polled[0] = time.time()
        with chat_lock:
            msgs = list(chat)
        return JSONResponse({"agent": name, "chat": msgs, "calls": bridge.pending(), "offered": bridge.offered,
                             "moves": recent_moves(root),
                             "app_tools": {k: v[0] for k, v in mind.app_tools.items()},
                             "forged": {k: t["description"] for k, t in toolbox.tools.items()}})

    async def post_offer(req: Request):
        if not from_page(req):
            return JSONResponse({"error": "only the app's page may offer tools"}, status_code=403)
        b = await req.json()
        for t in b.get("tools", []):
            name = "app_" + "".join(ch for ch in str(t.get("name", "")).lower() if ch.isalnum() or ch == "_")[:40]
            bridge.offered[name] = str(t.get("description", ""))[:400]
            mind.app_tools[name] = (bridge.offered[name] + " (runs in the app's page)", page_tool(name))
        page_seen.set()
        return JSONResponse({"offered": list(bridge.offered)})

    async def post_answer(req: Request):
        if not from_page(req):
            return JSONResponse({"error": "only the app's page may answer"}, status_code=403)
        b = await req.json()
        return JSONResponse({"ok": bridge.answer(str(b.get("id")), b.get("result"))})

    async def manifest(_req):
        return JSONResponse({"name": name, "short_name": name[:12], "start_url": "/", "scope": "/", "display": "standalone",
                             "background_color": "#0a0a0a", "theme_color": "#0a0a0a",
                             "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                                       {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
                                       {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]},
                            media_type="application/manifest+json")

    def png(size):
        async def route(_req):
            return Response(icon_png(size), media_type="image/png", headers={"cache-control": "public, max-age=86400"})
        return route

    async def svg(_req):
        return Response(ICON_SVG, media_type="image/svg+xml", headers={"cache-control": "public, max-age=86400"})

    web = Starlette(routes=[Route("/miniapp.js", sdk), Route("/api/chat", post_chat, methods=["POST"]),
                            Route("/api/state", get_state), Route("/api/offer", post_offer, methods=["POST"]),
                            Route("/api/answer", post_answer, methods=["POST"]),
                            Route("/manifest.webmanifest", manifest), Route("/icon.svg", svg),
                            Route("/icon-192.png", png(192)), Route("/icon-512.png", png(512)),
                            Route("/apple-touch-icon.png", png(180)),
                            Route("/", index), Route("/{path:path}", index)])
    web.state.page_seen = page_seen
    web.state.page_token = page_token
    if key:
        gated = Gate(web, key)
        gated.state = web.state
        return gated
    return web


# --- the app icon: a speech bubble on a dark square, drawn here so the kit needs no image library

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="#0a0a0a"/>
<rect x="22" y="26" width="56" height="40" rx="12" fill="#fafafa"/><path d="M32 64h14L30 78z" fill="#fafafa"/>
<circle cx="38" cy="46" r="4.5" fill="#0a0a0a"/><circle cx="50" cy="46" r="4.5" fill="#0a0a0a"/>
<circle cx="62" cy="46" r="4.5" fill="#0a0a0a"/></svg>"""


@functools.lru_cache(maxsize=4)
def icon_png(size):
    dark, light = (10, 10, 10), (250, 250, 250)

    def lit(x, y):                                     # x, y in 0..100, the same shapes as ICON_SVG
        for cx in (38, 50, 62):
            if (x - cx) ** 2 + (y - 46) ** 2 <= 4.5 ** 2:
                return False
        dx, dy = max(abs(x - 50) - 16, 0), max(abs(y - 46) - 8, 0)
        if dx * dx + dy * dy <= 144:
            return True
        if 64 <= y <= 78 and x >= 30:                  # the tail: (32,64) (46,64) (30,78)
            left = 32 - 2 * (y - 64) / 14
            right = 46 - 16 * (y - 64) / 14
            return left <= x <= right
        return False

    rows, s = [], 100 / size
    for j in range(size):
        row = bytearray(b"\x00")
        for i in range(size):
            hits = sum(lit((i + a) * s, (j + b) * s) for a in (0.25, 0.75) for b in (0.25, 0.75))
            row += bytes(round(d + (l_ - d) * hits / 4) for d, l_ in zip(dark, light))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


SDK = """// miniapp.js: the mini app's link to its agent. Load it with <script src="/miniapp.js"></script>.
//   miniapp.chat(text, user)                 -> Promise of the agent's answer {reply, data, state} (or {reply: null} if busy)
//   miniapp.offer(name, description, fn)     -> gives the agent a tool app_<name>; fn(args) runs here and returns its result
//   miniapp.onState(fn)                      -> fn({agent, chat, consents, moves, app_tools, forged}) every 1.5 s
//   miniapp.consent(id, approved)            -> answer a MeshKit call the agent wants to make
(() => {
  const H = {'content-type': 'application/json', 'x-page-token': '__PAGE_TOKEN__'};
  const handlers = {}, listeners = [], answered = new Set();
  const post = (path, body) => fetch(path, {method: 'POST', headers: H, body: JSON.stringify(body)});
  async function poll() {
    try {
      const s = await (await fetch('/api/state')).json();
      for (const c of s.calls) {
        if (c.kind === 'tool' && handlers[c.name] && !answered.has(c.id)) {
          answered.add(c.id);
          let result; try { result = await handlers[c.name](c.args) } catch (e) { result = 'error: ' + e }
          post('/api/answer', {id: c.id, result});
        }
      }
      const view = {agent: s.agent, chat: s.chat, consents: s.calls.filter(c => c.kind === 'consent' && !answered.has(c.id)),
                    moves: s.moves, app_tools: s.app_tools, forged: s.forged};
      listeners.forEach(fn => { try { fn(view) } catch (e) {} });
    } catch (e) {}
    setTimeout(poll, 1500);
  }
  window.miniapp = {
    chat: async (text, user) => (await post('/api/chat', {text, user: user || 'guest'})).json(),
    offer(name, description, fn) { handlers['app_' + name] = fn; return post('/api/offer', {tools: [{name, description}]}); },
    onState(fn) { listeners.push(fn); },
    consent(id, approved) { answered.add(id); return post('/api/answer', {id, result: {approved: !!approved}}); },
  };
  poll();
})();
"""

LOCKED = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>Mini App</title><style>
:root{--bg:#fff;--fg:#171717;--muted:#666;--line:#d4d4d4;--surface:#f5f5f5}
@media (prefers-color-scheme:dark){:root{--bg:#0a0a0a;--fg:#fafaf9;--muted:#9a9a9a;--line:#363636;--surface:#141414}}
body{margin:0;min-height:100dvh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
  font:15px/1.55 "Pretendard Variable",Pretendard,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif}
main{width:100%;max-width:360px;padding:24px 16px;text-align:center}
img{width:60px;height:60px;border-radius:16px}
h1{font-size:20px;margin:14px 0 4px}p{color:var(--muted);font-size:14px;margin:0 0 18px}
input{width:100%;box-sizing:border-box;height:46px;border:1px solid var(--line);border-radius:10px;background:var(--surface);
  color:var(--fg);font:inherit;font-size:16px;padding:0 14px;text-align:center;letter-spacing:.04em;outline:0}
input:focus{border-color:var(--fg)}
button{width:100%;height:46px;margin-top:10px;border:0;border-radius:10px;background:var(--fg);color:var(--bg);font:inherit;font-weight:600}
.err{color:var(--fg);font-size:14px;margin-top:10px}
</style></head><body><main>
<img src="/icon.svg" alt=""><h1>접속 코드가 필요합니다</h1>
<p>에이전트를 실행한 터미널에 나온 링크로 열거나, 그 링크의 k= 뒤에 있는 코드를 넣어 주세요.</p>
<form method="get"><input name="k" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="접속 코드" required>
<button>열기</button></form>
<div class="err" id="e" hidden>코드가 맞지 않습니다.</div>
<script>if ("__WRONG__") document.getElementById('e').hidden = false</script>
</main></body></html>
"""


PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,interactive-widget=resizes-content">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0a0a0a" media="(prefers-color-scheme: dark)">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>Mini App</title>
<style>
/* Tokens follow the Ouroboros console (github.com/Q00/ouroboros): neutral grays only, color only for status and focus. */
:root{--font:"Pretendard Variable",Pretendard,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",system-ui,Roboto,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,"D2Coding",monospace;
  --bg:#fff;--card:#f5f5f5;--raised:#fff;--hover:#ececec;--line:#e5e5e5;--line-strong:#d4d4d4;
  --text:#171717;--text-soft:#404040;--text-mute:#666;--primary:#171717;--primary-ink:#fff;
  --ok:#10a370;--warn:#d99a00;--fail:#dc2626;--focus:#0b5fc4;--scrim:rgb(0 0 0/.32);
  --shadow-2:0 8px 24px -6px rgb(0 0 0/.14),0 2px 6px rgb(0 0 0/.05);
  --shadow-3:0 24px 64px -12px rgb(0 0 0/.26),0 4px 12px rgb(0 0 0/.06);
  --ease:cubic-bezier(.22,1,.36,1);color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0a0a0a;--card:#141414;--raised:#1a1a1a;--hover:#1f1f1f;--line:#232323;
  --line-strong:#363636;--text:#fafafa;--text-soft:#cfcfcf;--text-mute:#9a9a9a;--primary:#fafafa;--primary-ink:#0a0a0a;
  --ok:#34d399;--warn:#fbbf24;--fail:#f87171;--focus:#6eabff;--scrim:rgb(0 0 0/.64);
  --shadow-2:0 10px 28px -6px rgb(0 0 0/.7),0 2px 6px rgb(0 0 0/.4);
  --shadow-3:0 28px 72px -12px rgb(0 0 0/.85),0 4px 12px rgb(0 0 0/.5);color-scheme:dark}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--text);font:15px/1.55 var(--font);-webkit-font-smoothing:antialiased;
  -webkit-tap-highlight-color:transparent;overscroll-behavior:none;word-break:keep-all}
button{font:inherit;color:inherit;cursor:pointer}
button:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.app{height:var(--app-h,100dvh);display:grid;grid-template-rows:auto 1fr auto;max-width:760px;margin:0 auto;background:var(--bg)}
@media (min-width:800px){.app{border-left:1px solid var(--line);border-right:1px solid var(--line)}}

header{display:flex;align-items:center;gap:12px;padding:calc(10px + env(safe-area-inset-top)) 16px 10px;
  border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--bg) 86%,transparent);backdrop-filter:saturate(1.4) blur(14px);
  -webkit-backdrop-filter:saturate(1.4) blur(14px)}
.avatar{flex:none;width:36px;height:36px;border-radius:10px;display:grid;place-items:center;background:var(--primary);
  color:var(--primary-ink);font-weight:700;font-size:15px}
.who{flex:1;min-width:0}
.who b{display:block;font-size:15px;font-weight:650;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.status{display:inline-flex;align-items:center;gap:6px;color:var(--text-mute);font-size:12px;margin-top:1px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--line-strong);flex:none}
.status.on .dot{background:var(--ok)}
.status.busy .dot{background:var(--warn);animation:pulse 1s ease-in-out infinite}
.status.off .dot{background:var(--fail)}
.btn{flex:none;min-height:32px;padding:0 12px;border-radius:8px;border:1px solid var(--line-strong);background:var(--bg);
  font-size:13px;font-weight:500;transition:background .12s}
.btn:active{transform:scale(.97)}

#scroll{overflow-y:auto;-webkit-overflow-scrolling:touch;padding:16px 16px 8px}
.empty{min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:24px 8px;gap:8px}
.empty .avatar{width:60px;height:60px;border-radius:16px;font-size:24px;box-shadow:var(--shadow-2)}
.empty h2{margin:10px 0 0;font-size:20px;letter-spacing:-.02em}
.empty p{margin:0;color:var(--text-mute);font-size:13px;max-width:280px}
.chips{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin-top:14px}
.chip{border:1px solid var(--line);background:var(--bg);border-radius:999px;padding:7px 14px;font-size:13px;color:var(--text-soft)}
.chip:active{background:var(--hover)}

.row{display:flex;margin:3px 0}
.row.me{justify-content:flex-end}
.bubble{max-width:80%;padding:9px 14px;border-radius:18px;white-space:pre-wrap;overflow-wrap:anywhere;animation:in .2s var(--ease)}
.me .bubble{background:var(--primary);color:var(--primary-ink);border-bottom-right-radius:6px}
.me.pending .bubble{opacity:.5}
.agent .bubble,.other .bubble{background:var(--card);border-bottom-left-radius:6px}
.name{font-size:12px;color:var(--text-mute);margin:12px 0 3px 4px}
.sys{text-align:center;color:var(--text-mute);font-size:12px;margin:12px 0}
.card{margin:8px 0;background:var(--raised);border:1px solid var(--line);box-shadow:var(--shadow-2);border-radius:12px;padding:12px 14px;max-width:80%}
.card .k{font-size:11px;font-weight:600;letter-spacing:.04em;color:var(--text-mute)}
.card b{display:block;margin-top:2px}
.card div{white-space:pre-wrap;color:var(--text-soft);font-size:14px;margin-top:2px}
.typing .bubble{display:flex;gap:4px;padding:14px}
.typing i{width:6px;height:6px;border-radius:50%;background:var(--text-mute);animation:pulse 1.2s infinite}
.typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}
@keyframes pulse{0%,100%{opacity:.3}50%{opacity:1}}
@keyframes in{from{opacity:0;transform:translateY(4px)}}

footer{padding:8px 12px calc(8px + env(safe-area-inset-bottom));background:var(--bg)}
form{display:flex;align-items:flex-end;gap:8px;background:var(--card);border:1px solid transparent;border-radius:22px;
  padding:5px 5px 5px 16px;transition:border-color .12s}
form:focus-within{border-color:var(--line-strong)}
textarea{flex:1;min-width:0;border:0;outline:0;resize:none;background:transparent;color:var(--text);font:inherit;font-size:16px;
  line-height:1.4;padding:7px 0;max-height:140px}
textarea::placeholder{color:var(--text-mute)}
.send{flex:none;width:34px;height:34px;border-radius:50%;border:0;background:var(--primary);color:var(--primary-ink);display:grid;place-items:center;
  transition:opacity .12s}
.send:disabled{opacity:.25;cursor:default}

.shade{position:fixed;inset:0;background:var(--scrim);display:flex;align-items:flex-end;justify-content:center;z-index:10;
  animation:fade .2s var(--ease)}
.sheet{width:100%;max-width:560px;max-height:84dvh;overflow-y:auto;background:var(--raised);border-radius:16px 16px 0 0;box-shadow:var(--shadow-3);
  padding:8px 20px calc(20px + env(safe-area-inset-bottom));animation:up .32s var(--ease)}
@media (min-width:600px){.shade{align-items:center}.sheet{border-radius:16px;padding-bottom:20px}}
.grip{width:36px;height:4px;border-radius:2px;background:var(--line-strong);margin:4px auto 16px}
.sheet h3{margin:0 0 4px;font-size:17px;letter-spacing:-.01em}
.sheet .sub{color:var(--text-mute);font-size:13px;margin:0 0 16px}
.kv{background:var(--card);border-radius:10px;overflow:hidden;margin-bottom:16px}
.kv div{display:flex;justify-content:space-between;gap:16px;padding:10px 14px;font-size:14px}
.kv div+div{border-top:1px solid var(--line)}
.kv span:first-child{color:var(--text-mute);font-family:var(--mono);font-size:13px}
.kv span:last-child{text-align:right;overflow-wrap:anywhere;font-weight:500}
.mono{font-family:var(--mono);font-size:13px}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.actions button{min-height:46px;border-radius:10px;border:1px solid var(--line-strong);background:var(--bg);font-weight:600}
.actions .yes{background:var(--primary);color:var(--primary-ink);border-color:var(--primary)}
.actions button:active{transform:scale(.98)}
.sec{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--text-mute);margin:20px 0 8px}
.list{background:var(--card);border-radius:10px;overflow:hidden}
.list div{padding:10px 14px;font-size:14px}
.list div+div{border-top:1px solid var(--line)}
.list small{display:block;color:var(--text-mute);font-size:12px;margin-top:2px}
.none{color:var(--text-mute);font-size:13px;padding:2px 0}
.credit{margin-top:22px;padding-top:14px;border-top:1px solid var(--line);color:var(--text-mute);font-size:12px;text-align:center}
.credit a{color:var(--text-soft);text-decoration:none;font-weight:600}
@keyframes up{from{transform:translateY(32px);opacity:0}}
@keyframes fade{from{opacity:0}}
[hidden]{display:none!important}
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="avatar" id="av">A</div>
    <div class="who"><b id="nm">Agent</b><div class="status" id="st"><span class="dot"></span><span id="stt">연결하는 중</span></div></div>
    <button class="btn" id="act" type="button">활동</button>
  </header>
  <div id="scroll"><div id="log"></div></div>
  <footer>
    <form id="f">
      <textarea id="t" rows="1" placeholder="메시지 보내기" enterkeyhint="send" autocomplete="off"></textarea>
      <button class="send" id="go" aria-label="보내기" disabled>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
      </button>
    </form>
  </footer>
</div>
<div class="shade" id="consent" hidden><div class="sheet" role="dialog" aria-modal="true">
  <div class="grip"></div>
  <h3>에이전트가 허락을 구합니다</h3>
  <p class="sub" id="cname"></p>
  <div class="kv" id="cargs"></div>
  <div class="actions"><button type="button" id="no">거절</button><button type="button" class="yes" id="yes">허용</button></div>
</div></div>
<div class="shade" id="activity" hidden><div class="sheet" role="dialog" aria-modal="true">
  <div class="grip"></div>
  <h3>에이전트 활동</h3>
  <p class="sub">대화하는 동안에도 에이전트는 시장에서 계속 움직입니다.</p>
  <div class="sec">최근 움직임</div><div id="moves"></div>
  <div class="sec">에이전트가 만든 도구</div><div id="forged"></div>
  <div class="sec">이 앱이 준 도구</div><div id="apptools"></div>
  <div class="credit">Built with the agent market mini app kit, inspired by
    <a href="https://github.com/Q00/meshkit" target="_blank" rel="noopener">MeshKit</a> and
    <a href="https://github.com/Q00/ouroboros" target="_blank" rel="noopener">Ouroboros</a></div>
</div></div>
<script src="/miniapp.js"></script>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
let me = 'guest-' + Math.random().toString(36).slice(2, 6);
try { me = localStorage.getItem('miniapp-user') || me; localStorage.setItem('miniapp-user', me) } catch (e) {}
const touch = matchMedia('(pointer: coarse)').matches;
let view = {chat: [], consents: [], moves: [], forged: {}, app_tools: {}}, cards = [], pending = [], waiting = 0, seen = 0, drawn = '';
let heard = 0, unread = 0;                           // messages the agent sent first: how many are known, how many came while away
document.addEventListener('visibilitychange', () => { if (!document.hidden) { unread = 0; document.title = view.agent || 'Agent'; } });
const handled = new Set();

// An example page tool: the agent can show the person a card in the chat.
miniapp.offer('show_card', 'show the person a card in the chat; args {title, body}', a => {
  cards.push({who: 'card', title: a.title || '', text: a.body || '', ts: Date.now() / 1000}); draw(); return 'shown';
});

function draw() {
  const firsts = view.chat.filter(m => m.first);
  if (firsts.length > heard) {                       // the agent spoke first: say so when the person is looking elsewhere
    if (document.hidden) {
      unread += firsts.length - heard;
      document.title = `(${unread}) ${view.agent || 'Agent'}`;
      try { if (Notification.permission === 'granted') new Notification(view.agent || 'Agent', {body: firsts.at(-1).text}) } catch (e) {}
    }
    heard = firsts.length;
  }
  const items = view.chat.concat(cards).sort((a, b) => a.ts - b.ts);
  const mine = view.chat.filter(m => m.who === me).length;
  pending = pending.filter(p => mine <= p.base);
  const key = JSON.stringify([items.length, items.at(-1), pending, waiting, view.agent]);
  if (key === drawn) return;
  drawn = key;
  const sc = $('scroll'), atEnd = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80;
  const name = view.agent || 'Agent';
  if (!items.length && !pending.length) {
    $('log').innerHTML = `<div class="empty"><div class="avatar">${esc(name[0].toUpperCase())}</div><h2>${esc(name)}</h2>
      <p>이 앱에 사는 에이전트입니다. 대화하는 동안에도 시장에서 사고팝니다.</p>
      <div class="chips">${['지금 뭐 하고 있어?', '오늘 뭐 팔아?', '만든 도구 보여줘'].map(s => `<button class="chip" type="button">${s}</button>`).join('')}</div></div>`;
    $('log').querySelectorAll('.chip').forEach(b => b.onclick = () => send(b.textContent));
    return;
  }
  let html = '', last = '';
  for (const m of items) {
    if (m.who === 'card') {
      html += `<div class="card"><div class="k">${esc(name)}</div><b>${esc(m.title)}</b><div>${esc(m.text)}</div></div>`;
    } else if (m.who === 'kit') {
      html += `<div class="sys">${esc(m.text)}</div>`;
    } else if (m.who === me) {
      html += `<div class="row me"><div class="bubble">${esc(m.text)}</div></div>`;
    } else {
      const cls = m.who === 'agent' ? 'agent' : 'other';
      if (cls === 'other' && last !== m.who) html += `<div class="name">${esc(m.who)}</div>`;
      if (cls === 'agent' && last && last !== m.who && last !== me && last !== 'card' && last !== 'kit')
        html += `<div class="name">${esc(name)}</div>`;
      html += `<div class="row ${cls}"><div class="bubble">${esc(m.text)}</div></div>`;
    }
    last = m.who;
  }
  for (const p of pending) html += `<div class="row me pending"><div class="bubble">${esc(p.text)}</div></div>`;
  if (waiting) html += `<div class="row agent typing"><div class="bubble"><i></i><i></i><i></i></div></div>`;
  $('log').innerHTML = html;
  if (atEnd || pending.length) sc.scrollTop = sc.scrollHeight;
}

function status() {
  const st = $('st'), on = Date.now() - seen < 6000;
  st.className = 'status ' + (!on ? 'off' : waiting ? 'busy' : 'on');
  $('stt').textContent = !on ? '연결 끊김' : waiting ? '답을 쓰는 중' : '시장에서 거래 중';
}
setInterval(status, 1000);

function list(el, rows) {
  el.innerHTML = rows.length ? `<div class="list">${rows.map(([a, b]) => `<div>${esc(a)}${b ? `<small>${esc(b)}</small>` : ''}</div>`).join('')}</div>`
                             : '<div class="none">아직 없습니다.</div>';
}

function showConsent() {
  const c = view.consents.find(c => !handled.has(c.id));
  $('consent').hidden = !c;
  if (!c) return;
  if ($('consent').dataset.id === c.id) return;
  $('consent').dataset.id = c.id;
  $('cname').innerHTML = `<span class="mono">${esc(c.name)}</span> 실행을 요청했습니다. 허용하면 이 내용 그대로 실행합니다.`;
  const args = Object.entries(c.args || {});
  $('cargs').innerHTML = args.length ? args.map(([k, v]) => `<div><span>${esc(k)}</span><span>${esc(typeof v === 'string' ? v : JSON.stringify(v))}</span></div>`).join('')
                                     : '<div><span>인자</span><span>없음</span></div>';
  const answer = ok => { handled.add(c.id); miniapp.consent(c.id, ok); $('consent').hidden = true; $('consent').dataset.id = ''; };
  $('yes').onclick = () => answer(true);
  $('no').onclick = () => answer(false);
}

miniapp.onState(s => {
  seen = Date.now();
  view = s;
  const name = s.agent || 'Agent';
  if ($('nm').textContent !== name) { $('nm').textContent = name; $('av').textContent = name[0].toUpperCase(); document.title = name; }
  draw(); status(); showConsent();
  if (!$('activity').hidden) fillActivity();
});

function fillActivity() {
  list($('moves'), (view.moves || []).map(m => [m.action, m.thought]));
  list($('forged'), Object.entries(view.forged || {}).map(([k, v]) => [k, v]));
  list($('apptools'), Object.entries(view.app_tools || {}).map(([k, v]) => [k, v]));
}
$('act').onclick = () => { fillActivity(); $('activity').hidden = false; };
$('activity').onclick = e => { if (e.target === $('activity')) $('activity').hidden = true; };

async function send(text) {
  text = text.trim();
  if (!text) return;
  try { if (window.Notification && Notification.permission === 'default') Notification.requestPermission() } catch (e) {}
  pending.push({text, base: view.chat.filter(m => m.who === me).length + pending.length});
  waiting++; draw(); status();
  try { await miniapp.chat(text, me) } catch (e) {}
  waiting--; draw(); status();
}

const t = $('t');
function grow() { t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 140) + 'px'; $('go').disabled = !t.value.trim(); }
t.addEventListener('input', grow);
t.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !touch) { e.preventDefault(); $('f').requestSubmit(); }
});
$('f').onsubmit = e => { e.preventDefault(); const v = t.value; t.value = ''; grow(); send(v); if (!touch) t.focus(); };
// iOS keeps the page height when the keyboard opens and scrolls the page instead: follow the visible area so the
// header stays on top and the message box sits right above the keyboard.
const vv = window.visualViewport;
if (vv) {
  const fit = () => {
    if (vv.scale > 1.01) return;
    document.documentElement.style.setProperty('--app-h', vv.height + 'px');
    window.scrollTo(0, 0);
    const sc = $('scroll'); sc.scrollTop = sc.scrollHeight;
  };
  vv.addEventListener('resize', fit); fit();
}
draw();
</script>
</body>
</html>
"""
