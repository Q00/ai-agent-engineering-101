"""The status page on Cloudflare (design/ideal-harness.md 17-6, D-36): a Worker on workers.dev serves one HTML page
kept in Workers KV, and Cloudflare Access lets only the owner's e-mail in (one-time PIN).

    py -m ops.cf_page setup            # KV namespace, Worker, workers.dev route, Access app (idempotent)
    py -m ops.cf_page put page.html    # replace the page
    py -m ops.cf_page check            # an anonymous request must not reach the page

Standard library only. The API token and account id come from the user's environment (CLOUDFLARE_API_TOKEN,
CLOUDFLARE_ACCOUNT_ID); they are never printed or written anywhere. The page must not go up before `check` passes.
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

API = "https://api.cloudflare.com/client/v4"
WORKER = "gom31-r3-status"
KV_TITLE = "gom31-r3-status"
KEY = "page"
ALLOW = ["gom31@seoultech.ac.kr", "gom12602@gmail.com"]     # the owner's e-mails (user 11/5)

WORKER_JS = """export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/healthz") return new Response("ok");
    const html = await env.PAGE.get("page");
    return new Response(html || "<p>no page yet</p>", {headers: {
      "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
      "x-robots-tag": "noindex, nofollow", "referrer-policy": "no-referrer"}});
  }
};
"""


def _env(name):
    v = os.environ.get(name)
    if not v and os.name == "nt":            # a user variable set after this process started
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = winreg.QueryValueEx(k, name)[0]
        except OSError:
            v = None
    if not v:
        raise SystemExit(f"{name} is not set")
    return v


def _req(method, path, body=None, ctype="application/json", raw=False):
    acc = _env("CLOUDFLARE_ACCOUNT_ID")
    url = API + path.replace("{acc}", acc)
    data = body if (body is None or isinstance(body, bytes)) else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Authorization": f"Bearer {_env('CLOUDFLARE_API_TOKEN')}", "Content-Type": ctype})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            out = resp.read()
    except urllib.error.HTTPError as e:
        out = e.read()
        try:
            msg = json.loads(out).get("errors")
        except ValueError:
            msg = out[:200]
        raise SystemExit(f"{method} {path} -> {e.code}: {msg}")
    return out if raw else json.loads(out)


def kv_id():
    for ns in _req("GET", "/accounts/{acc}/storage/kv/namespaces?per_page=100")["result"]:
        if ns["title"] == KV_TITLE:
            return ns["id"]
    return _req("POST", "/accounts/{acc}/storage/kv/namespaces", {"title": KV_TITLE})["result"]["id"]


def deploy_worker(ns):
    b = "----cf" + uuid.uuid4().hex
    meta = {"main_module": "worker.js", "compatibility_date": "2025-01-01",
            "bindings": [{"type": "kv_namespace", "name": "PAGE", "namespace_id": ns}]}
    parts = [("metadata", "blob", "application/json", json.dumps(meta)),
             ("worker.js", "worker.js", "application/javascript+module", WORKER_JS)]
    body = b""
    for name, fname, ctype, content in parts:
        body += (f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode() + content.encode() + b"\r\n"
    body += f"--{b}--\r\n".encode()
    _req("PUT", f"/accounts/{{acc}}/workers/scripts/{WORKER}", body, ctype=f"multipart/form-data; boundary={b}")
    _req("POST", f"/accounts/{{acc}}/workers/scripts/{WORKER}/subdomain", {"enabled": True, "previews_enabled": False})


def host():
    sub = _req("GET", "/accounts/{acc}/workers/subdomain")["result"]["subdomain"]
    return f"{WORKER}.{sub}.workers.dev"


def ensure_access(h):
    apps = _req("GET", "/accounts/{acc}/access/apps")["result"] or []
    app = next((a for a in apps if a.get("domain") == h), None)
    if app is None:
        app = _req("POST", "/accounts/{acc}/access/apps", {
            "name": "gom31 R3 status page", "domain": h, "type": "self_hosted", "session_duration": "24h",
            "app_launcher_visible": False})["result"]
    pol = _req("GET", f"/accounts/{{acc}}/access/apps/{app['id']}/policies")["result"] or []
    if not pol:
        _req("POST", f"/accounts/{{acc}}/access/apps/{app['id']}/policies", {
            "name": "owner only", "decision": "allow", "precedence": 1,
            "include": [{"email": {"email": e}} for e in ALLOW]})
    return app["id"]


def put(html):
    ns = kv_id()
    _req("PUT", f"/accounts/{{acc}}/storage/kv/namespaces/{ns}/values/{KEY}", html.encode("utf-8"),
         ctype="text/html; charset=utf-8")


def check():
    """An anonymous request must not see the page: Access answers with a redirect to its login (or 401/403)."""
    url = f"https://{host()}/"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect)
    try:
        r = op.open(urllib.request.Request(url, headers={"User-Agent": "status-check"}), timeout=20)
        body = r.read(400).decode("utf-8", "replace")
        return {"url": url, "status": r.status, "protected": False, "body_start": body[:80]}
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        return {"url": url, "status": e.code, "protected": e.code in (301, 302, 303, 307, 401, 403)
                and ("cloudflareaccess.com" in loc or e.code in (401, 403)), "location": loc[:80]}


def setup():
    ns = kv_id()
    deploy_worker(ns)
    h = host()
    app = ensure_access(h)
    return {"kv": "ok", "worker": WORKER, "host": h, "access_app": bool(app)}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "setup":
        print(json.dumps(setup()))
    elif cmd == "put":
        put(open(sys.argv[2], encoding="utf-8").read())
        print("put ok")
    else:
        print(json.dumps(check()))
