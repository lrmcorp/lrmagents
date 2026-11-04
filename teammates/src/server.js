// HTTP front for the teammates, served at /teammates/ on the agent domain.
// The page and its API need a signed-in console session; /teammates/internal/*
// is for LRMagent's ask_teammate tool inside the Docker network (shared token).

import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pty from 'node-pty';
import { WebSocketServer } from 'ws';
import { Jobs, RUN_AS } from './jobs.js';
import { MODEL_OF, PLAN_MODELS, handleCompletion } from './chat.js';
import { HOME, TEAMMATES, byId, childEnv, defaultModel, modelChoices, status } from './teammates.js';

const PORT = Number(process.env.PORT || 8090);
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA = process.env.DATA_DIR || '/data';
const COWAGENT = (process.env.COWAGENT_URL || 'http://cowagent:9899').replace(/\/$/, '');
const TOKEN = process.env.COWAGENT_API_TOKEN || '';
const ASSETS = {
  'xterm.js': ['node_modules/@xterm/xterm/lib/xterm.js', 'text/javascript'],
  'xterm.css': ['node_modules/@xterm/xterm/css/xterm.css', 'text/css'],
  'addon-fit.js': ['node_modules/@xterm/addon-fit/lib/addon-fit.js', 'text/javascript'],
};

