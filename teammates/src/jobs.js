// Tasks handed to teammates: one at a time per teammate, at most MAX_RUNNING at
// once, each with a log and a result kept on disk so they survive restarts.

import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { HOME, byId, childEnv, defaultModel, prepareFolder, runArgs } from './teammates.js';

const DATA = process.env.DATA_DIR || '/data';
const JOBS = path.join(DATA, 'jobs');
const MAX_RUNNING = Number(process.env.TEAMMATES_MAX_RUNNING || 2);
const TIME_LIMIT_MS = Number(process.env.TEAMMATES_TIME_LIMIT_MIN || 60) * 60_000;
const LOG_CAP = 2 << 20;
const GATEWAY = (process.env.WHATSAPP_GATEWAY_URL || 'http://whatsapp-gateway:8080').replace(/\/$/, '');
const TOKEN = process.env.COWAGENT_API_TOKEN || '';
// The server runs as root so its environment (the internal token) is out of the
// teammates' reach; they run as the unprivileged node user with a clean environment.
export const RUN_AS = process.getuid?.() === 0 ? { uid: Number(process.env.TEAMMATE_UID || 1000), gid: Number(process.env.TEAMMATE_GID || 1000) } : {};

const strip = (s) => s.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '').replace(/\r/g, '');

export class Jobs {
  constructor(settings) {
    this.settings = settings;
    this.jobs = new Map();
    this.procs = new Map();
    fs.mkdirSync(JOBS, { recursive: true });
    for (const name of fs.readdirSync(JOBS).filter((f) => f.endsWith('.json'))) {
      try {
        const job = JSON.parse(fs.readFileSync(path.join(JOBS, name), 'utf8'));
        // Anything left running by a restart did not finish.
        if (job.status === 'running' || job.status === 'queued') Object.assign(job, { status: 'failed', error: 'LRMagent restarted while this task was running.', finishedAt: Date.now() });
        this.jobs.set(job.id, job);
      } catch { /* skip a damaged record */ }
    }
    this.prune();
  }

  list(limit = 50) {
    return [...this.jobs.values()].sort((a, b) => b.createdAt - a.createdAt).slice(0, limit).map((j) => this.view(j));
  }

  view(job, withLog = false) {
    const { notify, ...rest } = job;
    const out = { ...rest, fromWhatsApp: Boolean(notify) };
    if (withLog) out.log = this.log(job.id);
    return out;
  }

  get(id) { return this.jobs.get(id); }

  log(id, max = 60_000) {
    try {
      const text = fs.readFileSync(path.join(JOBS, `${id}.log`), 'utf8');
      return text.length > max ? `…\n${text.slice(-max)}` : text;
    } catch { return ''; }
  }

  save(job) {
    fs.writeFileSync(path.join(JOBS, `${job.id}.json`), JSON.stringify(job, null, 2));
  }

  prune(keep = 200) {
    const old = [...this.jobs.values()].sort((a, b) => b.createdAt - a.createdAt).slice(keep);
    for (const job of old) {
      this.jobs.delete(job.id);
      for (const ext of ['.json', '.log']) fs.rmSync(path.join(JOBS, `${job.id}${ext}`), { force: true });
    }
  }

  create({ teammate, task, folder = 'main', fresh = false, source = 'console', notify = null, wait = false }) {
    const t = byId(teammate);
    if (!t) throw Object.assign(new Error(`Unknown teammate "${teammate}".`), { status: 400 });
    task = String(task || '').trim();
    if (!task) throw Object.assign(new Error('Say what the task is.'), { status: 400 });
    if (task.length > 20_000) throw Object.assign(new Error('That task description is too long.'), { status: 400 });
    prepareFolder(t, folder, RUN_AS.uid, RUN_AS.gid); // validates the folder name too
    const job = {
      id: crypto.randomBytes(6).toString('hex'), teammate: t.id, task, folder, fresh: Boolean(fresh), source,
      status: 'queued', createdAt: Date.now(), startedAt: null, finishedAt: null, result: '', error: '', notify, wait: Boolean(wait), acked: false,
    };
    this.jobs.set(job.id, job);
    this.save(job);
    this.prune();
    setImmediate(() => this.pump());
    return this.view(job);
  }

  stop(id) {
    const job = this.jobs.get(id);
    if (!job) return null;
    if (job.status === 'queued') Object.assign(job, { status: 'stopped', finishedAt: Date.now() });
    const proc = this.procs.get(id);
    if (proc) { job.stopping = true; try { process.kill(-proc.pid, 'SIGTERM'); } catch { proc.kill('SIGTERM'); } }
    this.save(job);
    return this.view(job);
  }

  // The agent already passed the result on, so no WhatsApp message is needed.
  ack(id) {
    const job = this.jobs.get(id);
    if (job) { job.acked = true; this.save(job); }
    return Boolean(job);
  }

  pump() {
    const running = [...this.jobs.values()].filter((j) => j.status === 'running');
    if (running.length >= MAX_RUNNING) return;
    const busy = new Set(running.map((j) => j.teammate));
    const next = [...this.jobs.values()].filter((j) => j.status === 'queued' && !busy.has(j.teammate)).sort((a, b) => a.createdAt - b.createdAt)[0];
    if (next) this.start(next).catch((e) => this.finish(next, 'failed', '', e.message)).finally(() => this.pump());
  }

