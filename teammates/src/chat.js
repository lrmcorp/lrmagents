// The teammates as chat models: an OpenAI-compatible /teammates/v1/chat/completions
// that LRMagent lists as the "Your plans" provider. Each chat (LRMagent session)
// gets its own folder, so the CLI continues that conversation's session; only the
// newest user message is sent, since the CLI remembers the rest.
//
// Owner only, like ask_teammate: console chats, the owner's own WhatsApp chat and
// LRMagent's morning brief. Other WhatsApp contacts and API callers are refused.

import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { RUN_AS, explain } from './jobs.js';
import { HOME, byId, childEnv, defaultModel, prepareFolder, runArgs } from './teammates.js';

const GATEWAY = (process.env.WHATSAPP_GATEWAY_URL || 'http://whatsapp-gateway:8080').replace(/\/$/, '');
const TOKEN = process.env.COWAGENT_API_TOKEN || '';
const CHAT_TIMEOUT_MS = Number(process.env.TEAMMATES_CHAT_TIMEOUT_MIN || 20) * 60_000;
const WHATSAPP = 'openai:conversation:whatsapp:';

// Model names in LRMagent's picker -> teammate ids.
export const PLAN_MODELS = { codex: 'codex', 'claude-code': 'claude', gemini: 'gemini', grok: 'grok', 'deepseek-opencode': 'deepseek' };
export const MODEL_OF = Object.fromEntries(Object.entries(PLAN_MODELS).map(([m, id]) => [id, m]));

// One turn at a time per chat folder: a CLI session cannot take two prompts at once.
const busy = new Map();
function serial(key, fn) {
  const prev = busy.get(key) || Promise.resolve();
  const next = prev.catch(() => {}).then(fn);
  busy.set(key, next);
  next.finally(() => { if (busy.get(key) === next) busy.delete(key); }).catch(() => {});
  return next;
}

const PREAMBLE = `You are the owner's assistant inside LRMagent (Liando Reyfort Management), answering in its chat
(the console or the owner's WhatsApp). Reply conversationally and concisely, in the language the
owner writes in. Use your own tools in this folder when a request needs them (code, files, web), then
answer with the result. Never print secrets.

`;

function textOf(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.map((p) => (typeof p === 'string' ? p : p?.text || '')).filter(Boolean).join('\n');
  return '';
}

export async function allowedSession(session) {
  if (!session) return { ok: false, why: 'This chat did not identify itself, so your plan was not used.' };
  const OWNER = ['openai:conversation:lrm-brief:', 'openai:conversation:lrm-voice:', 'openai:conversation:lrm-report:', 'openai:conversation:lrm-review:'];
  if (!session.startsWith('openai:') || OWNER.some((p) => session.startsWith(p))) return { ok: true };
  if (!session.startsWith(WHATSAPP)) return { ok: false, why: 'Your plans can only be used from the LRMagent console or your own WhatsApp chat.' };
  const [agent, ...rest] = session.slice(WHATSAPP.length).split(':');
  try {
    const r = await fetch(`${GATEWAY}/whatsapp/internal/owner?${new URLSearchParams({ agent, chat: rest.join(':') })}`,
      { headers: { Authorization: `Bearer ${TOKEN}` }, signal: AbortSignal.timeout(10_000) });
    if ((await r.json()).owner === true) return { ok: true };
  } catch { /* refuse below */ }
  return { ok: false, why: 'Your plans can only be used from the LRMagent console or your own WhatsApp chat ("Message yourself").' };
}

