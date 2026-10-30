// HTTP front for the gateway, served at /whatsapp/ on the agent domain.
// Everything except /whatsapp/health requires a signed-in console session.

import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Accounts } from './accounts.js';
import crypto from 'node:crypto';
import { consoleUser, listAgents } from './cowagent.js';

const PORT = Number(process.env.PORT || 8080);
const PAGE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'public', 'index.html');
const accounts = new Accounts();
const INTERNAL_TOKEN = process.env.COWAGENT_API_TOKEN || '';

// Container-to-container calls (scheduled messages from LRMagent) carry the
// shared API token and never pass through Caddy, which adds X-Forwarded-For.
function internalCaller(req) {
  if (!INTERNAL_TOKEN || req.headers['x-forwarded-for']) return false;
  const given = Buffer.from(String(req.headers.authorization || '').replace(/^Bearer\s+/i, ''));
  const want = Buffer.from(INTERNAL_TOKEN);
  return given.length === want.length && crypto.timingSafeEqual(given, want);
}

function send(res, status, body, type = 'application/json; charset=utf-8') {
  const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': type,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'same-origin',
  });
  res.end(data);
}

async function readJson(req) {
  let size = 0;
  const parts = [];
  for await (const part of req) {
    size += part.length;
    if (size > 64 * 1024) throw Object.assign(new Error('body too large'), { status: 413 });
    parts.push(part);
  }
  try {
    return parts.length ? JSON.parse(Buffer.concat(parts).toString('utf8')) : {};
  } catch {
    throw Object.assign(new Error('invalid JSON'), { status: 400 });
  }
}

// Writes must come from this site's own page: JSON only, and a same-host Origin
// when the browser sends one.
function sameSite(req) {
  if (!String(req.headers['content-type'] || '').startsWith('application/json')) return false;
  const origin = req.headers.origin;
  if (!origin) return true;
  try {
    return new URL(origin).host === (req.headers['x-forwarded-host'] || req.headers.host);
  } catch {
    return false;
  }
}

async function route(req, res) {
  const url = new URL(req.url, 'http://gateway');
  const cookie = req.headers.cookie || '';

  if (url.pathname === '/whatsapp/health') {
    const views = await Promise.all([...accounts.byAgent.keys()].map((id) => accounts.view(id)));
    const body = { ok: true, connected: views.filter((v) => v.status === 'connected').length };
    // Per-agent link states only for callers inside the Docker network (the
    // health check runs there); the public answer stays minimal.
    if (!req.headers['x-forwarded-for']) {
      body.accounts = views.map((v) => ({ agent: v.agentId, status: v.status, phone: v.phone ? `…${v.phone.slice(-4)}` : '' }));
    }
    return send(res, 200, body);
  }
  if (url.pathname === '/whatsapp/internal/send') {
    if (req.method !== 'POST') return send(res, 405, { error: 'method not allowed' });
    if (!internalCaller(req)) return send(res, 403, { error: 'internal endpoint' });
    const body = await readJson(req);
    try {
      await accounts.deliver(String(body.agentId || ''), String(body.chat || ''), String(body.text || ''));
      return send(res, 200, { ok: true });
    } catch (error) {
      return send(res, 503, { error: error.message });
    }
  }
  if (url.pathname === '/whatsapp') {
    res.writeHead(308, { Location: '/whatsapp/' });
    return res.end();
  }

  if (!(await consoleUser(cookie))) {
    if (url.pathname === '/whatsapp/') {
      res.writeHead(302, { Location: '/' });
      return res.end();
    }
    return send(res, 401, { error: 'Sign in to the LRMagent console first.' });
  }

  if (url.pathname === '/whatsapp/' && req.method === 'GET') {
    return send(res, 200, await fs.readFile(PAGE), 'text/html; charset=utf-8');
  }

  if (url.pathname === '/whatsapp/api/agents' && req.method === 'GET') {
    const agents = await listAgents(cookie);
    const rows = await Promise.all(agents.map(async (a) => ({ ...a, whatsapp: await accounts.view(a.id) })));
    return send(res, 200, { agents: rows });
  }

  const match = url.pathname.match(/^\/whatsapp\/api\/agents\/([A-Za-z0-9_-]{1,64})\/(link|qr|unlink|settings|approve)$/);
  if (!match) return send(res, 404, { error: 'not found' });
  const [, agentId, action] = match;
  const known = (await listAgents(cookie)).some((a) => a.id === agentId);
  if (!known) return send(res, 404, { error: 'unknown agent' });

  if (action === 'qr' && req.method === 'GET') {
    const view = await accounts.view(agentId);
    return send(res, 200, { status: view.status, phone: view.phone, error: view.error, qr: accounts.qr(agentId) });
  }
  if (req.method !== 'POST' && req.method !== 'PUT') return send(res, 405, { error: 'method not allowed' });
  if (!sameSite(req)) return send(res, 403, { error: 'cross-site request refused' });
  const body = await readJson(req);

  if (action === 'link') return send(res, 200, await accounts.link(agentId));
  if (action === 'unlink') {
    await accounts.unlink(agentId);
    return send(res, 200, await accounts.view(agentId));
  }
  if (action === 'settings') return send(res, 200, { settings: await accounts.saveSettings(agentId, body) });
  if (action === 'approve') {
    const phone = await accounts.approve(agentId, body.code);
    return phone ? send(res, 200, { approved: phone }) : send(res, 404, { error: 'No pending request with that code.' });
  }
  return send(res, 404, { error: 'not found' });
}

const server = http.createServer((req, res) => {
  route(req, res).catch((error) => {
    console.error(error);
    if (!res.headersSent) send(res, error.status || 500, { error: error.status ? error.message : 'internal error' });
  });
});

await accounts.restore();
server.listen(PORT, () => console.log(`whatsapp-gateway listening on :${PORT}/whatsapp/`));

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, async () => {
    server.close();
    await accounts.shutdown();
    process.exit(0);
  });
}
