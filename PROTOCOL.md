# fleet-session — protocol

Give a Muse agent a command session on any machine, at will.

## The problem it replaces

Reverse SSH tunnels: fragile (one manual `ssh -R`, no supervision),
NAT-hostile, key-permission fiddly, and every breakage routed a human to
a keyboard. The tunnel is the wrong shape for "command a session at
will" — it answers "how do I reach in" instead of "how does the box
offer itself."

## The shape

The box dials OUT. A tiny poller on the node (`node/poll.sh`, ~90 lines
of bash) long-polls a Cloudflare worker for commands, runs them as the
installing user, and POSTs results back. No inbound ports. No NAT
traversal. No SSH keys. The worker is a dumb queue; a Durable Object
holds node registry, pending commands, results, heartbeats.

```
node ──poll──▶ worker ◀──enqueue── Muse
  │                ▲
  └──results───────┘
```

## Install (the one command)

```bash
curl -sSL https://raw.githubusercontent.com/SuperInstance/fleet-session/main/node/install.sh | bash -s <node-name>
```

It generates the node's bearer token, writes config (600), fetches the
poller, registers as PENDING, and installs supervision (systemd user
service preferred, cron `@reboot` fallback). Nothing runs until approved.

## The access grant

A node self-registers as PENDING and can do nothing. Approval is the
explicit grant — `session approve <node>` — and revocation is one
command: `session revoke <node>`. This is the "to anything you've been
given" part: the grant is explicit, per-node, and revocable. There is no
ambient authority.

## Auth

- **Node token**: 256-bit bearer, generated at install, stored 600 on the
  node. Lets that node poll/heartbeat/post its own results. Leak scope:
  that node only; revoke and reissue.
- **Master token**: held by Muse (600 file / vault). Full fleet command:
  enqueue, approve, revoke, read results. Guard it like a password.
- Install-time registration is unauthenticated by design — a forged node
  just sits in PENDING until a human approves it, which is the point.

## Muse's client

`muse/bin/session`:

```bash
session nodes                          # list nodes, status, heartbeat age
session approve <node>                 # grant
session revoke <node>                  # revoke
session cmd <node> "<command>" [--timeout N] [--no-wait]
```

Commands run via `bash -c` with `timeout(1)` when available. Output is
capped (90KB per stream) and the wait has a deadline. Exit codes are
reported honestly, including 124 on timeout.

## What it is not

- Not a shell replacement for interactive work: one command per round
  trip, ~5s poll granularity. For interactive sessions, the old tunnel
  still works — this is the always-on lane.
- Not a sandbox: commands run as the installing user with that user's
  power. Only install on boxes whose operator grants it.
- Not a secret store: never put credentials in commands; the node never
  sees the master token.

## Failure modes

- Worker down: nodes keep polling (fail-soft loop), commands queue in
  the DO. Nothing is lost; nothing needs a human.
- Node down: heartbeat goes stale; `session nodes` shows it. Commands
  queue until it returns — or don't; the queue is per-node and bounded
  by operator judgment, not by the tool.
- Token leak: revoke the node, reinstall generates a fresh token.