// ---- settings (chosen model per teammate) --------------------------------------
const SETTINGS_FILE = path.join(DATA, 'settings.json');
let saved = {};
try { saved = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')); } catch { /* first start */ }
// The owner's standing choice for Codex; a model saved in Config → AI plans replaces it.
const DEFAULT_MODELS = { codex: process.env.TEAMMATE_CODEX_MODEL ?? 'gpt-6-sol' };
const settings = {
  model: (id) => String(saved[id]?.model || DEFAULT_MODELS[id] || ''),
  saved: (id) => String(saved[id]?.model || ''),
  set(id, model) {
    saved[id] = { ...(saved[id] || {}), model: String(model || '').trim().slice(0, 120) };
    fs.mkdirSync(DATA, { recursive: true });
    fs.writeFileSync(SETTINGS_FILE, JSON.stringify(saved, null, 2));
  },
};
const jobs = new Jobs(settings);

// OpenCode asks before editing or running anything unless told it may: these
// teammates work unattended inside their own container.
function prepareHome() {
  const dir = path.join(HOME, '.config', 'opencode');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, 'opencode.json');
  let conf = {};
  try { conf = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { /* new */ }
  conf.$schema = conf.$schema || 'https://opencode.ai/config.json';
  conf.permission = { edit: 'allow', bash: 'allow', webfetch: 'allow', ...(conf.permission || {}) };
  conf.autoupdate = false;
  fs.writeFileSync(file, JSON.stringify(conf, null, 2));
  if (RUN_AS.uid != null) for (const p of [path.join(HOME, '.config'), dir, file]) fs.chownSync(p, RUN_AS.uid, RUN_AS.gid);
}

// ---- status cache ---------------------------------------------------------------
let cache = { at: 0, rows: [] };
async function teammateRows(force = false) {
  if (!force && Date.now() - cache.at < 60_000) return cache.rows;
  const rows = await Promise.all(TEAMMATES.map(async (t) => {
    const s = await status(t, RUN_AS).catch(() => ({ installed: false, signedIn: false }));
    const model = settings.model(t.id) || (s.signedIn && t.provider ? await defaultModel(t, RUN_AS).catch(() => '') : '');
    const models = s.signedIn ? await modelChoices(t, RUN_AS).catch(() => []) : [];
    return { id: t.id, name: t.name, maker: t.maker, plan: t.plan, blurb: t.blurb, loginHelp: t.loginHelp, model, models, modelSet: Boolean(settings.saved(t.id)), ...s };
  }));
  cache = { at: Date.now(), rows };
  return rows;
}

// ---- helpers ---------------------------------------------------------------------
function send(res, code, body, type = 'application/json; charset=utf-8') {
  const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin' });
  res.end(data);
}

async function readJson(req, limit = 64 * 1024) {
  let size = 0; const parts = [];
  for await (const part of req) {
    size += part.length;
    if (size > limit) throw Object.assign(new Error('body too large'), { status: 413 });
    parts.push(part);
  }
  try { return parts.length ? JSON.parse(Buffer.concat(parts).toString('utf8')) : {}; } catch { throw Object.assign(new Error('invalid JSON'), { status: 400 }); }
}

function internalCaller(req) {
  if (!TOKEN || req.headers['x-forwarded-for']) return false;
  const given = Buffer.from(String(req.headers.authorization || '').replace(/^Bearer\s+/i, ''));
  const want = Buffer.from(TOKEN);
  return given.length === want.length && crypto.timingSafeEqual(given, want);
}

// Writes from the page: JSON, same host.
function sameSite(req, json = true) {
  if (json && !String(req.headers['content-type'] || '').startsWith('application/json')) return false;
  const origin = req.headers.origin;
  if (!origin) return true;
  try { return new URL(origin).host === (req.headers['x-forwarded-host'] || req.headers.host); } catch { return false; }
}

// The console's own sign-in decides who may use this page.
async function consoleUser(cookie) {
  try {
    const r = await fetch(`${COWAGENT}/auth/check`, { headers: cookie ? { Cookie: cookie } : {}, signal: AbortSignal.timeout(5000) });
    const body = await r.json();
    return body?.auth_required === false || body?.authenticated === true;
  } catch { return false; }
}

// ---- routes ----------------------------------------------------------------------
async function route(req, res) {
  const url = new URL(req.url, 'http://teammates');
  const p = url.pathname;

  if (p === '/teammates/health') return send(res, 200, { ok: true, running: jobs.list(200).filter((j) => j.status === 'running').length });

  // The signed-in plans as an OpenAI-compatible model provider ("Your plans" in LRMagent).
  if (p.startsWith('/teammates/v1/')) {
    if (!internalCaller(req)) return send(res, 403, { error: { message: 'internal endpoint' } });
    if (p === '/teammates/v1/models' && req.method === 'GET') {
      const rows = (await teammateRows()).filter((t) => t.signedIn && MODEL_OF[t.id]);
      return send(res, 200, { object: 'list', data: rows.map((t) => ({ id: MODEL_OF[t.id], object: 'model', owned_by: t.maker, name: `${t.name} · ${t.plan}` })) });
    }
    if (p === '/teammates/v1/chat/completions' && req.method === 'POST') {
      const body = await readJson(req, 8 << 20);
      const row = (await teammateRows()).find((t) => t.id === PLAN_MODELS[body.model]);
      if (row && !row.signedIn) return send(res, 409, { error: { message: `${row.name} is not signed in yet. Sign in once on Config → AI plans in the console.` } });
      return handleCompletion(req, res, body, settings);
    }
    return send(res, 404, { error: { message: 'not found' } });
  }

  if (p.startsWith('/teammates/internal/')) {
    if (!internalCaller(req)) return send(res, 403, { error: 'internal endpoint' });
    if (p === '/teammates/internal/state' && req.method === 'GET') return send(res, 200, { teammates: await teammateRows() });
    if (p === '/teammates/internal/jobs' && req.method === 'POST') {
      const body = await readJson(req);
      const row = (await teammateRows()).find((t) => t.id === body.teammate);
      if (row && !row.signedIn) return send(res, 409, { error: `${row.name} is not signed in yet. Sign in once on Config → AI plans in the console.` });
      return send(res, 200, { job: jobs.create({ ...body, source: body.notify ? 'whatsapp' : 'agent' }) });
    }
    const m = p.match(/^\/teammates\/internal\/jobs\/([a-f0-9]+)(\/stop|\/ack)?$/);
    if (m) {
      const job = jobs.get(m[1]);
      if (!job) return send(res, 404, { error: 'no such task' });
      if (m[2] === '/stop' && req.method === 'POST') return send(res, 200, { job: jobs.stop(job.id) });
      if (m[2] === '/ack' && req.method === 'POST') return send(res, 200, { ok: jobs.ack(job.id) });
      return send(res, 200, { job: jobs.view(job, url.searchParams.get('log') === '1') });
    }
    return send(res, 404, { error: 'not found' });
  }

  if (p === '/teammates') { res.writeHead(308, { Location: '/teammates/' }); return res.end(); }
  if (!(await consoleUser(req.headers.cookie || ''))) {
    if (p === '/teammates/') { res.writeHead(302, { Location: '/' }); return res.end(); }
    return send(res, 401, { error: 'Sign in to the LRMagent console first.' });
  }

  if (p === '/teammates/' && req.method === 'GET') return send(res, 200, fs.readFileSync(path.join(ROOT, 'public', 'index.html')), 'text/html; charset=utf-8');
  const asset = p.match(/^\/teammates\/assets\/([\w.-]+)$/);
  if (asset && ASSETS[asset[1]]) {
    const [file, type] = ASSETS[asset[1]];
    return send(res, 200, fs.readFileSync(path.join(ROOT, file)), `${type}; charset=utf-8`);
  }
  if (p === '/teammates/api/state' && req.method === 'GET') {
    return send(res, 200, { teammates: await teammateRows(url.searchParams.get('refresh') === '1'), jobs: jobs.list() });
  }
  if (req.method === 'POST' && !sameSite(req)) return send(res, 403, { error: 'cross-site request refused' });
  if (p === '/teammates/api/jobs' && req.method === 'POST') {
    const body = await readJson(req);
    return send(res, 200, { job: jobs.create({ teammate: body.teammate, task: body.task, folder: body.folder, fresh: body.fresh, source: 'console' }) });
  }
  if (p === '/teammates/api/settings' && req.method === 'POST') {
    const body = await readJson(req);
    if (!byId(body.teammate)) return send(res, 400, { error: 'unknown teammate' });
    settings.set(body.teammate, body.model);
    cache.at = 0;
    return send(res, 200, { ok: true });
  }
  const m = p.match(/^\/teammates\/api\/jobs\/([a-f0-9]+)(\/stop)?$/);
  if (m) {
    const job = jobs.get(m[1]);
    if (!job) return send(res, 404, { error: 'no such task' });
    if (m[2] && req.method === 'POST') return send(res, 200, { job: jobs.stop(job.id) });
    return send(res, 200, { job: jobs.view(job, true) });
  }
  return send(res, 404, { error: 'not found' });
}

const server = http.createServer((req, res) => {
  route(req, res).catch((e) => send(res, e.status || 500, { error: e.message || 'error' }));
});

// ---- terminals: sign-in flows and a shell, in a real terminal --------------------
const wss = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 });
const terminals = new Set();
server.on('upgrade', async (req, socket, head) => {
  const url = new URL(req.url, 'http://teammates');
  if (url.pathname !== '/teammates/api/terminal' || !sameSite(req, false) || !(await consoleUser(req.headers.cookie || '')) || terminals.size >= 3) {
    socket.write('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
    return socket.destroy();
  }
  wss.handleUpgrade(req, socket, head, (ws) => openTerminal(ws, url.searchParams));
});

function openTerminal(ws, params) {
  const t = byId(params.get('teammate'));
  const shell = params.get('mode') === 'shell';
  const args = shell || !t ? ['bash', '-l'] : t.login;
  const cols = Math.min(Math.max(Number(params.get('cols')) || 100, 40), 240);
  const rows = Math.min(Math.max(Number(params.get('rows')) || 30, 10), 80);
  let term;
  try {
    term = pty.spawn(args[0], args.slice(1), { name: 'xterm-256color', cols, rows, cwd: HOME, env: childEnv(t), uid: RUN_AS.uid, gid: RUN_AS.gid });
  } catch (e) {
    ws.send(`\r\nCould not start: ${e.message}\r\n`);
    return ws.close();
  }
  terminals.add(term);
  const idle = () => setTimeout(() => term.kill(), 20 * 60_000);
  let timer = idle();
  term.onData((d) => { if (ws.readyState === 1) ws.send(d); });
  term.onExit(({ exitCode }) => {
    terminals.delete(term);
    cache.at = 0; // a sign-in may have just finished
    if (ws.readyState === 1) { ws.send(`\r\n\x1b[2m[finished${exitCode ? `, code ${exitCode}` : ''}]\x1b[0m\r\n`); ws.close(); }
  });
  ws.on('message', (raw) => {
    clearTimeout(timer); timer = idle();
    const text = raw.toString();
    if (text.startsWith('\x00resize:')) {
      const [c, r] = text.slice(8).split('x').map(Number);
      if (c > 0 && r > 0) term.resize(Math.min(c, 240), Math.min(r, 80));
      return;
    }
    term.write(text);
  });
  ws.on('close', () => { clearTimeout(timer); try { term.kill(); } catch { /* gone */ } terminals.delete(term); });
}

prepareHome();
server.listen(PORT, () => console.log(`teammates listening on ${PORT}`));
