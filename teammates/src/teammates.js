// The coding teammates: which CLI each one is, how it signs in, how to tell it
// is signed in, and the command line that hands it one task.

import { execFile } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

export const HOME = process.env.TEAMMATE_HOME || '/home/node';
export const WORK = process.env.TEAMMATE_WORK || '/work';

export const TEAMMATES = [
  {
    id: 'codex', name: 'Codex', maker: 'OpenAI', plan: 'ChatGPT plan', cli: 'codex',
    blurb: 'Writes and fixes code, runs tests, works through repositories.',
    login: ['codex', 'login', '--device-auth'],
    loginHelp: 'Open the link, sign in with your ChatGPT account and enter the code shown here. If it says device codes are off, turn on "device code authorization for Codex" in ChatGPT → Settings → Security first.',
  },
  {
    id: 'claude', name: 'Claude Code', maker: 'Anthropic', plan: 'Claude plan', cli: 'claude',
    blurb: 'Careful coding, refactors and writing; reads whole projects.',
    login: ['claude', 'auth', 'login', '--claudeai'],
    loginHelp: 'Open the link, sign in with your Claude account, then paste the code it gives you here and press Enter.',
  },
  {
    id: 'gemini', name: 'Gemini', maker: 'Google', plan: 'Google account', cli: 'gemini',
    blurb: 'Large-context research and coding with Google models.',
    login: ['gemini'],
    loginHelp: 'Choose "Login with Google", open the link, sign in and paste the code here. When it shows the prompt, type /quit.',
  },
  {
    id: 'grok', name: 'Grok', maker: 'xAI', plan: 'xAI account', cli: 'opencode', provider: 'xai',
    blurb: 'Grok models through OpenCode.',
    login: ['opencode', 'auth', 'login'],
    loginHelp: 'Pick xAI from the list, then follow the sign-in it shows (or paste an xAI API key).',
  },
  {
    id: 'deepseek', name: 'DeepSeek', maker: 'DeepSeek', plan: 'API key', cli: 'opencode', provider: 'deepseek',
    blurb: 'DeepSeek models through OpenCode, on your DeepSeek API key.',
    login: ['opencode', 'auth', 'login'],
    loginHelp: 'Uses the DEEPSEEK_API_KEY secret when it is set. Otherwise pick DeepSeek from the list and paste your API key.',
  },
];

export const byId = (id) => TEAMMATES.find((t) => t.id === id);

// Folder names a task may work in: one path segment, nothing hidden.
export function safeFolder(name) {
  const value = String(name || 'main').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value)) throw Object.assign(new Error('Folder names use letters, numbers, dot, dash and underscore.'), { status: 400 });
  return value;
}

export function workdir(teammate, folder) {
  return path.join(WORK, teammate.id, safeFolder(folder));
}

// The environment a teammate runs with: nothing of the server's own (no internal
// token), only what the CLI needs, plus the one provider key it uses.
export function childEnv(teammate, extra = {}) {
  const env = {
    HOME, USER: 'node', PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin', LANG: 'C.UTF-8',
    TERM: 'xterm-256color', COLORTERM: 'truecolor', NO_BROWSER: 'true', BROWSER: 'false',
    CI: '', TZ: process.env.TZ || 'Asia/Jakarta', ...extra,
  };
  if (teammate?.id === 'deepseek' && process.env.DEEPSEEK_API_KEY) env.DEEPSEEK_API_KEY = process.env.DEEPSEEK_API_KEY;
  for (const k of ['HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'https_proxy', 'http_proxy', 'no_proxy']) if (process.env[k]) env[k] = process.env[k];
  return env;
}

// The newest general model of a provider from `opencode models <provider>`.
export function pickModel(provider, lines) {
  const names = lines.map((l) => l.trim()).filter((l) => l.startsWith(`${provider}/`));
  const skip = /imagine|image|video|vision|embed|non-reasoning|multi-agent|mini|fast|flash|build/i;
  const good = names.filter((n) => !skip.test(n));
  const ver = (n) => (n.match(/(\d+(?:\.\d+)*)/g) || ['0']).map((v) => v.split('.').map(Number));
  const cmp = (a, b) => {
    const [x] = ver(a); const [y] = ver(b);
    for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] || 0) !== (y[i] || 0)) return (y[i] || 0) - (x[i] || 0);
    return a.length - b.length;
  };
  return [...(good.length ? good : names)].sort(cmp)[0] || '';
}

