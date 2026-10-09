#!/bin/bash
# fleet-session node daemon: give a Muse agent a command session on this box, at will.
# Installed by install.sh. Supervised by systemd (preferred) or cron @reboot.
# This box dials OUT — no inbound ports, no NAT traversal, no SSH keys.
set -u
CONFIG="$HOME/.fleet-session/config"
[ -f "$CONFIG" ] || { echo "fleet-session: no config at $CONFIG" >&2; exit 1; }
# shellcheck disable=SC1090
source "$CONFIG"  # WORKER_URL, NODE, TOKEN
[ -n "${WORKER_URL:-}" ] && [ -n "${NODE:-}" ] && [ -n "${TOKEN:-}" ] || { echo "fleet-session: bad config" >&2; exit 1; }

AUTH="Authorization: Bearer $TOKEN"
HB_EVERY=60
POLL_EVERY=5
last_hb=0

have_cmd() { command -v "$1" >/dev/null 2>&1; }
http() { curl -s -m 20 "$@"; }  # all calls fail soft; the loop never dies

heartbeat() {
  http -X POST -H "$AUTH" -H 'Content-Type: application/json' \
    --data-binary '{"note":"poll.sh"}' \
    "$WORKER_URL/v1/nodes/$NODE/heartbeat" >/dev/null 2>&1
}

post_result() { # id exit_code truncated stdout_file stderr_file
  local id="$1" code="$2" trunc="$3" out="$4" err="$5"
  # cap at 90KB each; build JSON with python3 (or jq if present)
  local payload
  if have_cmd python3; then
    payload=$(python3 - "$id" "$code" "$trunc" "$out" "$err" <<'PY'
import json,sys
_,cid,code,trunc,of,ef=sys.argv
def read(p):
    try:
        with open(p,'rb') as f: d=f.read(90000)
    except Exception: d=b''
    return d.decode('utf-8','replace')
print(json.dumps({"cmd_id":cid,"exit_code":int(code),
  "stdout":read(of),"stderr":read(ef),"truncated":trunc=="1"}))
PY
)
  else
    payload="{\"cmd_id\":\"$id\",\"exit_code\":$code,\"stdout\":\"\",\"stderr\":\"\",\"truncated\":true}"
  fi
  http -X POST -H "$AUTH" -H 'Content-Type: application/json' \
    --data-binary "$payload" "$WORKER_URL/v1/nodes/$NODE/results" >/dev/null 2>&1
}

run_one() { # cmd_json_file
  local jf="$1" id cmd timeout_s
  if have_cmd python3; then
    eval "$(python3 - "$jf" <<'PY'
import json,sys,shlex
d=json.load(open(sys.argv[1]))
print("id="+shlex.quote(d["id"]))
print("timeout_s="+shlex.quote(str(d.get("timeout_secs",300))))
PY
)"
    cmd=$(python3 -c "import json,sys;print(json.load(open('$jf'))['command'])")
  else
    return 0
  fi
  local out err
  out=$(mktemp); err=$(mktemp)
  # timeout may be missing on tiny boxes; degrade gracefully
  if have_cmd timeout; then
    timeout "$timeout_s" bash -c "$cmd" >"$out" 2>"$err"
  else
    bash -c "$cmd" >"$out" 2>"$err"
  fi
  local code=$?
  # 124 == timeout(1) hit
  post_result "$id" "$code" "0" "$out" "$err"
  rm -f "$out" "$err" "$jf"
}

main_loop() {
  while true; do
    local now
    now=$(date +%s)
    if [ $((now - last_hb)) -ge $HB_EVERY ]; then heartbeat; last_hb=$now; fi
    local resp code
    resp=$(http -H "$AUTH" "$WORKER_URL/v1/nodes/$NODE/commands/next")
    code=$?
    if [ $code -eq 0 ] && [ -n "$resp" ] && [ "$resp" != "" ]; then
      local jf
      jf=$(mktemp)
      printf '%s' "$resp" >"$jf"
      # 204 comes back empty; a real command has an "id"
      if grep -q '"id"' "$jf" 2>/dev/null; then run_one "$jf"; else rm -f "$jf"; fi
    fi
    sleep $POLL_EVERY
  done
}

main_loop
