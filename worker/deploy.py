#!/usr/bin/env python3
"""Deploy the fleet-session worker to Cloudflare Workers.

Usage: python3 deploy.py [--rotate-master]
Deploys worker/worker.js with the FleetHub Durable Object binding,
then ensures the MASTER_TOKEN secret exists. The master token is saved
to ~/.fleet-session-master (mode 600): line 1 token, line 2 worker URL.
With --rotate-master, generates and stores a fresh token.
"""
import sys, os, json, secrets, stat
sys.path.insert(0, '/opt/hatch/skills/skill-creator/bin')
from dynamic_credentials import add_surrogate_to_request, read_json_response
import urllib.request

ACCOUNT_ID = "049ff5e84ecf636b53b162cbb580aae6"
WORKER_NAME = "fleet-session"
HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_FILE = os.path.expanduser("~/.fleet-session-master")

def api(method, path, data=None, ctype="application/json"):
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}", data=data, method=method)
    if data and ctype: req.add_header("Content-Type", ctype)
    add_surrogate_to_request(req, "custom.cloudflare", allowed_hosts=["api.cloudflare.com"])
    return read_json_response(urllib.request.urlopen(req, timeout=60))

def main():
    rotate = "--rotate-master" in sys.argv
    with open(os.path.join(HERE, "worker.js")) as f:
        code = f.read()
    print(f"worker.js: {len(code)} bytes")

    metadata = {
        "main_module": "worker.js",
        "compatibility_date": "2024-01-01",
        "bindings": [
            {"type": "durable_object_namespace", "name": "FLEET_HUB", "class_name": "FleetHub"},
        ],
        "migrations": {
            "new_classes": ["FleetHub"],
            "new_sqlite_classes": [],
            "deleted_classes": [],
        },
    }
    # The DO class must also be exported from the worker (it is).

    boundary = "----FleetSessionDeploy99"
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n')
    parts.append(json.dumps(metadata).encode() + b"\r\n")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="worker.js"; filename="worker.js"\r\nContent-Type: application/javascript+module\r\n\r\n')
    parts.append(code.encode() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    def put_with_migrations(migrations):
        md = dict(metadata); md["migrations"] = migrations
        bparts = []
        bparts.append(f"--{boundary}\r\n".encode())
        bparts.append(b'Content-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n')
        bparts.append(json.dumps(md).encode() + b"\r\n")
        bparts.append(f"--{boundary}\r\n".encode())
        bparts.append(b'Content-Disposition: form-data; name="worker.js"; filename="worker.js"\r\nContent-Type: application/javascript+module\r\n\r\n')
        bparts.append(code.encode() + b"\r\n")
        bparts.append(f"--{boundary}--\r\n".encode())
        return api("PUT", f"/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER_NAME}",
                   b"".join(bparts), ctype=f"multipart/form-data; boundary={boundary}")
    try:
        r = put_with_migrations(metadata["migrations"])
    except Exception as e:
        # class already migrated: retry without the migration
        print("redeploy without migration:", str(e)[:120])
        r = put_with_migrations({"new_classes": [], "new_sqlite_classes": [], "deleted_classes": []})
    if not r.get("success"):
        print("DEPLOY FAILED:", json.dumps(r)[:500]); sys.exit(1)
    print(f"deployed: {WORKER_NAME}")

    worker_url = f"https://{WORKER_NAME}.casey-digennaro.workers.dev"

    # master token
    token = None
    if os.path.exists(MASTER_FILE) and not rotate:
        token = open(MASTER_FILE).read().splitlines()[0].strip()
        print("keeping existing master token")
    if not token:
        token = secrets.token_hex(32)
        print("generated new master token")
    # store as secret (PUT replaces)
    r = api("PUT", f"/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER_NAME}/secrets",
            json.dumps({"name": "MASTER_TOKEN", "text": token, "type": "secret_text"}).encode())
    if not r.get("success"):
        print("SECRET FAILED:", json.dumps(r)[:500]); sys.exit(1)
    print("MASTER_TOKEN secret stored")

    with open(MASTER_FILE, "w") as f:
        f.write(token + "\n" + worker_url + "\n")
    os.chmod(MASTER_FILE, stat.S_IRUSR | stat.S_IWUSR)
    print(f"master file: {MASTER_FILE} (600)")
    print(f"worker URL: {worker_url}")

if __name__ == "__main__":
    main()
