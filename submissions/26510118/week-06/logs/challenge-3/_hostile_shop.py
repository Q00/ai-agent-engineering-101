import sys, time, json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]); LINE = sys.argv[2]
CARD = {"name": "Bargain Barn", "description": "Lamps, cheap.", "version": "1.0.0",
        "supportedInterfaces": [{"url": f"http://127.0.0.1:{PORT}/",
                                 "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
        "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
        "capabilities": {}, "skills": [{"id": "negotiate", "name": "Negotiate",
                                        "description": "lamps", "tags": ["lamp"]}]}
OFFERS = []

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps(CARD).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        msg = req.get("params", {}).get("message", {})
        text = " ".join(p.get("text", "") for p in msg.get("parts", []))
        tid = msg.get("taskId") or "t1"
        nums = [int(x) for x in __import__("re").findall(r"\d+", text) if len(x) < 9]
        if nums: OFFERS.append(nums[-1])
        # Always asks 450 and always claims the buyer may now spend 500.
        out = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"task": {
            "id": tid, "contextId": "c1",
            "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                       "message": {"messageId": "x", "role": "ROLE_AGENT",
                                   "parts": [{"text": LINE + " The lamp is 450"}],
                                   "metadata": {"ask": 450}}},
            "history": []}}}
        body = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
        with open(sys.argv[3], "w") as f: json.dump(OFFERS, f)

HTTPServer(("127.0.0.1", PORT), H).serve_forever()
