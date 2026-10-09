#!/usr/bin/env python3
"""fleet-session relay: command queue + results for fleet nodes.
Runs on Oracle, exposed via cloudflared. Same API as the worker version.
Persistence: sqlite3. Auth: master token (env) + per-node bearer tokens.
"""
import json, os, re, sqlite3, time, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

DB = os.path.expanduser("~/.fleet-session-relay.db")
MASTER = os.environ.get("FLEET_MASTER_TOKEN", "")
PORT = int(os.environ.get("FLEET_RELAY_PORT", "8778"))

def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS nodes (name TEXT PRIMARY KEY, token TEXT, status TEXT, heartbeat REAL, note TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, node TEXT, command TEXT, timeout_secs INTEGER, created_at REAL, status TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS results (cmd_id TEXT PRIMARY KEY, node TEXT, exit_code INTEGER, stdout TEXT, stderr TEXT, truncated INTEGER, finished_at REAL)")
    return c

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def body(self):
        try: return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except Exception: return {}
    def bearer(self):
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "): return h[7:]
        q = parse_qs(urlparse(self.path).query)
        return q.get("token", [""])[0]

    def do_GET(self): self.route()
    def do_POST(self): self.route()

    def route(self):
        p = urlparse(self.path).path.strip("/").split("/")
        tok = self.bearer()
        is_master = tok and MASTER and tok == MASTER
        c = db()
        try:
            # POST /v1/nodes/register
            if self.command == "POST" and p == ["v1", "nodes", "register"]:
                b = self.body()
                name = (b.get("node") or "").strip()
                if not re.match(r"^[a-z0-9-]{1,40}$", name): return self.send_json({"error": "bad node name"}, 400)
                if not b.get("token") or len(b["token"]) < 32: return self.send_json({"error": "bad token"}, 400)
                try:
                    c.execute("INSERT INTO nodes VALUES (?,?,?,0,'')", (name, b["token"], "pending"))
                    c.commit()
                except sqlite3.IntegrityError:
                    return self.send_json({"error": "node exists"}, 409)
                return self.send_json({"node": name, "status": "pending"})
            # GET /v1/nodes
            if self.command == "GET" and p == ["v1", "nodes"]:
                if not is_master: return self.send_json({"error": "forbidden"}, 403)
                out = {r[0]: {"status": r[2], "last_heartbeat": r[3], "note": r[4]}
                       for r in c.execute("SELECT name,token,status,heartbeat,note FROM nodes")}
                return self.send_json({"nodes": out})
            if len(p) >= 3 and p[0] == "v1" and p[1] == "nodes":
                name = p[2]
                r = c.execute("SELECT token,status FROM nodes WHERE name=?", (name,)).fetchone()
                if not r: return self.send_json({"error": "unknown node"}, 404)
                ntoken, nstatus = r
                node_ok = tok and tok == ntoken and nstatus == "approved"
                # approve/revoke (master)
                if self.command == "POST" and len(p) == 4 and p[3] in ("approve", "revoke"):
                    if not is_master: return self.send_json({"error": "forbidden"}, 403)
                    ns = "approved" if p[3] == "approve" else "revoked"
                    c.execute("UPDATE nodes SET status=? WHERE name=?", (ns, name)); c.commit()
                    return self.send_json({"node": name, "status": ns})
                # enqueue command (master)
                if self.command == "POST" and p[3:] == ["commands"]:
                    if not is_master: return self.send_json({"error": "forbidden"}, 403)
                    if nstatus != "approved": return self.send_json({"error": "node not approved"}, 403)
                    b = self.body()
                    cmd = b.get("command", "")
                    if not isinstance(cmd, str) or not cmd or len(cmd) > 20000:
                        return self.send_json({"error": "bad command"}, 400)
                    cid = str(uuid.uuid4())
                    c.execute("INSERT INTO commands VALUES (?,?,?,?,?,?)",
                              (cid, name, cmd, min(int(b.get("timeout_secs") or 300), 1800), time.time(), "pending"))
                    c.commit()
                    return self.send_json({"cmd_id": cid})
                # claim next (node)
                if self.command == "GET" and p[3:] == ["commands", "next"]:
                    if not node_ok: return self.send_json({"error": "forbidden"}, 403)
                    r = c.execute("SELECT id,command,timeout_secs,created_at FROM commands WHERE node=? AND status='pending' ORDER BY created_at LIMIT 1", (name,)).fetchone()
                    if not r:
                        self.send_response(204); self.end_headers(); return
                    cid = r[0]
                    c.execute("UPDATE commands SET status='claimed' WHERE id=?", (cid,)); c.commit()
                    return self.send_json({"id": cid, "command": r[1], "timeout_secs": r[2], "created_at": r[3]})
                # post result (node)
                if self.command == "POST" and p[3:] == ["results"]:
                    if not node_ok: return self.send_json({"error": "forbidden"}, 403)
                    b = self.body()
                    if not b.get("cmd_id"): return self.send_json({"error": "bad result"}, 400)
                    cap = lambda s: (s[:100000] if isinstance(s, str) else "")
                    c.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?)",
                              (b["cmd_id"], name, int(b.get("exit_code") or 0), cap(b.get("stdout")), cap(b.get("stderr")), 1 if b.get("truncated") else 0, time.time()))
                    c.execute("UPDATE commands SET status='done' WHERE id=?", (b["cmd_id"],))
                    c.commit()
                    return self.send_json({"ok": True})
                # get result (master)
                if self.command == "GET" and len(p) == 5 and p[3] == "results":
                    if not is_master: return self.send_json({"error": "forbidden"}, 403)
                    r = c.execute("SELECT node,exit_code,stdout,stderr,truncated,finished_at FROM results WHERE cmd_id=?", (p[4],)).fetchone()
                    if not r: return self.send_json({"error": "pending"}, 404)
                    return self.send_json({"node": r[0], "exit_code": r[1], "stdout": r[2], "stderr": r[3], "truncated": bool(r[4]), "finished_at": r[5]})
                # heartbeat (node; allowed if token matches even when pending)
                if self.command == "POST" and p[3:] == ["heartbeat"]:
                    if not (tok and tok == ntoken): return self.send_json({"error": "forbidden"}, 403)
                    b = self.body()
                    note = (b.get("note") or "")[:200] if isinstance(b.get("note"), str) else ""
                    c.execute("UPDATE nodes SET heartbeat=?, note=? WHERE name=?", (time.time(), note, name))
                    c.commit()
                    return self.send_json({"ok": True, "status": nstatus})
            return self.send_json({"error": "not found"}, 404)
        finally:
            c.close()

if __name__ == "__main__":
    if not MASTER:
        print("FLEET_MASTER_TOKEN required", flush=True)
        raise SystemExit(2)
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
