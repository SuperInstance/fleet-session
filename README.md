# fleet-session

Give a Muse agent a command session on any machine, at will.

One install command. Self-healing. Scoped by explicit grant. No SSH keys,
no inbound ports, no NAT traversal — the box dials out.

```bash
curl -sSL https://raw.githubusercontent.com/SuperInstance/fleet-session/main/node/install.sh | bash -s <node-name>
```

The node registers as PENDING and can do nothing until approved. Approval
is the access grant.

## Layout

- `worker/` — Cloudflare worker: command queue + results + heartbeats,
  backed by one Durable Object. `deploy.py` ships it.
- `node/` — `poll.sh` (the daemon) and `install.sh` (the one command).
- `muse/` — `bin/session`: Muse's client (`nodes`, `approve`, `revoke`,
  `cmd`).
- `PROTOCOL.md` — the design: why, auth model, failure modes.

## Security

Public repo, private traffic. Node tokens (bearer, 256-bit, mode 600)
and the master token (Muse's, mode 600 / vault) never appear here.
Commands run as the installing user — only install where granted.
