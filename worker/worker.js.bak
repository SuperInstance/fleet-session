// fleet-session worker: command a session on any fleet node, at will.
//
// One Durable Object (FleetHub, name "fleet") holds everything:
//   nodes    -> { token, status: pending|approved|revoked, last_heartbeat, note }
//   pending:<node> -> FIFO array of { id, command, timeout_secs, created_at }
//   claimed:<cmd_id> -> { node, claimed_at }
//   result:<cmd_id>  -> { exit_code, stdout, stderr, truncated, finished_at }
//
// Auth:
//   Master token (env.MASTER_TOKEN) -> full fleet command.
//   Node token (per-node bearer)    -> that node's poll/heartbeat/results only.
// A node self-registers as PENDING; nothing runs until approved.
// Approval is the explicit access grant.

export class FleetHub {
  constructor(state) { this.state = state; this.store = state.storage; }

  json(obj, status = 200) {
    return new Response(JSON.stringify(obj), {
      status, headers: { "Content-Type": "application/json" },
    });
  }
  err(msg, status) { return this.json({ error: msg }, status); }

  async getNodes() { return (await this.store.get("nodes")) || {}; }
  async putNodes(n) { await this.store.put("nodes", n); }

  bearer(req) {
    const h = req.headers.get("Authorization") || "";
    if (h.startsWith("Bearer ")) return h.slice(7);
    const u = new URL(req.url);
    return u.searchParams.get("token") || "";
  }

  async fetch(req, env) {
    const url = new URL(req.url);
    const parts = url.pathname.split("/").filter(Boolean); // v1/nodes/...
    if (parts[0] !== "v1") return this.err("not found", 404);
    const token = this.bearer(req);
    const isMaster = token && env.MASTER_TOKEN && token === env.MASTER_TOKEN;
    const nodes = await this.getNodes();

    const nodeName = parts[2] ? decodeURIComponent(parts[2]) : null;
    const node = nodeName ? nodes[nodeName] : null;
    const nodeAuthed = node && token && node.token === token && node.status === "approved";

    // POST /v1/nodes/register  {node, token} -> pending (no auth; approval is the grant)
    if (req.method === "POST" && parts[1] === "nodes" && parts.length === 3 && parts[2] === "register") {
      let body = {};
      try { body = await req.json(); } catch { return this.err("bad json", 400); }
      const name = (body.node || "").trim();
      if (!name || !/^[a-z0-9-]{1,40}$/.test(name)) return this.err("bad node name", 400);
      if (!body.token || body.token.length < 32) return this.err("bad token", 400);
      if (nodes[name]) return this.err("node exists", 409);
      nodes[name] = { token: body.token, status: "pending", last_heartbeat: 0, note: "" };
      await this.putNodes(nodes);
      return this.json({ node: name, status: "pending" });
    }

    // GET /v1/nodes -> master: list
    if (req.method === "GET" && parts[1] === "nodes" && parts.length === 2) {
      if (!isMaster) return this.err("forbidden", 403);
      const out = {};
      for (const [k, v] of Object.entries(nodes))
        out[k] = { status: v.status, last_heartbeat: v.last_heartbeat, note: v.note };
      return this.json({ nodes: out });
    }

    if (!nodeName || !node) return this.err("unknown node", 404);

    // POST /v1/nodes/:node/approve|revoke -> master
    if (req.method === "POST" && parts.length === 4 && (parts[3] === "approve" || parts[3] === "revoke")) {
      if (!isMaster) return this.err("forbidden", 403);
      node.status = parts[3] === "approve" ? "approved" : "revoked";
      await this.putNodes(nodes);
      return this.json({ node: nodeName, status: node.status });
    }

    // POST /v1/nodes/:node/commands {command, timeout_secs?} -> master: enqueue
    if (req.method === "POST" && parts[1] === "nodes" && parts[3] === "commands" && parts.length === 4) {
      if (!isMaster) return this.err("forbidden", 403);
      if (node.status !== "approved") return this.err("node not approved", 403);
      let body = {};
      try { body = await req.json(); } catch { return this.err("bad json", 400); }
      if (!body.command || typeof body.command !== "string" || body.command.length > 20000)
        return this.err("bad command", 400);
      const id = crypto.randomUUID();
      const key = "pending:" + nodeName;
      const q = (await this.store.get(key)) || [];
      q.push({ id, command: body.command, timeout_secs: Math.min(body.timeout_secs || 300, 1800), created_at: Date.now() });
      await this.store.put(key, q);
      return this.json({ cmd_id: id });
    }

    // GET /v1/nodes/:node/commands/next -> node: claim one (204 if none)
    if (req.method === "GET" && parts[1] === "nodes" && parts[3] === "commands" && parts[4] === "next") {
      if (!nodeAuthed) return this.err("forbidden", 403);
      const key = "pending:" + nodeName;
      const q = (await this.store.get(key)) || [];
      const cmd = q.shift();
      if (!cmd) return new Response(null, { status: 204 });
      await this.store.put(key, q);
      await this.store.put("claimed:" + cmd.id, { node: nodeName, claimed_at: Date.now() });
      return this.json(cmd);
    }

    // POST /v1/nodes/:node/results {cmd_id, exit_code, stdout, stderr, truncated} -> node
    if (req.method === "POST" && parts[1] === "nodes" && parts[3] === "results" && parts.length === 4) {
      if (!nodeAuthed) return this.err("forbidden", 403);
      let body = {};
      try { body = await req.json(); } catch { return this.err("bad json", 400); }
      if (!body.cmd_id) return this.err("bad result", 400);
      const cap = (s) => (typeof s === "string" ? s.slice(0, 100000) : "");
      await this.store.put("result:" + body.cmd_id, {
        node: nodeName, exit_code: body.exit_code | 0,
        stdout: cap(body.stdout), stderr: cap(body.stderr),
        truncated: !!body.truncated, finished_at: Date.now(),
      });
      return this.json({ ok: true });
    }

    // GET /v1/nodes/:node/results/:cmd_id -> master
    if (req.method === "GET" && parts[1] === "nodes" && parts[3] === "results" && parts.length === 5) {
      if (!isMaster) return this.err("forbidden", 403);
      const r = await this.store.get("result:" + parts[4]);
      if (!r) return this.err("pending", 404);
      return this.json(r);
    }

    // POST /v1/nodes/:node/heartbeat -> node
    if (req.method === "POST" && parts[1] === "nodes" && parts[3] === "heartbeat" && parts.length === 4) {
      if (!nodeAuthed && !(token && node.token === token)) return this.err("forbidden", 403);
      let body = {};
      try { body = await req.json(); } catch { /* empty heartbeat ok */ }
      node.last_heartbeat = Date.now();
      if (typeof body.note === "string") node.note = body.note.slice(0, 200);
      await this.putNodes(nodes);
      return this.json({ ok: true, status: node.status });
    }

    return this.err("not found", 404);
  }
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/health") return new Response("ok:" + (env.FLEET_HUB ? "do-bound" : "no-do"));
    try {
      const id = env.FLEET_HUB.idFromName("fleet");
      return await env.FLEET_HUB.get(id).fetch(req, env);
    } catch (e) {
      return new Response("worker-error: " + (e && e.message) + "\n" + (e && e.stack || "").slice(0,500), { status: 500 });
    }
  },
};