function run(t, { dir, prompt, model, resume, lastFile }) {
  return new Promise((resolve) => {
    const args = runArgs(t, { prompt, model, resume, lastMessageFile: lastFile });
    const proc = spawn(args[0], args.slice(1), { cwd: dir, env: childEnv(t), uid: RUN_AS.uid, gid: RUN_AS.gid, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    const take = (c) => { out = (out + c.toString('utf8')).slice(-400_000); };
    proc.stdout.on('data', take);
    proc.stderr.on('data', take);
    const started = Date.now();
    const timer = setTimeout(() => { try { process.kill(-proc.pid, 'SIGTERM'); } catch { /* gone */ } }, CHAT_TIMEOUT_MS);
    proc.on('error', (e) => take(Buffer.from(String(e.message))));
    proc.on('close', (code) => { clearTimeout(timer); resolve({ code, out: out.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '').replace(/\r/g, ''), quick: Date.now() - started < 20_000 }); });
  });
}

// ---- LRMagent's tools, offered to the CLI ------------------------------------------
// The CLI can't call LRMagent's tools directly, so they are listed in the prompt and the
// CLI asks for them with <lrm_tool>{...}</lrm_tool>; those become OpenAI tool_calls, and
// the results come back in the next request as role "tool" messages.

function toolCatalogue(tools) {
  const lines = [];
  for (const entry of tools || []) {
    const fn = entry?.function || entry;
    if (!fn?.name) continue;
    const props = fn.parameters?.properties || {};
    const required = new Set(fn.parameters?.required || []);
    const params = Object.entries(props).map(([k, v]) => {
      const kind = v?.enum ? v.enum.map((e) => JSON.stringify(e)).join('|') : (v?.type || 'any');
      const note = v?.description ? ` - ${String(v.description).replace(/\s+/g, ' ').slice(0, 140)}` : '';
      return `    ${k}${required.has(k) ? '' : '?'}: ${kind}${note}`;
    });
    lines.push(`- ${fn.name}: ${String(fn.description || '').replace(/\s+/g, ' ').slice(0, 400)}${params.length ? '\n' + params.join('\n') : ''}`);
  }
  return lines.join('\n');
}

const TOOL_RULES = `LRMagent has its own tools, listed below. They act on LRMagent itself: its WhatsApp number, memory and
knowledge vault, the owner's Google account, reminders (scheduler), the browser, files it sends, and more.
Use them whenever the request involves those things; your own shell runs in a separate folder that is not
LRMagent's workspace. To call LRMagent tools, reply with ONLY one or more lines like
<lrm_tool>{"name": "whatsapp", "arguments": {"action": "send", "to": "me", "text": "Hi!"}}</lrm_tool>
and nothing else. The results arrive in the next message; then continue, call more tools, or answer.

LRMagent tools:
`;

function parseToolCalls(text, names) {
  const calls = [];
  const rest = text.replace(/<lrm_tool>\s*([\s\S]*?)\s*<\/lrm_tool>/g, (whole, raw) => {
    try {
      const data = JSON.parse(raw.replace(/^```(?:json)?\s*|\s*```$/g, ''));
      const name = String(data.name || data.tool || '');
      if (names.has(name)) {
        calls.push({ id: `call_${crypto.randomBytes(6).toString('hex')}`, type: 'function',
          function: { name, arguments: JSON.stringify(data.arguments ?? data.args ?? {}) } });
        return '';
      }
    } catch { /* leave it in the text */ }
    return whole;
  });
  return { calls, rest: rest.trim() };
}

function clip(text, limit) {
  text = String(text || '');
  return text.length > limit ? `${text.slice(0, limit)} […${text.length - limit} more characters]` : text;
}

// What the CLI has not seen yet: everything after the last assistant message.
function newInput(messages) {
  const list = messages || [];
  let i = list.length;
  while (i > 0 && list[i - 1]?.role !== 'assistant') i -= 1;
  const fresh = list.slice(i).filter((m) => m?.role === 'user' || m?.role === 'tool');
  const parts = fresh.map((m) => (m.role === 'tool'
    ? `Result of LRMagent tool ${m.name || ''} (${m.tool_call_id || ''}):\n${clip(textOf(m.content), 12_000)}`
    : textOf(m.content)));
  return parts.filter((p) => p && p.trim()).join('\n\n').trim();
}

// For a CLI session that starts in the middle of a conversation (a fallback, a reset):
// the recent conversation, so it knows what is going on.
function history(messages) {
  const list = (messages || []).filter((m) => m?.role === 'user' || m?.role === 'assistant');
  let i = list.length;
  while (i > 0 && list[i - 1]?.role !== 'assistant') i -= 1;
  const earlier = list.slice(0, i).slice(-16).map((m) => `${m.role === 'user' ? 'Owner' : 'Assistant'}: ${clip(textOf(m.content), 2000)}`);
  return earlier.length ? `Conversation so far:\n${earlier.join('\n\n')}\n\n` : '';
}

// One chat turn on a plan. Returns { text, toolCalls } or throws { status, message }.
export async function chatTurn(modelName, messages, session, settings, tools = []) {
  const id = PLAN_MODELS[modelName];
  const t = id && byId(id);
  if (!t) throw Object.assign(new Error(`Unknown plan model "${modelName}". Choose one of Your plans in the model list.`), { status: 404 });
  const allowed = await allowedSession(session);
  if (!allowed.ok) throw Object.assign(new Error(allowed.why), { status: 403 });
  const input = newInput(messages);
  if (!input) throw Object.assign(new Error('There was no message to answer.'), { status: 400 });
  const folder = `chat-${crypto.createHash('sha256').update(session).digest('hex').slice(0, 16)}`;
  return serial(`${t.id}/${folder}`, () => turn(t, folder, { input, messages, tools }, settings));
}

async function turn(t, folder, { input, messages, tools }, settings) {
  const dir = prepareFolder(t, folder, RUN_AS.uid, RUN_AS.gid);
  const started = path.join(dir, '.lrm-chat-started');
  const toolsFile = path.join(dir, '.lrm-chat-tools');
  const first = !fs.existsSync(started);
  const catalogue = toolCatalogue(tools);
  const toolHash = crypto.createHash('sha256').update(catalogue).digest('hex').slice(0, 16);
  let knownHash = '';
  try { knownHash = fs.readFileSync(toolsFile, 'utf8').trim(); } catch { /* first time */ }
  const names = new Set((tools || []).map((e) => (e?.function || e)?.name).filter(Boolean));
  let model = settings.model(t.id);
  if (!model && t.provider) model = await defaultModel(t, RUN_AS);
  const lastDir = path.join(HOME, '.lrm-last');
  fs.mkdirSync(lastDir, { recursive: true });
  if (RUN_AS.uid != null) fs.chownSync(lastDir, RUN_AS.uid, RUN_AS.gid);
  const lastFile = path.join(lastDir, `chat-${crypto.randomBytes(6).toString('hex')}.txt`);

  const toolPart = (fresh) => (catalogue && (fresh || toolHash !== knownHash) ? `${TOOL_RULES}${catalogue}\n\n` : '');
  const opening = () => `${PREAMBLE}${toolPart(true)}${history(messages)}`;
  const prompt = first ? opening() + input : toolPart(false) + input;
  let r = await run(t, { dir, prompt, model, resume: !first, lastFile: t.id === 'codex' ? lastFile : '' });
  if (!first && r.code !== 0 && r.quick) r = await run(t, { dir, prompt: opening() + input, model, resume: false, lastFile: t.id === 'codex' ? lastFile : '' });
  let text = '';
  try { text = fs.readFileSync(lastFile, 'utf8').trim(); fs.rmSync(lastFile, { force: true }); } catch { /* not codex */ }
  if (r.code !== 0) {
    // 429 for a spent plan and 401 for a lost sign-in: LRMagent moves straight to the
    // next model on those, where a 502 would be retried on this plan three times first.
    const why = explain(r.out, t);
    throw Object.assign(new Error(why), { status: /usage limit/.test(why) ? 429 : /not signed in/.test(why) ? 401 : 502 });
  }
  if (!text) text = r.out.trim();
  fs.writeFileSync(started, new Date().toISOString());
  if (catalogue) fs.writeFileSync(toolsFile, toolHash);
  const { calls, rest } = names.size ? parseToolCalls(text, names) : { calls: [], rest: text };
  return { text: rest || (calls.length ? '' : '(No reply.)'), toolCalls: calls };
}

function completion(model, { text, toolCalls }) {
  const message = { role: 'assistant', content: text || (toolCalls.length ? null : '') };
  if (toolCalls.length) message.tool_calls = toolCalls;
  return { id: `chatcmpl-${crypto.randomBytes(8).toString('hex')}`, object: 'chat.completion', created: Math.floor(Date.now() / 1000), model,
    choices: [{ index: 0, message, finish_reason: toolCalls.length ? 'tool_calls' : 'stop' }],
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } };
}