  async start(job) {
    const t = byId(job.teammate);
    const dir = prepareFolder(t, job.folder, RUN_AS.uid, RUN_AS.gid);
    let model = this.settings.model(t.id);
    if (!model && t.provider) model = await defaultModel(t, RUN_AS);
    if (t.provider && !model) throw new Error(`${t.name} is not signed in, so no model is available. Sign in on the Teammates page.`);
    Object.assign(job, { status: 'running', startedAt: Date.now(), model });
    this.save(job);
    const logFile = path.join(JOBS, `${job.id}.log`);
    // Codex writes its final answer to a file; it can only write where the teammate user may.
    const lastDir = path.join(HOME, '.lrm-last');
    fs.mkdirSync(lastDir, { recursive: true });
    if (RUN_AS.uid != null) fs.chownSync(lastDir, RUN_AS.uid, RUN_AS.gid);
    const lastFile = path.join(lastDir, `${job.id}.txt`);
    fs.writeFileSync(logFile, `▶ ${t.name} · ${job.folder}${model ? ` · ${model}` : ''}\n▶ ${job.task}\n\n`);

    const attempt = (resume) => new Promise((resolve) => {
      const args = runArgs(t, { prompt: job.task, model, resume, lastMessageFile: t.id === 'codex' ? lastFile : '' });
      const proc = spawn(args[0], args.slice(1), { cwd: dir, env: childEnv(t), uid: RUN_AS.uid, gid: RUN_AS.gid, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
      this.procs.set(job.id, proc);
      let size = 0; let out = '';
      const write = (chunk) => {
        const text = strip(chunk.toString('utf8'));
        out = (out + text).slice(-200_000);
        if (size < LOG_CAP) { size += text.length; fs.appendFileSync(logFile, text); }
      };
      proc.stdout.on('data', write);
      proc.stderr.on('data', write);
      const timer = setTimeout(() => { job.timedOut = true; try { process.kill(-proc.pid, 'SIGTERM'); } catch { /* gone */ } }, TIME_LIMIT_MS);
      const started = Date.now();
      proc.on('error', (e) => { write(Buffer.from(`\n${e.message}\n`)); });
      proc.on('close', (code) => { clearTimeout(timer); this.procs.delete(job.id); resolve({ code, out, quick: Date.now() - started < 20_000 }); });
    });

    // Continue the previous session in this folder; a teammate with none yet
    // fails fast, and then starts fresh.
    let r = await attempt(!job.fresh);
    if (!job.fresh && r.code !== 0 && r.quick && !job.stopping) {
      fs.appendFileSync(logFile, '\n▶ No earlier session to continue here; starting fresh.\n\n');
      r = await attempt(false);
    }
    let result = '';
    try { result = fs.readFileSync(lastFile, 'utf8').trim(); fs.rmSync(lastFile, { force: true }); } catch { /* not codex */ }
    if (!result) result = r.out.trim().split('\n').slice(-80).join('\n').trim();
    if (job.stopping) return this.finish(job, 'stopped', result, 'Stopped.');
    if (job.timedOut) return this.finish(job, 'failed', result, `Stopped after ${TIME_LIMIT_MS / 60000} minutes.`);
    return r.code === 0 ? this.finish(job, 'done', result, '') : this.finish(job, 'failed', result, explain(r.out, t));
  }

  finish(job, status, result, error) {
    Object.assign(job, { status, result: String(result || '').slice(-20_000), error, finishedAt: Date.now() });
    delete job.stopping;
    this.save(job);
    if (job.notify) setTimeout(() => this.notify(job).catch(() => {}), job.wait ? 15_000 : 0);
  }

  async notify(job) {
    if (job.acked || !TOKEN || !job.notify?.chat) return;
    const t = byId(job.teammate);
    const head = job.status === 'done' ? `✅ ${t.name} finished: ${job.task.slice(0, 120)}` : `⚠️ ${t.name} could not finish: ${job.task.slice(0, 120)}\n${job.error}`;
    const text = `${head}\n\n${(job.result || '(no summary)').slice(0, 3500)}`;
    await fetch(`${GATEWAY}/whatsapp/internal/send`, {
      method: 'POST', headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ agentId: job.notify.agentId, chat: job.notify.chat, text }), signal: AbortSignal.timeout(30_000),
    });
  }
}

// Plain-language reasons for the usual failures.
export function explain(output, teammate) {
  const low = String(output || '').toLowerCase();
  if (/not logged in|please log in|login required|unauthori[sz]ed|401|invalid api key|authentication|no credentials|run .*login/.test(low)) return `${teammate.name} is not signed in (or its sign-in expired). Sign in again on the Teammates page.`;
  if (/usage limit|rate limit|quota|429|limit reached|out of credits|insufficient/.test(low)) return `${teammate.name} hit its plan's usage limit. Try again later or use another teammate.`;
  if (/enotfound|econnrefused|network|timed out|timeout/.test(low)) return `${teammate.name} could not reach its service. Try again in a moment.`;
  return `${teammate.name} stopped with an error. The log on the Teammates page has the details.`;
}
