"""Minimal fake Telegram Bot API for an end-to-end test of the real library."""
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

INBOX = []      # updates to deliver
SENT = []       # what the bot sent
COMMANDS = []
uid = [0]

def push(text, user=111, username="owner"):
    uid[0] += 1
    INBOX.append({"update_id": uid[0], "message": {"message_id": uid[0], "date": int(time.time()),
        "chat": {"id": 500, "type": "private"}, "from": {"id": user, "is_bot": False, "first_name": "U", "username": username},
        "text": text, **({"entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]} if text.startswith("/") else {})}})

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n).decode() if n else ""
        try: data = json.loads(raw) if raw.startswith("{") else {k: v[0] for k, v in parse_qs(raw).items()}
        except Exception: data = {}
        method = self.path.rsplit("/", 1)[-1]
        if method == "getMe": res = {"id": 42, "is_bot": True, "first_name": "Zen", "username": "ZenBot", "can_join_groups": True, "can_read_all_group_messages": False, "supports_inline_queries": False}
        elif method == "getUpdates":
            off = int(data.get("offset") or 0)
            batch = [u for u in INBOX if u["update_id"] >= off]
            if not batch: time.sleep(0.3)
            res = batch
        elif method == "sendMessage":
            SENT.append(data.get("text")); res = {"message_id": 900 + len(SENT), "date": int(time.time()), "chat": {"id": 500, "type": "private"}, "text": data.get("text")}
        elif method == "setMyCommands": COMMANDS.append(data.get("commands")); res = True
        else: res = True
        body = json.dumps({"ok": True, "result": res}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

def serve(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