// The command line for one task. `resume` continues the teammate's previous
// session in that folder, so it remembers what it was doing.
export function runArgs(teammate, { prompt, model = '', resume = false, lastMessageFile = '' }) {
  switch (teammate.id) {
    case 'codex':
      return ['codex', 'exec', ...(resume ? ['resume', '--last'] : []), '--skip-git-repo-check',
        '--dangerously-bypass-approvals-and-sandbox', ...(lastMessageFile ? ['-o', lastMessageFile] : []),
        ...(model ? ['-m', model] : []), prompt];
    case 'claude':
      return ['claude', '-p', prompt, '--dangerously-skip-permissions', '--output-format', 'text',
        ...(resume ? ['--continue'] : []), ...(model ? ['--model', model] : [])];
    case 'gemini':
      return ['gemini', '-p', prompt, '--yolo', '-o', 'text', ...(resume ? ['--resume', 'latest'] : []),
        ...(model ? ['-m', model] : [])];
    default: // grok, deepseek: OpenCode
      return ['opencode', 'run', ...(resume ? ['--continue'] : []), ...(model ? ['-m', model] : []), prompt];
  }
}

// Each teammate's standing instructions, in the file its CLI reads by itself.
export const MEMORY_FILE = { codex: 'AGENTS.md', claude: 'CLAUDE.md', gemini: 'GEMINI.md', grok: 'AGENTS.md', deepseek: 'AGENTS.md' };
export const MEMORY_TEXT = `# Working here

You are a coding teammate of LRMagent (Liando Reyfort Management). Tasks come
from the owner through LRMagent, often from WhatsApp, so:

- Work in this folder. Finish the task fully, then reply with a short summary in
  plain language: what you did, where the files are, anything left to decide.
- Keep a running "## Notes" section at the end of this file with what you learn
  about this project, so the next task can pick up where this one stopped.
- Never print secrets, tokens or keys in your reply.
`;

export function prepareFolder(teammate, folder, uid, gid) {
  const dir = workdir(teammate, folder);
  fs.mkdirSync(dir, { recursive: true });
  const memory = path.join(dir, MEMORY_FILE[teammate.id]);
  if (!fs.existsSync(memory)) fs.writeFileSync(memory, MEMORY_TEXT);
  if (uid != null) {
    for (const p of [path.join(WORK, teammate.id), dir, memory]) { try { fs.chownSync(p, uid, gid); } catch { /* not root (tests) */ } }
  }
  return dir;
}

function run(args, env, opts = {}) {
  return new Promise((resolve) => {
    execFile(args[0], args.slice(1), { env, timeout: opts.timeout || 20000, uid: opts.uid, gid: opts.gid, cwd: opts.cwd || HOME, maxBuffer: 4 << 20 },
      (error, stdout, stderr) => resolve({ code: error ? (error.code ?? 1) : 0, out: `${stdout}${stderr}` }));
  });
}