// POST /teammates/v1/chat/completions (stream or not).
export async function handleCompletion(req, res, body, settings) {
  const session = String(req.headers['x-lrm-session'] || '');
  const model = String(body.model || '');
  const tools = Array.isArray(body.tools) && body.tool_choice !== 'none' ? body.tools : [];
  const fail = (e) => ({ error: { message: e.message, type: 'lrm_plan_error', code: e.status || 500 } });
  if (!body.stream) {
    try {
      const answer = await chatTurn(model, body.messages, session, settings, tools);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(completion(model, answer)));
    } catch (e) {
      res.writeHead(e.status || 500, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(fail(e)));
    }
  }
  // Streaming: keep the connection alive while the CLI works, then send the answer.
  let ready;
  try {
    const allowed = PLAN_MODELS[model] ? await allowedSession(session) : { ok: false, why: `Unknown plan model "${model}".` };
    if (!allowed.ok) throw Object.assign(new Error(allowed.why), { status: PLAN_MODELS[model] ? 403 : 404 });
    ready = chatTurn(model, body.messages, session, settings, tools);
  } catch (e) {
    res.writeHead(e.status || 500, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(fail(e)));
  }
  ready.catch(() => {}); // handled below; never an unhandled rejection meanwhile
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
  const id = `chatcmpl-${crypto.randomBytes(8).toString('hex')}`;
  const chunk = (delta, finish = null) => res.write(`data: ${JSON.stringify({ id, object: 'chat.completion.chunk', created: Math.floor(Date.now() / 1000), model, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`);
  chunk({ role: 'assistant' });
  const ping = setInterval(() => res.write(': working\n\n'), 15_000);
  try {
    const { text, toolCalls } = await ready;
    clearInterval(ping);
    if (text) chunk({ content: text });
    toolCalls.forEach((call, index) => chunk({ tool_calls: [{ index, ...call }] }));
    chunk({}, toolCalls.length ? 'tool_calls' : 'stop');
  } catch (e) {
    clearInterval(ping);
    res.write(`data: ${JSON.stringify(fail(e))}\n\n`);
  }
  res.write('data: [DONE]\n\n');
  res.end();
}