// Signed in, and with what. Checked by asking each CLI, never by reading its tokens.
export async function status(teammate, ids = {}) {
  const env = childEnv(teammate);
  const opts = { uid: ids.uid, gid: ids.gid };
  const version = (await run([teammate.cli, '--version'], env, opts)).out.split('\n').map((l) => l.trim())
    .filter((l) => /\d+\.\d+/.test(l) && !/warning|error/i.test(l)).pop()?.slice(0, 60) || '';
  if (!version || /not found|ENOENT/i.test(version)) return { installed: false, signedIn: false, version: '' };
  let signedIn = false; let detail = '';
  if (teammate.id === 'codex') {
    const r = await run(['codex', 'login', 'status'], env, opts);
    signedIn = r.code === 0 && /logged in/i.test(r.out) && !/not logged in/i.test(r.out);
    detail = /chatgpt/i.test(r.out) ? 'ChatGPT' : /api key/i.test(r.out) ? 'API key' : '';
  } else if (teammate.id === 'claude') {
    const r = await run(['claude', 'auth', 'status'], env, opts);
    try { const j = JSON.parse(r.out.slice(r.out.indexOf('{'))); signedIn = j.loggedIn === true; detail = j.subscriptionType || j.authMethod || ''; } catch { signedIn = false; }
  } else if (teammate.id === 'gemini') {
    signedIn = fs.existsSync(path.join(HOME, '.gemini', 'oauth_creds.json'));
    detail = signedIn ? 'Google account' : '';
  } else {
    const r = await run(['opencode', 'auth', 'list'], env, opts);
    const listed = new RegExp(`\\b${teammate.provider}\\b`, 'i').test(r.out.replace(/\x1b\[[0-9;]*m/g, ''));
    signedIn = listed || (teammate.id === 'deepseek' && Boolean(process.env.DEEPSEEK_API_KEY));
    detail = teammate.id === 'deepseek' && process.env.DEEPSEEK_API_KEY && !listed ? 'API key from secrets' : '';
  }
  return { installed: true, signedIn, version, detail };
}

export async function defaultModel(teammate, ids = {}) {
  if (!teammate.provider) return '';
  const r = await run(['opencode', 'models', teammate.provider], childEnv(teammate), { uid: ids.uid, gid: ids.gid, timeout: 30000 });
  return pickModel(teammate.provider, r.out.replace(/\x1b\[[0-9;]*m/g, '').split('\n'));
}

// Models the owner can pick for a teammate (Config → AI plans). Codex keeps the list
// its ChatGPT plan offers in ~/.codex/models_cache.json; OpenCode lists a provider's
// models; Claude Code takes its aliases. Blank in the picker means the CLI's default.
export function codexModels(raw) {
  let data;
  try { data = JSON.parse(raw); } catch { return []; }
  const list = Array.isArray(data) ? data : Array.isArray(data?.models) ? data.models : Array.isArray(data?.data) ? data.data : [];
  const out = [];
  for (const m of list) {
    const slug = typeof m === 'string' ? m : m?.slug || m?.id || m?.model;
    if (!slug || typeof slug !== 'string' || /^(hide|hidden)$/i.test(m?.visibility || '')) continue;
    if (!out.some((o) => o.id === slug)) out.push({ id: slug, name: (typeof m === 'object' && (m.display_name || m.name)) || slug });
  }
  return out.slice(0, 40);
}

export async function modelChoices(teammate, ids = {}) {
  if (teammate.id === 'codex') {
    try { return codexModels(fs.readFileSync(path.join(HOME, '.codex', 'models_cache.json'), 'utf8')); } catch { return []; }
  }
  if (teammate.id === 'claude') return [{ id: 'opus', name: 'Opus (newest)' }, { id: 'sonnet', name: 'Sonnet (newest)' }, { id: 'haiku', name: 'Haiku (newest)' }];
  if (teammate.provider) {
    const r = await run(['opencode', 'models', teammate.provider], childEnv(teammate), { uid: ids.uid, gid: ids.gid, timeout: 30000 });
    return r.out.replace(/\x1b\[[0-9;]*m/g, '').split('\n').map((l) => l.trim()).filter((l) => l.startsWith(`${teammate.provider}/`))
      .slice(0, 60).map((id) => ({ id, name: id }));
  }
  return [];
}
