/* LRMagent Vault: an Obsidian-style home for every agent's memory and knowledge.
 * Talks to /api/lrm/vault (deploy/agent-tools/lrm_vault_api.py). */
'use strict';

const SECTIONS = ['memory', 'knowledge'];
const ROOT_FILE = 'MEMORY.md';
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const mod = (e) => (isMac ? e.metaKey : e.ctrlKey);
const small = () => window.matchMedia('(max-width: 900px)').matches;
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

// Paths
const dirname = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
const basename = (p) => p.slice(p.lastIndexOf('/') + 1);
const stem = (p) => basename(p).replace(/\.md$/i, '');
const isNote = (p) => /\.md$/i.test(p);
const isImage = (p) => /\.(png|jpe?g|gif|webp|svg)$/i.test(p);
const isCanvas = (p) => /\.canvas$/i.test(p);
const isBase = (p) => /\.base$/i.test(p);
const kindOf = (p) => (isNote(p) ? 'note' : isCanvas(p) ? 'canvas' : isBase(p) ? 'base' : 'asset');
const isText = (tab) => ['note', 'canvas', 'base'].includes(tab?.kind);
const sectionOf = (p) => p.split('/')[0];
function normPath(p) {
  const out = [];
  for (const part of p.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') { if (!out.length) return null; out.pop(); } else out.push(part);
  }
  return out.join('/');
}
const joinPath = (dir, rel) => normPath(dir ? `${dir}/${rel}` : rel);
const fileUrl = (path) => `/api/lrm/vault?op=file&agent_id=${encodeURIComponent(S.agent || '')}&path=${encodeURIComponent(path)}`;
const today = () => new Date().toLocaleDateString('en-CA');
// Dates on screen are DD-MM-YYYY (the owner's format); file names keep YYYY-MM-DD so they sort.
const dmy = (d) => new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' }).replace(/\//g, '-');
const dmyTime = (d) => `${dmy(d)} ${new Date(d).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`;
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return dmy(t * 1000);
}

// State
const S = {
  agent: '', agents: [], entries: [], notes: new Map(), lc: new Map(), byStem: new Map(), byName: new Map(),
  aliases: new Map(), dirs: new Set(), out: new Map(), back: new Map(), unresolved: new Map(), tags: new Map(),
  panes: [{ tabs: [], active: -1, ed: null, sim: null }], focus: 0, bookmarks: [], expanded: new Set(SECTIONS), folder: 'knowledge', sharedKnowledge: false, truncated: false,
  leftTab: 'files', rightTab: 'links', graph: { local: false, depth: 1, orphans: true, unresolved: false, tags: false, filter: '' },
  sim: null, loaded: false,
};
// The focused pane's tabs: everything that works on "the tabs" works on the pane in focus.
Object.defineProperty(S, 'tabs', { get: () => S.panes[S.focus].tabs, set: (v) => { S.panes[S.focus].tabs = v; } });
Object.defineProperty(S, 'active', { get: () => S.panes[S.focus].active, set: (v) => { S.panes[S.focus].active = v; } });
const ui = store.get('lrm_vault:ui', {});
const paneView = (i = S.focus) => document.querySelector(`.pane-col[data-pane="${i}"] .view`);
const $v = (sel) => paneView()?.querySelector(sel) || null;
const curEd = () => S.panes[S.focus].ed;
// Render pane i (its active tab) as if it had focus.
function withPane(i, fn) { const f = S.focus; S.focus = i; try { return fn(); } finally { S.focus = f; } }
function renderAll() { buildPanes(); S.panes.forEach((_, i) => withPane(i, renderView)); }
function buildAndDraw() { if (buildPanes()) S.panes.forEach((_, i) => withPane(i, renderView)); }
const activeTab = () => S.tabs[S.active] || null;
const allTabs = () => S.panes.flatMap((p) => p.tabs);

// API
async function api(op, params = {}, body = null) {
  const url = new URL('/api/lrm/vault', location.origin);
  url.searchParams.set('op', op);
  url.searchParams.set('agent_id', S.agent || '');
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const res = await fetch(url, body
    ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, op, agent_id: S.agent || '' }) }
    : { credentials: 'same-origin' });
  if (res.status === 401) { (window.top || window).location.href = '/'; throw new Error('Signed out'); }
  let data;
  try { data = await res.json(); } catch { throw new Error(`Server answered ${res.status}`); }
  if (data.status !== 'success') { const err = new Error(data.message || 'Request failed'); err.code = data.code; throw err; }
  return data;
}
const post = (op, body) => api(op, {}, body);

// Theme: follow the console
function syncTheme() {
  const dark = false; // LRMagent is light only
  document.documentElement.classList.toggle('dark', dark);
  $('#hl-light').disabled = dark;
  $('#hl-dark').disabled = !dark;
}
syncTheme();
try {
  if (window.parent !== window) {
    new MutationObserver(() => { syncTheme(); if (activeTab()?.kind === 'graph') renderView(); })
      .observe(window.parent.document.documentElement, { attributes: true, attributeFilter: ['class'] });
  }
} catch { /* not same-origin */ }

// Toasts, menus, modals
function toast(message, error = false) {
  const el = document.createElement('div');
  el.className = `toast${error ? ' error' : ''}`;
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), error ? 5000 : 2500);
}
const fail = (e) => toast(e.message || String(e), true);

function closeMenus() { document.querySelectorAll('.menu').forEach((m) => m.remove()); }
function showMenu(x, y, items) {
  closeMenus();
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.setAttribute('role', 'menu');
  for (const item of items) {
    if (item === '-') { menu.appendChild(document.createElement('hr')); continue; }
    const b = document.createElement('button');
    b.innerHTML = `<i class="${item.icon}"></i>${esc(item.label)}`;
    if (item.danger) b.className = 'danger';
    b.onclick = () => { closeMenus(); item.fn(); };
    menu.appendChild(b);
  }
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(x, innerWidth - r.width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(y, innerHeight - r.height - 4))}px`;
  setTimeout(() => menu.querySelector('button')?.focus(), 0);
}
document.addEventListener('mousedown', (e) => { if (!e.target.closest('.menu')) closeMenus(); });

function modal(html, onKey) {
  const overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  document.body.appendChild(overlay);
  const close = () => { overlay.remove(); document.removeEventListener('keydown', key, true); };
  const key = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); overlay.dispatchEvent(new Event('cancel')); } else if (onKey) onKey(e); };
  document.addEventListener('keydown', key, true);
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) { close(); overlay.dispatchEvent(new Event('cancel')); } });
  return { overlay, close };
}

function ask({ title, text = '', value = null, ok = 'OK', danger = false, select = null }) {
  return new Promise((resolve) => {
    const { overlay, close } = modal(`<div class="msg"><h3>${esc(title)}</h3>${text ? `<div class="muted">${text}</div>` : ''}
      ${value != null ? `<input value="${esc(value)}" spellcheck="false">` : ''}</div>
      <div class="btns"><button class="btn" data-a="cancel">Cancel</button><button class="btn ${danger ? 'danger' : 'primary'}" data-a="ok">${esc(ok)}</button></div>`);
    const input = $('input', overlay);
    const done = (v) => { close(); resolve(v); };
    overlay.addEventListener('cancel', () => resolve(null));
    $('[data-a=cancel]', overlay).onclick = () => done(null);
    $('[data-a=ok]', overlay).onclick = () => done(input ? input.value.trim() : true);
    (input || $('[data-a=ok]', overlay)).focus();
    if (input) {
      if (select) input.setSelectionRange(...select(input.value)); else input.select();
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); done(input.value.trim()); } });
    }
  });
}

// Index: links, backlinks, tags
function resolveLink(raw, from = '', markdown = false) {
  let t = String(raw || '').trim().replace(/\\/g, '/');
  try { t = decodeURIComponent(t); } catch { /* keep as written */ }
  if (!t) return null;
  const lookup = (p) => { if (!p) return null; const lc = p.toLowerCase(); return S.lc.get(lc) || S.lc.get(`${lc}.md`) || null; };
  if (markdown || t.startsWith('./') || t.startsWith('../')) {
    const hit = lookup(joinPath(dirname(from), t));
    if (hit) return hit;
  }
  t = normPath(t.replace(/^\/+/, '')) || '';
  if (!t) return null;
  const direct = lookup(t);
  if (direct) return direct;
  const sec = sectionOf(from);
  for (const s of [sec, ...SECTIONS]) {
    if (SECTIONS.includes(s)) { const hit = lookup(`${s}/${t}`); if (hit) return hit; }
  }
  const lc = t.toLowerCase();
  let candidates;
  if (lc.includes('/')) {
    const want = lc.replace(/\.md$/, '');
    candidates = [...S.lc.values()].filter((p) => p.toLowerCase().replace(/\.md$/, '').endsWith(`/${want}`));
  } else {
    candidates = S.byName.get(lc) || S.byStem.get(lc.replace(/\.md$/, '')) || S.aliases.get(lc) || [];
  }
  if (!candidates.length) return null;
  return [...candidates].sort((a, b) => (sectionOf(b) === sec) - (sectionOf(a) === sec) || a.length - b.length)[0];
}

function linkName(path) {
  // Shortest [[link]] that still finds this note.
  const s = stem(path);
  return (S.byStem.get(s.toLowerCase()) || []).length > 1 ? path.replace(/\.md$/i, '') : s;
}

function buildIndex(entries) {
  S.entries = entries;
  S.notes = new Map(); S.lc = new Map(); S.byStem = new Map(); S.byName = new Map(); S.aliases = new Map();
  S.dirs = new Set(); S.out = new Map(); S.back = new Map(); S.unresolved = new Map(); S.tags = new Map();
  const push = (map, key, value) => { if (!map.has(key)) map.set(key, []); map.get(key).push(value); };
  for (const e of entries) {
    if (e.type === 'dir') { S.dirs.add(e.path); continue; }
    S.lc.set(e.path.toLowerCase(), e.path);
    push(S.byName, basename(e.path).toLowerCase(), e.path);
    if (isNote(e.path)) {
      S.notes.set(e.path, e);
      push(S.byStem, stem(e.path).toLowerCase(), e.path);
      for (const a of e.aliases || []) push(S.aliases, a.toLowerCase(), e.path);
      for (const tag of e.tags || []) push(S.tags, tag, e.path);
    }
  }
  const linkers = [...S.notes, ...S.entries.filter((e) => e.type === 'file' && isCanvas(e.path)).map((e) => [e.path, e])];
  for (const [path, e] of linkers) {
    const out = new Set(); const missing = new Set();
    for (const l of e.links || []) {
      const target = resolveLink(l.t, path, !l.w);
      if (target) { if (target !== path && isNote(target)) out.add(target); } else if (!(l.e && /\.\w{2,4}$/.test(l.t) && !isNote(l.t))) missing.add(l.t);
    }
    S.out.set(path, out);
    S.unresolved.set(path, missing);
    for (const t of out) { if (!S.back.has(t)) S.back.set(t, new Set()); S.back.get(t).add(path); }
  }
}

async function loadIndex({ quiet = false } = {}) {
  try {
    const data = await api('index');
    S.sharedKnowledge = data.shared_knowledge;
    S.truncated = data.truncated;
    buildIndex(data.entries);
    S.loaded = true;
    renderTree(); renderTags(); renderBookmarks(); renderRight(); renderStatus();
    if (activeTab()?.kind === 'graph' && !quiet) renderView();
    if (!activeTab() && !quiet) renderView();
    await refreshOpenNotes();
  } catch (e) { if (!quiet) fail(e); }
}
let indexTimer = null;
const loadIndexSoon = () => { clearTimeout(indexTimer); indexTimer = setTimeout(() => loadIndex({ quiet: true }), 1200); };

// Notes the agent changed while they were open (and the user hasn't touched) are reloaded.
async function refreshOpenNotes() {
  for (const tab of allTabs()) {
    if (!isText(tab) || tab.loading || tab.conflict || tab.content !== tab.saved) continue;
    const entry = S.notes.get(tab.path) || S.entries.find((e) => e.path === tab.path);
    if (!entry || (!tab.stale && entry.mtime <= tab.mtime + 0.001)) continue;
    tab.stale = false;
    try {
      const data = await api('read', { path: tab.path });
      if (tab.content !== tab.saved) continue;
      Object.assign(tab, { content: data.content, saved: data.content, mtime: data.mtime, editable: data.editable });
      S.panes.forEach((pane, i) => {
        if (pane.tabs[pane.active] !== tab) return;
        if (pane.ed && tab.kind === 'note' && tab.mode === 'edit') pane.ed.setDoc(data.content);
        else if (!(tab.kind === 'canvas' && tab.cvBusy)) withPane(i, renderView);
      });
      if (tab === activeTab()) { renderRight(); renderStatus(); }
    } catch { /* deleted or unreadable: the index refresh shows it */ }
  }
}

// Markdown rendering
const CALLOUT_ICONS = { note: 'fa-pencil', abstract: 'fa-clipboard-list', summary: 'fa-clipboard-list', info: 'fa-circle-info', todo: 'fa-circle-check', tip: 'fa-fire', success: 'fa-check', check: 'fa-check', done: 'fa-check', question: 'fa-circle-question', faq: 'fa-circle-question', help: 'fa-circle-question', warning: 'fa-triangle-exclamation', caution: 'fa-triangle-exclamation', attention: 'fa-triangle-exclamation', failure: 'fa-xmark', danger: 'fa-bolt', error: 'fa-bolt', bug: 'fa-bug', example: 'fa-list', quote: 'fa-quote-left', cite: 'fa-quote-left' };

function slug(text) { return String(text).toLowerCase().trim().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s+/g, '-'); }

function splitFrontmatter(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  if (!m) return { props: null, body: text, offset: 0 };
  const props = []; let current = null;
  for (const line of m[1].split(/\r?\n/)) {
    const item = /^\s+-\s*(.*)$/.exec(line);
    if (item && current) { current.list.push(item[1].replace(/^["']|["']$/g, '')); continue; }
    const kv = /^([\w -]+?)\s*:\s*(.*)$/.exec(line);
    if (!kv) continue;
    let value = kv[2].trim();
    const list = /^\[(.*)\]$/.exec(value) ? value.slice(1, -1).split(',').map((v) => v.trim().replace(/^["']|["']$/g, '')).filter(Boolean) : null;
    current = { key: kv[1], value: list ? '' : value.replace(/^["']|["']$/g, ''), list: list || [] };
    props.push(current);
  }
  return { props, body: text.slice(m[0].length), offset: m[0].split('\n').length - 1 };
}

const md = window.markdownit({
  html: false, linkify: true, breaks: true,
  highlight(code, lang) {
    if (lang && window.hljs?.getLanguage(lang)) { try { return window.hljs.highlight(code, { language: lang, ignoreIllegals: true }).value; } catch { /* plain */ } }
    return '';
  },
});

// [[note]], [[note#heading|alias]], ![[embed]]
md.inline.ruler.before('link', 'wikilink', (state, silent) => {
  const src = state.src; let pos = state.pos; let embed = false;
  if (src.charCodeAt(pos) === 0x21) { if (src.slice(pos + 1, pos + 3) !== '[[') return false; embed = true; pos += 1; } else if (src.slice(pos, pos + 2) !== '[[') return false;
  const end = src.indexOf(']]', pos + 2);
  if (end < 0) return false;
  const inner = src.slice(pos + 2, end);
  if (!inner.trim() || inner.includes('\n') || inner.includes('[[')) return false;
  if (!silent) {
    const [target, alias] = inner.split(/\|(.*)/s);
    const [name, heading] = target.split(/#(.*)/s);
    const token = state.push('wikilink', '', 0);
    token.meta = { embed, name: name.trim(), heading: (heading || '').replace(/^\^/, '').trim(), alias: (alias || '').trim() };
  }
  state.pos = end + 2;
  return true;
});
md.renderer.rules.wikilink = (tokens, idx, opts, env) => {
  const m = tokens[idx].meta;
  const target = m.name ? resolveLink(m.name, env.path) : env.path;
  if (m.embed && target) {
    if (isImage(target)) {
      const width = /^\d+(x\d+)?$/.test(m.alias) ? ` width="${esc(m.alias.split('x')[0])}"` : '';
      return `<img src="${esc(fileUrl(target))}" alt="${esc(m.alias && !width ? m.alias : basename(target))}"${width} loading="lazy">`;
    }
    if (/\.pdf$/i.test(target)) return `<iframe class="pdf-embed" src="${esc(fileUrl(target))}" title="${esc(basename(target))}"></iframe>`;
    if (isNote(target)) return `<span class="embed" data-embed="${esc(target)}" data-heading="${esc(m.heading)}"></span>`;
  }
  const label = m.alias || [m.name, m.heading].filter(Boolean).join(' › ');
  return `<a href="#" class="internal${target ? '' : ' unresolved'}" data-href="${esc(target || m.name)}"${target ? '' : ' data-create="1"'} data-heading="${esc(m.heading)}">${esc(label)}</a>`;
};

// $math$ and $$math$$ (KaTeX), ```mermaid diagrams: drawn after rendering by fillRich().
md.inline.ruler.after('escape', 'math_inline', (state, silent) => {
  const src = state.src; const start = state.pos;
  if (src[start] !== '$' || src[start + 1] === '$' || /\s/.test(src[start + 1] || ' ')) return false;
  if (start > 0 && src[start - 1] === '\\') return false;
  let end = start + 1;
  while ((end = src.indexOf('$', end)) !== -1 && src[end - 1] === '\\') end++;
  if (end === -1 || /\s/.test(src[end - 1]) || /\d/.test(src[end + 1] || '') || src.slice(start, end).includes('\n')) return false;
  if (!silent) { const t = state.push('math_inline', '', 0); t.content = src.slice(start + 1, end); }
  state.pos = end + 1;
  return true;
});
md.block.ruler.before('fence', 'math_block', (state, startLine, endLine, silent) => {
  const first = state.src.slice(state.bMarks[startLine] + state.tShift[startLine], state.eMarks[startLine]);
  if (!first.startsWith('$$')) return false;
  const one = /^\$\$(.+)\$\$\s*$/.exec(first);
  let line = startLine; let body = '';
  if (one) body = one[1];
  else {
    const parts = [first.slice(2)];
    for (line = startLine + 1; line < endLine; line++) {
      const text = state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
      if (text.trimEnd().endsWith('$$')) { parts.push(text.trimEnd().slice(0, -2)); break; }
      parts.push(text);
    }
    if (line >= endLine) return false;
    body = parts.join('\n');
  }
  if (silent) return true;
  const t = state.push('math_block', 'div', 0);
  t.content = body.trim(); t.map = [startLine, line + 1];
  state.line = line + 1;
  return true;
}, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
md.renderer.rules.math_inline = (tokens, idx) => `<span data-rich="math" data-src="${esc(tokens[idx].content)}">${esc(tokens[idx].content)}</span>`;
md.renderer.rules.math_block = (tokens, idx) => `<div class="math-block" data-rich="mathblock" data-src="${esc(tokens[idx].content)}"></div>\n`;
const renderFence = md.renderer.rules.fence;
md.renderer.rules.fence = (tokens, idx, opts, env, self) => {
  const info = (tokens[idx].info || '').trim().toLowerCase();
  if (info === 'mermaid') return `<div class="mermaid-block" data-rich="mermaid" data-src="${esc(tokens[idx].content)}"></div>\n`;
  if (info === 'math' || info === 'latex') return `<div class="math-block" data-rich="mathblock" data-src="${esc(tokens[idx].content)}"></div>\n`;
  return renderFence(tokens, idx, opts, env, self);
};

// #tags
md.inline.ruler.before('link', 'tag', (state, silent) => {
  if (state.src.charCodeAt(state.pos) !== 0x23) return false;
  if (state.pos > 0 && !/[\s(]/.test(state.src[state.pos - 1])) return false;
  const m = /^#([\p{L}_][\p{L}\p{N}_/-]*)/u.exec(state.src.slice(state.pos));
  if (!m) return false;
  if (!silent) { const t = state.push('tag', '', 0); t.content = m[1]; }
  state.pos += m[0].length;
  return true;
});
md.renderer.rules.tag = (tokens, idx) => `<a href="#" class="tag" data-tag="${esc(tokens[idx].content)}">#${esc(tokens[idx].content)}</a>`;

// ==highlight==
md.inline.ruler.before('emphasis', 'mark', (state, silent) => {
  const src = state.src; const start = state.pos;
  if (src.slice(start, start + 2) !== '==' || src[start + 2] === ' ' || src[start + 2] === '=') return false;
  const end = src.indexOf('==', start + 2);
  if (end < 0 || src.slice(start, end).includes('\n')) return false;
  if (!silent) {
    state.push('mark_open', 'mark', 1);
    const max = state.posMax;
    state.pos = start + 2; state.posMax = end;
    state.md.inline.tokenize(state);
    state.posMax = max;
    state.push('mark_close', 'mark', -1);
  }
  state.pos = end + 2;
  return true;
});

// > [!note] Callouts, with +/- folding
md.core.ruler.after('block', 'callout', (state) => {
  const t = state.tokens;
  for (let i = 0; i < t.length; i++) {
    if (t[i].type !== 'blockquote_open' || t[i + 1]?.type !== 'paragraph_open' || t[i + 2]?.type !== 'inline') continue;
    const m = /^\[!([\w-]+)\]([+-]?)[ \t]*([^\n]*)\n?/.exec(t[i + 2].content);
    if (!m) continue;
    const type = m[1].toLowerCase();
    t[i].attrJoin('class', `callout${m[2] ? ' foldable' : ''}${m[2] === '-' ? ' folded' : ''}`);
    t[i].attrSet('data-callout', type);
    const title = new state.Token('html_block', '', 0);
    title.content = `<div class="callout-title"><i class="fa-solid ${CALLOUT_ICONS[type] || 'fa-pencil'}"></i>${esc(m[3] || type[0].toUpperCase() + type.slice(1))}${m[2] ? ' <i class="fa-solid fa-chevron-down" style="font-size:10px;opacity:.6"></i>' : ''}</div>\n`;
    const rest = t[i + 2].content.slice(m[0].length);
    if (rest.trim()) { t[i + 2].content = rest; t.splice(i + 1, 0, title); } else t.splice(i + 1, 3, title);
  }
});

// - [ ] tasks (clickable) and heading ids (outline, [[note#heading]])
md.core.ruler.after('inline', 'tasks_and_ids', (state) => {
  const t = state.tokens; let n = 0; const seen = new Map();
  for (let i = 0; i < t.length; i++) {
    if (t[i].type === 'heading_open' && t[i + 1]?.type === 'inline') {
      let id = slug(t[i + 1].content) || 'section';
      const k = seen.get(id) || 0; seen.set(id, k + 1);
      if (k) id += `-${k}`;
      t[i].attrSet('id', `h-${id}`);
      t[i].attrSet('data-heading', t[i + 1].content);
    }
    if (i < 2 || t[i].type !== 'inline' || t[i - 1].type !== 'paragraph_open' || t[i - 2].type !== 'list_item_open') continue;
    const first = t[i].children[0];
    const m = first && first.type === 'text' ? /^\[([ xX])\](?:[ \t]|$)/.exec(first.content) : null;
    if (!m) continue;
    first.content = first.content.slice(m[0].length);
    const box = new state.Token('html_inline', '', 0);
    const done = m[1] !== ' ';
    box.content = `<input type="checkbox" data-task="${n++}"${done ? ' checked' : ''}${state.env.readonly ? ' disabled' : ''}>`;
    t[i].children.unshift(box);
    t[i - 2].attrJoin('class', `task${done ? ' done' : ''}`);
  }
});

const renderLinkOpen = md.renderer.rules.link_open || ((tokens, idx, opts, env, self) => self.renderToken(tokens, idx, opts));
md.renderer.rules.link_open = (tokens, idx, opts, env, self) => {
  const tok = tokens[idx];
  const href = tok.attrGet('href') || '';
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) {
    tok.attrSet('target', '_blank'); tok.attrSet('rel', 'noopener noreferrer'); tok.attrJoin('class', 'external');
  } else {
    const [path, anchor = ''] = href.split('#');
    let heading = anchor;
    try { heading = decodeURIComponent(anchor); } catch { /* as written */ }
    const target = path ? resolveLink(path, env.path, true) : env.path;
    let guess = path;
    try { guess = joinPath(dirname(env.path), decodeURIComponent(path)) || path; } catch { /* as written */ }
    tok.attrSet('href', '#');
    tok.attrJoin('class', `internal${target ? '' : ' unresolved'}`);
    tok.attrSet('data-href', target || guess);
    if (!target) tok.attrSet('data-create', '1');
    if (heading) tok.attrSet('data-heading', heading);
  }
  return renderLinkOpen(tokens, idx, opts, env, self);
};
const renderImage = md.renderer.rules.image;
md.renderer.rules.image = (tokens, idx, opts, env, self) => {
  const tok = tokens[idx];
  const src = tok.attrGet('src') || '';
  if (!/^[a-z][a-z0-9+.-]*:/i.test(src) && !src.startsWith('//')) {
    const target = resolveLink(src, env.path, true);
    if (target) tok.attrSet('src', fileUrl(target));
  }
  tok.attrSet('loading', 'lazy');
  return renderImage(tokens, idx, opts, env, self);
};

function renderMarkdown(text, path, { depth = 0, readonly = false, heading = '' } = {}) {
  const { props, body } = splitFrontmatter(text);
  const html = md.render(heading ? sectionOf_(body, heading) : body, { path, depth, readonly });
  return { props, html };
}
// The part of a note under one heading, for ![[note#heading]].
function sectionOf_(body, heading) {
  const lines = body.split('\n');
  const want = slug(heading);
  const at = lines.findIndex((l) => /^#{1,6}\s/.test(l) && slug(l.replace(/^#+\s*/, '')) === want);
  if (at < 0) return body;
  const level = /^#+/.exec(lines[at])[0].length;
  let end = lines.findIndex((l, i) => i > at && /^#{1,6}\s/.test(l) && /^#+/.exec(l)[0].length <= level);
  if (end < 0) end = lines.length;
  return lines.slice(at, end).join('\n');
}

function propsHtml(props) {
  if (!props || !props.length) return '';
  const rows = props.map((p) => {
    const tags = /^tags?$/i.test(p.key);
    const values = p.list.length ? p.list : (tags && p.value ? p.value.split(/[,\s]+/).filter(Boolean) : []);
    const icon = tags ? 'fa-hashtag' : /alias/i.test(p.key) ? 'fa-share' : /date|created|updated/i.test(p.key) ? 'fa-regular fa-calendar' : 'fa-solid fa-align-left';
    const v = values.length
      ? values.map((x) => (tags ? `<button class="tag-chip" data-tag="${esc(x.replace(/^#/, ''))}">#${esc(x.replace(/^#/, ''))}</button>` : `<span class="tag-chip" style="cursor:default">${esc(x)}</span>`)).join('')
      : esc(p.value);
    return `<div class="prow"><div class="pkey"><i class="${icon.includes(' ') ? icon : `fa-solid ${icon}`}"></i>${esc(p.key)}</div><div>${v || '<span class="muted">—</span>'}</div></div>`;
  }).join('');
  return `<div class="props"><div class="props-head">Properties</div>${rows}</div>`;
}

const embedCache = new Map();
async function fillEmbeds(root, depth) {
  for (const el of root.querySelectorAll('.embed[data-embed]:not([data-done])')) {
    el.dataset.done = '1';
    const path = el.dataset.embed;
    const title = `<div class="embed-title" data-open="${esc(path)}"><i class="fa-regular fa-file-lines"></i> ${esc(stem(path))}${el.dataset.heading ? ` › ${esc(el.dataset.heading)}` : ''}</div>`;
    if (depth >= 2) { el.innerHTML = title; continue; }
    try {
      const entry = S.notes.get(path);
      const key = `${path}@${entry?.mtime}`;
      let text = embedCache.get(key);
      if (text == null) { text = (await api('read', { path })).content; embedCache.set(key, text); }
      const { html } = renderMarkdown(text, path, { depth: depth + 1, readonly: true, heading: el.dataset.heading });
      el.innerHTML = `${title}<div class="md">${html}</div>`;
      highlightCode(el);
      fillRich(el);
      await fillEmbeds(el, depth + 1);
    } catch (e) { el.innerHTML = `${title}<div class="muted">${esc(e.message)}</div>`; }
  }
}
function highlightCode(root) { root.querySelectorAll('pre code').forEach((c) => c.classList.add('hljs')); }

function toggleTask(text, n) {
  const lines = text.split('\n');
  let k = 0; let fence = false;
  const start = splitFrontmatter(text).offset;
  for (let i = start; i < lines.length; i++) {
    if (/^\s*(```|~~~)/.test(lines[i])) fence = !fence;
    if (fence) continue;
    const m = /^(\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\[)([ xX])\]/.exec(lines[i]);
    if (!m) continue;
    if (k++ === n) { lines[i] = m[1] + (m[2] === ' ' ? 'x' : ' ') + lines[i].slice(m[1].length + 1); return lines.join('\n'); }
  }
  return null;
}

// File tree
function treeChildren() {
  const kids = new Map();
  const add = (parent, entry) => { if (!kids.has(parent)) kids.set(parent, []); kids.get(parent).push(entry); };
  for (const e of S.entries) {
    if (e.path === ROOT_FILE || SECTIONS.includes(e.path)) continue;
    add(dirname(e.path), e);
  }
  for (const list of kids.values()) {
    list.sort((a, b) => (a.type === 'dir' ? 0 : 1) - (b.type === 'dir' ? 0 : 1) || a.path.localeCompare(b.path, undefined, { numeric: true, sensitivity: 'base' }));
  }
  return kids;
}

function renderTree() {
  const kids = treeChildren();
  const current = activeTab()?.path;
  const row = (e, depth) => {
    const dir = e.type === 'dir';
    const open = S.expanded.has(e.path);
    const board = isCanvas(e.path) || isBase(e.path);
    const name = dir ? basename(e.path) : (isNote(e.path) || board ? e.path.split('/').pop().replace(/\.(md|canvas|base)$/i, '') : basename(e.path));
    const ext = !dir && !isNote(e.path) ? `<span class="ext">${esc(e.path.split('.').pop().toUpperCase())}</span>` : '';
    const icon = dir ? (open ? 'fa-regular fa-folder-open' : 'fa-regular fa-folder') : isNote(e.path) ? 'fa-regular fa-file-lines'
      : isCanvas(e.path) ? 'fa-regular fa-object-group' : isBase(e.path) ? 'fa-solid fa-table' : isImage(e.path) ? 'fa-regular fa-image' : 'fa-regular fa-file';
    const cls = ['tree-row', dir && S.folder === e.path ? 'selected-folder' : '', !dir && e.path === current ? 'active' : ''].join(' ');
    let html = `<div class="${cls}" role="treeitem" tabindex="-1" data-path="${esc(e.path)}" data-type="${e.type}" draggable="true" style="padding-left:${6 + depth * 14}px"${dir ? ` aria-expanded="${open}"` : ''}>
      ${dir ? `<i class="fa-solid fa-chevron-right chev${open ? ' open' : ''}"></i>` : '<span class="chev"></span>'}<i class="ico ${icon}"></i><span class="name">${esc(name)}</span>${ext}
      <button class="icon-btn more" data-more title="More" tabindex="-1"><i class="fa-solid fa-ellipsis"></i></button></div>`;
    if (dir && open) for (const c of kids.get(e.path) || []) html += row(c, depth + 1);
    return html;
  };
  let html = '';
  // The vault is yours: your notes come first, shared by every agent; each agent's own memory is one folder below.
  const agentName = S.agents?.find((a) => a.id === S.agent)?.name || 'Agent';
  const memoryRow = () => (S.notes.has(ROOT_FILE) ? `<div class="tree-row${current === ROOT_FILE ? ' active' : ''}" data-path="${ROOT_FILE}" data-type="file" title="Long-term memory ${esc(agentName)} reads in every conversation" style="padding-left:20px">
      <span class="chev"></span><i class="ico fa-solid fa-brain" style="color:var(--brand)"></i><span class="name">MEMORY</span></div>` : '');
  const labels = { memory: [`${agentName}'s memory`, 'fa-solid fa-robot', `What ${agentName} remembers: daily notes, chat summaries, long-term memory`],
    knowledge: ['Vault', 'fa-solid fa-gem', 'Your notes: the whole vault, synced with Git and readable by every agent'] };
  for (const s of ['knowledge', 'memory']) {
    const open = S.expanded.has(s);
    const [label, icon, title] = labels[s];
    const badge = s === 'knowledge' ? `<span class="badge" title="${S.sharedKnowledge || S.agents?.length < 2 ? 'Every agent reads these notes' : 'This agent has its own notes'}">${S.sharedKnowledge || S.agents?.length < 2 ? 'ALL AGENTS' : 'OWN'}</span>` : '';
    html += `<div class="tree-row section${S.folder === s ? ' selected-folder' : ''}" data-path="${s}" data-type="dir" title="${esc(title)}" aria-expanded="${open}">
      <i class="fa-solid fa-chevron-right chev${open ? ' open' : ''}"></i><i class="ico ${icon}"></i><span class="name">${label}</span>${badge}
      <button class="icon-btn more" data-more title="More" tabindex="-1"><i class="fa-solid fa-ellipsis"></i></button></div>`;
    if (open) {
      const list = kids.get(s) || [];
      if (s === 'memory') html += memoryRow();
      for (const c of list) html += row(c, 1);
      if (!list.length) html += `<div class="hint" style="padding-left:34px">Empty</div>`;
    }
  }
  if (S.truncated) html += '<div class="hint">Showing the first 5,000 files only.</div>';
  $('#tree').innerHTML = html;
}

function treeMenu(path, type, x, y) {
  const dir = type === 'dir';
  const section = SECTIONS.includes(path);
  const items = [];
  if (dir) {
    items.push({ icon: 'fa-regular fa-pen-to-square', label: 'New note', fn: () => newNote(path) });
    items.push({ icon: 'fa-regular fa-folder-open', label: 'New folder', fn: () => newFolder(path) });
    items.push({ icon: 'fa-solid fa-paperclip', label: 'Upload images or PDFs…', fn: () => uploadInto(path) });
    items.push({ icon: 'fa-regular fa-copy', label: 'New note from template…', fn: () => { S.folder = path; newFromTemplate(); } });
    items.push({ icon: 'fa-regular fa-object-group', label: 'New canvas', fn: () => newBoard('canvas', path) });
    items.push({ icon: 'fa-solid fa-table', label: 'New table view (base)', fn: () => newBoard('base', path) });
  } else {
    items.push({ icon: 'fa-regular fa-file-lines', label: 'Open in new tab', fn: () => openPath(path, { newTab: true }) });
    items.push({ icon: 'fa-solid fa-table-columns', label: 'Open to the right', fn: () => openSplit(path) });
    items.push({ icon: 'fa-regular fa-star', label: S.bookmarks.includes(path) ? 'Remove bookmark' : 'Bookmark', fn: () => toggleBookmark(path) });
    if (isNote(path)) items.push({ icon: 'fa-solid fa-link', label: 'Copy [[link]]', fn: () => copy(`[[${linkName(path)}]]`) });
    if (isNote(path)) items.push({ icon: 'fa-solid fa-diagram-project', label: 'Open local graph', fn: () => openGraph(path) });
  }
  if (!section && path !== ROOT_FILE && !S.notes.get(path)?.readonly) {
    items.push('-');
    items.push({ icon: 'fa-solid fa-i-cursor', label: 'Rename…', fn: () => renamePath(path, { nameOnly: true }) });
    items.push({ icon: 'fa-solid fa-folder-tree', label: 'Move to…', fn: () => renamePath(path) });
    items.push('-');
    items.push({ icon: 'fa-regular fa-trash-can', label: 'Delete', danger: true, fn: () => deletePath(path, dir) });
  }
  showMenu(x, y, items);
}

function uploadInto(folder) {
  const input = document.createElement('input');
  input.type = 'file'; input.multiple = true; input.accept = 'image/*,application/pdf';
  input.onchange = () => { if (input.files.length) attachFiles([...input.files], '', folder); };
  input.click();
}

function copy(text) {
  navigator.clipboard?.writeText(text).then(() => toast('Copied'), () => toast(text));
}

$('#tree').addEventListener('click', (e) => {
  const rowEl = e.target.closest('.tree-row');
  if (!rowEl) return;
  const { path, type } = rowEl.dataset;
  if (e.target.closest('[data-more]')) { const r = e.target.getBoundingClientRect(); treeMenu(path, type, r.left, r.bottom + 4); return; }
  if (type === 'dir') {
    S.folder = path;
    if (S.expanded.has(path)) S.expanded.delete(path); else S.expanded.add(path);
    saveState(); renderTree();
  } else {
    S.folder = path === ROOT_FILE ? S.folder : dirname(path);
    openPath(path, { newTab: mod(e) || e.button === 1 });
    if (small()) setPane('left', false);
  }
});
$('#tree').addEventListener('contextmenu', (e) => {
  const rowEl = e.target.closest('.tree-row');
  if (!rowEl) return;
  e.preventDefault();
  treeMenu(rowEl.dataset.path, rowEl.dataset.type, e.clientX, e.clientY);
});
// Drag a file or folder onto a folder to move it.
let dragPath = null;
$('#tree').addEventListener('dragstart', (e) => {
  const rowEl = e.target.closest('.tree-row');
  const path = rowEl?.dataset.path;
  if (!path || SECTIONS.includes(path) || path === ROOT_FILE || S.notes.get(path)?.readonly) { e.preventDefault(); return; }
  dragPath = path;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', isNote(path) ? `[[${linkName(path)}]]` : path);
});
$('#tree').addEventListener('dragover', (e) => {
  const rowEl = e.target.closest('.tree-row[data-type=dir]');
  document.querySelectorAll('.tree-row.drop').forEach((r) => r.classList.remove('drop'));
  const files = !dragPath && [...(e.dataTransfer?.types || [])].includes('Files');
  if (!rowEl || (!files && (!dragPath || rowEl.dataset.path === dirname(dragPath) || `${rowEl.dataset.path}/`.startsWith(`${dragPath}/`)))) return;
  e.preventDefault();
  rowEl.classList.add('drop');
});
$('#tree').addEventListener('dragend', () => { dragPath = null; document.querySelectorAll('.tree-row.drop').forEach((r) => r.classList.remove('drop')); });
$('#tree').addEventListener('drop', (e) => {
  const rowEl = e.target.closest('.tree-row[data-type=dir]');
  if (rowEl && !dragPath && e.dataTransfer?.files?.length) {
    e.preventDefault();
    attachFiles([...e.dataTransfer.files], '', rowEl.dataset.path);
    return;
  }
  if (!rowEl || !dragPath) return;
  e.preventDefault();
  const from = dragPath; dragPath = null;
  rowEl.classList.remove('drop');
  movePath(from, `${rowEl.dataset.path}/${basename(from)}`);
});

// File operations
function uniquePath(folder, name, ext = '.md') {
  let path = `${folder}/${name}${ext}`; let i = 1;
  while (S.lc.has(path.toLowerCase()) || S.dirs.has(path)) path = `${folder}/${name} ${i++}${ext}`;
  return path;
}

async function createNote(path, content = '', { mode = 'edit', newTab = false, focusTitle = false } = {}) {
  try {
    await post('create', { path, content });
    await loadIndex({ quiet: true });
    await openPath(path, { mode, newTab });
    if (focusTitle) { const t = $v('#inline-title'); if (t) { t.focus(); t.select(); } }
  } catch (e) { fail(e); }
}

function newNote(folder = S.folder) {
  if (!folder || folder === ROOT_FILE || !(SECTIONS.includes(folder) || S.dirs.has(folder))) folder = 'knowledge';
  S.expanded.add(folder);
  return createNote(uniquePath(folder, 'Untitled'), '', { focusTitle: true });
}

async function newFolder(parent = S.folder) {
  if (!parent || !(SECTIONS.includes(parent) || S.dirs.has(parent))) parent = 'knowledge';
  const name = await ask({ title: 'New folder', text: `Inside <b>${esc(parent)}</b>`, value: 'New folder', ok: 'Create' });
  if (!name) return;
  try {
    await post('create', { path: `${parent}/${name}`, kind: 'folder' });
    S.expanded.add(parent); S.folder = `${parent}/${name}`;
    await loadIndex({ quiet: true });
  } catch (e) { fail(e); }
}

function dailyNote(day = today()) {
  const path = `memory/${day}.md`;
  if (S.notes.has(path)) return openPath(path);
  S.expanded.add('memory');
  const tpl = S.notes.has(`${TEMPLATE_DIR}/Daily note.md`) ? `${TEMPLATE_DIR}/Daily note.md` : '';
  if (!tpl || day !== today()) return createNote(path, `# ${day}\n\n`, { mode: 'edit' });
  return readNote(tpl).then((text) => createNote(path, fillTemplate(text, day), { mode: 'edit' }));
}

async function createFromLink(name, from) {
  let path = name.replace(/\\/g, '/').replace(/^\/+/, '');
  if (!isNote(path)) path += '.md';
  if (!path.includes('/')) path = `${from && sectionOf(from) === 'knowledge' ? dirname(from) : 'knowledge'}/${path}`;
  else if (!SECTIONS.includes(sectionOf(path))) path = `knowledge/${path}`;
  path = normPath(path);
  if (!path) return;
  const ok = await ask({ title: 'Create note?', text: `<b>${esc(path)}</b> doesn't exist yet.`, ok: 'Create' });
  if (ok) createNote(path, sectionOf(path) === 'knowledge' ? `# ${stem(path)}\n\n` : '', { mode: 'edit' });
}

async function renamePath(path, { nameOnly = false } = {}) {
  const dir = S.dirs.has(path);
  const value = nameOnly ? (dir ? basename(path) : stem(path)) : path;
  const target = await ask({
    title: nameOnly ? 'Rename' : 'Move to',
    text: nameOnly ? '' : 'Full path inside the vault, starting with <b>memory/</b> or <b>knowledge/</b>. Missing folders are created.',
    value, ok: nameOnly ? 'Rename' : 'Move',
  });
  if (!target || target === value) return;
  let newPath = nameOnly ? (dirname(path) ? `${dirname(path)}/` : '') + target : target.replace(/^\/+|\/+$/g, '');
  if (!dir && isNote(path) && !isNote(newPath) && (nameOnly || !/\.[a-z0-9]{1,5}$/i.test(newPath))) newPath += '.md';
  return movePath(path, newPath);
}

async function movePath(path, newPath) {
  await flushAll();
  try {
    const res = await post('rename', { path, new_path: newPath, update_links: true });
    const oldStem = stem(path);
    for (const tab of allTabs()) {
      if (tab.kind === 'graph') continue;
      if (tab.path === path) tab.path = newPath;
      else if (tab.path.startsWith(`${path}/`)) tab.path = newPath + tab.path.slice(path.length);
    }
    if (S.expanded.has(path)) S.expanded.add(newPath);
    S.expanded.add(dirname(newPath));
    if (S.bookmarks.some((b) => b === path || b.startsWith(`${path}/`))) {
      S.bookmarks = S.bookmarks.map((b) => (b === path ? newPath : b.startsWith(`${path}/`) ? newPath + b.slice(path.length) : b));
      savePrefs();
    }
    await loadIndex({ quiet: true });
    // Notes that the link update rewrote are reloaded from disk.
    for (const tab of allTabs()) if (isText(tab)) tab.stale = true;
    await refreshOpenNotes();
    const tab = activeTab();
    if (tab?.path === newPath && isNote(newPath) && stem(newPath) !== oldStem && tab.content.startsWith(`# ${oldStem}\n`)) {
      // Keep a "# Untitled" heading in step with the new name.
      tab.content = `# ${stem(newPath)}\n${tab.content.slice(oldStem.length + 3)}`;
      await saveTab(tab);
    }
    renderAll();
    if (res.updated_links) toast(`Updated links in ${res.updated_links} note${res.updated_links > 1 ? 's' : ''}`);
  } catch (e) { fail(e); renderView(); }
}

async function deletePath(path, dir = S.dirs.has(path)) {
  const back = S.back.get(path)?.size || 0;
  const ok = await ask({
    title: `Delete ${dir ? 'folder' : 'note'}?`,
    text: `<b>${esc(path)}</b>${dir ? ' and everything in it' : ''} will be moved to the agent's <code>.trash</code> folder.${back ? ` ${back} note${back > 1 ? 's link' : ' links'} here.` : ''}`,
    ok: 'Delete', danger: true,
  });
  if (!ok) return;
  try {
    await post('delete', { path });
    for (let pi = S.panes.length - 1; pi >= 0; pi--) {
      withPane(pi, () => {
        for (let i = S.tabs.length - 1; i >= 0; i--) {
          const p = S.tabs[i].path;
          if (p && (p === path || p.startsWith(`${path}/`))) closeTab(i, { skipSave: true });
        }
      });
    }
    S.bookmarks = S.bookmarks.filter((b) => b !== path && !b.startsWith(`${path}/`)); savePrefs();
    await loadIndex({ quiet: true });
    toast('Moved to trash');
  } catch (e) { fail(e); }
}

// Tabs
function saveState() {
  store.set(`lrm_vault:${S.agent}`, {
    panes: S.panes.map((p) => ({ tabs: p.tabs.map((t) => ({ kind: t.kind, path: t.path, mode: t.mode, center: t.center || null })), active: p.active })),
    focus: S.focus, expanded: [...S.expanded], folder: S.folder, graph: S.graph,
  });
  store.set('lrm_vault:ui', { ...ui, agent: S.agent, leftTab: S.leftTab, rightTab: S.rightTab });
}

function newTabObj(kind, path, mode) {
  return { kind, path, mode: mode || 'read', content: '', saved: '', mtime: 0, editable: true, loading: isText({ kind }), error: '', conflict: false, history: [], fwd: [], scroll: 0 };
}

async function openPath(path, { newTab = false, mode = null, heading = '', line = 0, fromHistory = false } = {}) {
  if (!path) return;
  const kind = kindOf(path);
  const existing = S.tabs.findIndex((t) => t.path === path);
  if (existing >= 0 && !fromHistory) {
    activate(existing);
    if (mode && S.tabs[existing].mode !== mode) setMode(mode);
    if (heading) scrollToHeading(heading);
    return;
  }
  let tab = activeTab();
  if (newTab || !tab || tab.kind === 'graph') {
    tab = newTabObj(kind, path, mode);
    S.tabs.splice(S.active + 1, 0, tab);
    S.active += 1;
  } else {
    await flush(tab);
    if (!fromHistory && tab.path !== path) { tab.history.push(tab.path); tab.fwd = []; }
    Object.assign(tab, newTabObj(kind, path, mode || (tab.kind === 'note' ? tab.mode : 'read')), { history: tab.history, fwd: tab.fwd });
  }
  S.folder = path === ROOT_FILE ? S.folder : dirname(path);
  saveState(); renderTabs(); renderTree(); renderView();
  if (isText(tab)) await loadTab(tab);
  if (tab === activeTab()) {
    if (heading) scrollToHeading(heading);
    if (line && tab.mode === 'edit') caretToLine(line);
  }
}

async function loadTab(tab) {
  tab.loading = true; tab.error = '';
  try {
    const data = await api('read', { path: tab.path });
    Object.assign(tab, { content: data.content, saved: data.content, mtime: data.mtime, editable: data.editable, conflict: false });
  } catch (e) { tab.error = e.message; }
  tab.loading = false;
  S.panes.forEach((pane, i) => { if (pane.tabs[pane.active] === tab) withPane(i, renderView); });
  if (tab === activeTab()) { renderRight(); renderStatus(); }
}

function activate(i) {
  if (i === S.active) return;
  const cur = activeTab();
  if (cur) { cur.scroll = $v('.scroller')?.scrollTop || 0; flush(cur); }
  S.active = i;
  const tab = activeTab();
  if (tab?.path) S.folder = tab.path === ROOT_FILE ? S.folder : dirname(tab.path);
  saveState(); renderTabs(); renderTree(); renderView();
}

function closeTab(i, { skipSave = false } = {}) {
  const tab = S.tabs[i];
  if (!tab) return;
  if (!skipSave) flush(tab);
  if (tab.kind === 'graph') stopGraph();
  S.tabs.splice(i, 1);
  if (S.active >= i) S.active = Math.max(S.tabs.length ? 0 : -1, S.active - 1);
  if (S.active >= S.tabs.length) S.active = S.tabs.length - 1;
  if (!S.tabs.length && S.panes.length > 1) { closePane(S.focus); return; }
  saveState(); renderTabs(); renderTree(); renderView();
}

function renderTabs() {
  S.panes.forEach((pane, p) => {
    const bar = document.querySelector(`.pane-col[data-pane="${p}"] .tabbar`);
    if (bar) bar.innerHTML = tabsHtml(pane.tabs, pane.active) + (S.panes.length > 1 ? `<button class="icon-btn pane-close" data-pane-close="${p}" title="Close this side"><i class="fa-solid fa-xmark"></i></button>` : '');
  });
}
function tabsHtml(tabs, active) {
  return tabs.map((t, i) => {
    const name = t.kind === 'graph' ? (t.center ? `Graph: ${stem(t.center)}` : 'Graph view') : (t.path === ROOT_FILE ? 'MEMORY' : (t.kind === 'asset' ? basename(t.path) : stem(t.path.replace(/\.(canvas|base)$/i, '.md'))));
    const icon = t.kind === 'graph' ? 'fa-diagram-project' : t.kind === 'asset' ? 'fa-image' : t.kind === 'canvas' ? 'fa-object-group' : t.kind === 'base' ? 'fa-table' : 'fa-file-lines';
    const dirty = isText(t) && t.content !== t.saved;
    return `<div class="tab${i === active ? ' active' : ''}" data-i="${i}" title="${esc(t.path || name)}">
      <i class="fa-regular ${icon}" style="font-size:11px;opacity:.7"></i><span class="t-name">${esc(name)}</span>${dirty ? '<span class="dirty" title="Saving…"></span>' : ''}
      <button class="icon-btn close" data-close="${i}" title="Close"><i class="fa-solid fa-xmark"></i></button></div>`;
  }).join('') + '<button class="icon-btn tab-new" title="New tab (quick switcher)" style="margin:0 0 4px 2px"><i class="fa-solid fa-plus"></i></button>';
}

// Panes: one, or two side by side (split view)
function buildPanes() {
  const host = $('#panes');
  let rebuilt = false;
  if (host.children.length !== S.panes.length) {
    // New pane columns: the old editors go with their DOM, every pane is drawn again.
    S.panes.forEach((p) => { p.ed?.destroy(); p.ed = null; p.sim?.stop(); p.sim = null; });
    host.innerHTML = S.panes.map((_, i) => `<div class="pane-col" data-pane="${i}"><div class="tabbar"></div><div class="view"></div></div>`).join('');
    rebuilt = true;
  }
  host.classList.toggle('split', S.panes.length > 1);
  host.querySelectorAll('.pane-col').forEach((el) => el.classList.toggle('focused', Number(el.dataset.pane) === S.focus && S.panes.length > 1));
  renderTabs();
  return rebuilt;
}
function focusPane(i) {
  if (i === S.focus || !S.panes[i]) return;
  S.focus = i;
  buildPanes(); renderTree(); renderRight(); renderStatus(); saveState();
}
async function openSplit(path = activeTab()?.path) {
  if (S.panes.length === 1) S.panes.push({ tabs: [], active: -1, ed: null, sim: null });
  await flushAll();
  S.focus = 1;
  buildAndDraw();
  if (path) await openPath(path, { newTab: true }); else quickSwitcher();
  saveState();
}
function closePane(i) {
  withPane(i, () => { flushAll(); stopGraph(); S.panes[i].ed?.destroy(); });
  S.panes.splice(i, 1);
  S.focus = 0;
  renderAll(); renderTree(); renderRight(); renderStatus(); saveState();
}
$('#panes').addEventListener('pointerdown', (e) => {
  const col = e.target.closest('.pane-col');
  if (col) focusPane(Number(col.dataset.pane));
}, true);
$('#panes').addEventListener('click', (e) => {
  if (!e.target.closest('.tabbar')) return;
  if (e.target.closest('.tab-new')) return quickSwitcher(true);
  const paneClose = e.target.closest('[data-pane-close]');
  if (paneClose) return closePane(Number(paneClose.dataset.paneClose));
  const close = e.target.closest('[data-close]');
  if (close) { closeTab(Number(close.dataset.close)); return; }
  const tab = e.target.closest('.tab');
  if (tab) activate(Number(tab.dataset.i));
});
$('#panes').addEventListener('auxclick', (e) => { const tab = e.target.closest('.tabbar .tab'); if (tab && e.button === 1) closeTab(Number(tab.dataset.i)); });

// Saving
let saveTimer = null;
function scheduleSave(tab) { clearTimeout(saveTimer); saveTimer = setTimeout(() => saveTab(tab), 900); }
async function flush(tab) { if (isText(tab) && tab.content !== tab.saved && !tab.conflict) { clearTimeout(saveTimer); await saveTab(tab); } }
async function flushAll() { clearTimeout(saveTimer); for (const t of allTabs()) await flush(t); }

async function saveTab(tab, { force = false } = {}) {
  if (!isText(tab) || !tab.editable || (tab.conflict && !force)) return;
  if (tab.saving) { tab.again = true; return; }
  if (tab.content === tab.saved && !force) return;
  const content = tab.content;
  tab.saving = true; renderStatus();
  try {
    const res = await post('write', { path: tab.path, content, expected_mtime: force ? null : tab.mtime });
    tab.saved = content; tab.mtime = res.mtime; tab.conflict = false;
    loadIndexSoon();
  } catch (e) {
    if (e.code === 'conflict') { tab.conflict = true; if (tab === activeTab()) renderBanner(); } else fail(e);
  }
  tab.saving = false;
  if (tab.again) { tab.again = false; if (tab.content !== tab.saved) return saveTab(tab); }
  renderTabs(); renderStatus();
}
window.addEventListener('beforeunload', (e) => {
  if (allTabs().some((t) => isText(t) && t.content !== t.saved)) { flushAll(); e.preventDefault(); e.returnValue = ''; }
});
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushAll(); });

// The note view
function setMode(mode) {
  const tab = activeTab();
  if (tab?.kind !== 'note') return;
  if (mode === 'edit' && !tab.editable) { toast('This note is maintained by the agent and is read-only'); return; }
  tab.mode = mode || (tab.mode === 'edit' ? 'read' : 'edit');
  if (tab.mode === 'read') flush(tab);
  saveState(); renderView();
}

function toggleSourceMode() {
  ui.sourceMode = !ui.sourceMode;
  saveState();
  S.panes.forEach((p) => p.ed?.setLivePreview(!ui.sourceMode));
  toast(ui.sourceMode ? 'Source mode: all Markdown visible' : 'Live preview: formatting shown as you write');
}

function crumbs(path) {
  const parts = path.split('/');
  return parts.map((p, i) => `<span data-folder="${esc(parts.slice(0, i + 1).join('/'))}">${esc(i === parts.length - 1 && isNote(p) ? stem(p) : p)}</span>`).join(' / ');
}

function renderView() {
  const view = paneView();
  if (!view) return;
  const tab = activeTab();
  if (curEd()) { curEd().destroy(); S.panes[S.focus].ed = null; }
  if (!tab || tab.kind !== 'graph') stopGraph();
  if (!tab) { view.innerHTML = emptyState(); renderRight(); renderStatus(); return; }
  if (tab.kind === 'graph') { renderGraph(view, tab); renderRight(); renderStatus(); return; }
  if ((tab.kind === 'canvas' || tab.kind === 'base') && !tab.loading) {
    if (tab.kind === 'canvas') renderCanvas(view, tab); else renderBase(view, tab);
    renderRight(); renderStatus(); return;
  }
  const back = tab.history.length; const fwd = tab.fwd.length;
  const head = `<div class="note-head">
    <button class="icon-btn" data-act="back" title="Back (Alt ←)" ${back ? '' : 'disabled style="opacity:.35"'}><i class="fa-solid fa-arrow-left"></i></button>
    <button class="icon-btn" data-act="fwd" title="Forward (Alt →)" ${fwd ? '' : 'disabled style="opacity:.35"'}><i class="fa-solid fa-arrow-right"></i></button>
    <div class="crumbs">${crumbs(tab.path)}</div>
    ${tab.kind === 'note' ? `${tab.editable ? '' : '<!--'}<button class="icon-btn" data-act="mode" title="${tab.mode === 'edit' ? 'Reading view' : 'Edit'} (Ctrl/⌘ E)"><i class="fa-solid ${tab.mode === 'edit' ? 'fa-book-open' : 'fa-pen'}"></i></button>${tab.editable ? '' : '-->'}
    ${tab.mode === 'edit' ? '<button class="icon-btn" data-act="find" title="Find and replace (Ctrl/⌘ F, Ctrl/⌘ H)"><i class="fa-solid fa-magnifying-glass"></i></button><button class="icon-btn" data-act="source" title="Live preview or source mode"><i class="fa-solid fa-code"></i></button>' : ''}
    <button class="icon-btn" data-act="graph" title="Local graph"><i class="fa-solid fa-diagram-project"></i></button>` : ''}
    <button class="icon-btn" data-act="bookmark" title="${S.bookmarks.includes(tab.path) ? 'Remove bookmark' : 'Bookmark'}"><i class="fa-${S.bookmarks.includes(tab.path) ? 'solid' : 'regular'} fa-star"></i></button>
    <button class="icon-btn hide-sm" data-act="split" title="${S.panes.length > 1 ? 'Close the split' : 'Open beside (split view)'}"><i class="fa-solid ${S.panes.length > 1 ? 'fa-window-maximize' : 'fa-table-columns'}"></i></button>
    <button class="icon-btn" data-act="more" title="More"><i class="fa-solid fa-ellipsis-vertical"></i></button></div>`;
  if (tab.kind === 'asset') {
    const body = isImage(tab.path) ? `<img src="${esc(fileUrl(tab.path))}" alt="${esc(basename(tab.path))}" style="max-width:100%;border-radius:10px">`
      : `<iframe src="${esc(fileUrl(tab.path))}" title="${esc(basename(tab.path))}" style="width:100%;height:75vh;border:1px solid var(--line);border-radius:10px"></iframe>`;
    view.innerHTML = `${head}<div class="scroller"><div class="doc">${body}</div></div>`;
    renderRight(); renderStatus(); return;
  }
  const readonlyTitle = !tab.editable || tab.path === ROOT_FILE;
  let body;
  if (tab.loading) body = '<div class="muted">Loading…</div>';
  else if (tab.error) body = `<div class="banner warn"><i class="fa-solid fa-triangle-exclamation"></i>${esc(tab.error)}</div>`;
  else if (tab.mode === 'edit') body = '<div class="editor" id="editor"></div>';
  else {
    const { props, html } = renderMarkdown(tab.content, tab.path, { readonly: !tab.editable });
    body = `${propsHtml(props)}<div class="md" id="reading">${html || `<p class="muted">This note is empty.${tab.editable ? ' Press Ctrl/⌘ E to write.' : ''}</p>`}</div>`;
  }
  view.innerHTML = `${head}<div class="scroller"><div class="doc"><div id="banner"></div>
    <input class="inline-title" id="inline-title" value="${esc(tab.path === ROOT_FILE ? 'MEMORY' : stem(tab.path))}" ${readonlyTitle ? 'readonly' : ''} spellcheck="false" aria-label="Note name">
    ${body}</div></div>`;
  renderBanner();
  if ($v('#editor')) mountEditor(tab);
  const reading = $v('#reading');
  if (reading) { highlightCode(reading); fillRich(reading); fillEmbeds(reading, 0); }
  $v('.scroller').scrollTop = tab.scroll || 0;
  tab.scroll = 0;
  renderRight(); renderStatus();
}

function renderBanner() {
  const tab = activeTab(); const el = $v('#banner');
  if (!el || tab?.kind !== 'note') return;
  if (tab.conflict) {
    el.innerHTML = `<div class="banner warn"><i class="fa-solid fa-code-merge"></i><span>This note changed on disk (probably the agent) while you were editing. Your edits aren't saved yet.</span>
      <button data-act="theirs">Load the disk version</button><button data-act="mine">Keep mine</button><button data-act="copy-mine">Copy mine</button></div>`;
  } else if (!tab.editable) {
    el.innerHTML = '<div class="banner info"><i class="fa-solid fa-lock"></i>The agent maintains this note; it is read-only here.</div>';
  } else el.innerHTML = '';
}

function emptyState() {
  const recent = [...S.notes.values()].sort((a, b) => b.mtime - a.mtime).slice(0, 6);
  return `<div class="empty-state"><i class="fa-solid fa-gem" style="font-size:34px;color:var(--brand)"></i><h2>Your vault</h2>
    <p>Your notes for everything: work, ideas, references, life. Every agent can read them, and each agent's own memory sits below. Notes link with <code>[[wikilinks]]</code> like Obsidian.</p>
    <div class="actions">
      <button class="link-btn" data-act="new">Create a new note <kbd>${isMac ? '⌘' : 'Ctrl'} N</kbd></button>
      <button class="link-btn" data-act="switcher">Go to a note <kbd>${isMac ? '⌘' : 'Ctrl'} O</kbd></button>
      <button class="link-btn" data-act="daily">Open today's daily note</button>
      <button class="link-btn" data-act="open-graph">Open graph view <kbd>${isMac ? '⌘' : 'Ctrl'} G</kbd></button>
      <button class="link-btn" data-act="topo">Topologies of Thoughts</button>
      ${S.lc.has('knowledge/home.md') ? '<button class="link-btn" data-act="home">Open Home</button>' : '<button class="link-btn" data-act="setup">Set up my vault (numbered folders)</button>'}
    </div>
    ${recent.length ? `<div style="margin-top:22px;text-align:left"><div class="side-head">Recently changed</div>${recent.map((n) => `<a class="side-item" data-open="${esc(n.path)}">${esc(n.path === ROOT_FILE ? 'MEMORY' : stem(n.path))} <small>· ${esc(dirname(n.path) || 'vault')} · ${ago(n.mtime)}</small></a>`).join('')}</div>` : ''}</div>`;
}


function scrollToHeading(heading) {
  const tab = activeTab();
  if (!tab) return;
  if (tab.mode === 'edit') {
    const lines = tab.content.split('\n');
    const at = lines.findIndex((l) => /^#{1,6}\s/.test(l) && slug(l.replace(/^#+\s*/, '')) === slug(heading));
    if (at >= 0) caretToLine(at + 1);
    return;
  }
  const el = $v(`#h-${CSS.escape(slug(heading))}`);
  if (el) { el.scrollIntoView({ block: 'start', behavior: 'smooth' }); el.animate([{ background: 'var(--mark)' }, { background: 'transparent' }], { duration: 1500 }); }
}

function caretToLine(line) { curEd()?.gotoLine(line); }

// The editor: CodeMirror live preview (vendor/lrm-editor.js)
function mountEditor(tab) {
  const host = $v('#editor');
  if (!window.LrmEditor) {
    host.innerHTML = '<div class="banner warn">The editor did not load. Reload the page.</div>';
    return;
  }
  const editor = window.LrmEditor.create(host, {
    doc: tab.content,
    livePreview: ui.sourceMode !== true,
    readOnly: !tab.editable,
    placeholder: 'Start writing…  [[ links a note, # adds a tag, paste an image to attach it',
    suggest: (q) => {
      const notes = rankNotes(q, 12).map((it) => ({ label: it.alias || it.name, detail: `${it.alias ? `${it.name} · ` : ''}${dirname(it.path) || 'vault'}`, insert: linkName(it.path) + (it.alias ? `|${it.alias}` : '') }));
      const lq = q.toLowerCase();
      const files = lq ? [...S.lc.values()].filter((p) => !isNote(p) && basename(p).toLowerCase().includes(lq)).slice(0, 6)
        .map((p) => ({ label: basename(p), detail: dirname(p), insert: basename(p) })) : [];
      return [...notes, ...files];
    },
    tags: () => [...S.tags.keys()],
    resolve: (name) => resolveLink(name, tab.path),
    fileUrl: (src, resolved) => {
      if (resolved) return fileUrl(src);
      if (/^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith('//')) return /^https?:/i.test(src) ? src : null;
      const target = resolveLink(src, tab.path, true);
      return target ? fileUrl(target) : null;
    },
    render: renderRich,
    onChange: (text) => {
      tab.content = text;
      scheduleSave(tab);
      if (!document.querySelector(`.pane-col[data-pane="${S.focus}"] .tab.active .dirty`)) renderTabs();
      renderStatusSoon(); renderRightSoon();
    },
    onSave: () => saveTab(tab),
    openLink: (href, { md: mdLink, newTab }) => {
      const [name, heading = ''] = href.split('#');
      const target = name ? resolveLink(name, tab.path, mdLink) : tab.path;
      if (target) openPath(target, { newTab, heading });
      else createFromLink(name, tab.path);
    },
    openTag: (tag) => searchFor(`tag:#${tag}`),
    onFiles: (files) => attachFiles(files, tab.path),
  });
  S.panes[S.focus].ed = editor;
  if (tab.focusEditor) { tab.focusEditor = false; editor.focus(); }
}

// Formulas (KaTeX) and diagrams (Mermaid), loaded the first time a note needs them
const loading = {};
function loadScript(src) {
  return (loading[src] ||= new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src; el.onload = resolve; el.onerror = () => reject(new Error(`Could not load ${src}`));
    document.head.appendChild(el);
  }));
}
function loadCss(href) {
  if (loading[href]) return;
  loading[href] = true;
  const el = document.createElement('link'); el.rel = 'stylesheet'; el.href = href;
  document.head.appendChild(el);
}
let mermaidTheme = '';
async function mermaidReady() {
  await loadScript('/assets/brand/vendor/mermaid.min.js');
  const theme = document.documentElement.classList.contains('dark') ? 'dark' : 'default';
  if (theme !== mermaidTheme) {
    window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme, fontFamily: 'Inter, system-ui, sans-serif' });
    mermaidTheme = theme;
  }
  return window.mermaid;
}
let mermaidSeq = 0;
function renderRich(kind, source, el) {
  if (kind === 'mermaid') {
    el.textContent = 'Drawing diagram…';
    mermaidReady().then((m) => m.render(`lrm-mermaid-${++mermaidSeq}`, source))
      .then(({ svg }) => { el.innerHTML = svg; })
      .catch((e) => { el.innerHTML = `<pre class="rich-error">Diagram error: ${esc(String(e.message || e).split('\n')[0])}</pre>`; document.querySelectorAll('[id^="dlrm-mermaid-"]').forEach((n) => n.remove()); });
    return;
  }
  loadCss('/assets/brand/vendor/katex/katex.min.css');
  el.textContent = source;
  loadScript('/assets/brand/vendor/katex/katex.min.js').then(() => {
    window.katex.render(source, el, { displayMode: kind === 'mathblock', throwOnError: false, strict: 'ignore' });
  }).catch(() => {});
}
function fillRich(root) {
  root.querySelectorAll('[data-rich]:not([data-done])').forEach((el) => {
    el.dataset.done = '1';
    renderRich(el.dataset.rich, el.dataset.src, el);
  });
}

// Attachments: pasted or dropped images and PDFs
const ATTACH = /\.(png|jpe?g|gif|webp|svg|pdf)$/i;
async function attachFiles(files, notePath, folder = null) {
  const out = [];
  for (const file of files) {
    let name = file.name || '';
    if (!name || name === 'image.png') {
      const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
      name = `Pasted image ${stamp}.${(file.type.split('/')[1] || 'png').replace('jpeg', 'jpg').replace('svg+xml', 'svg')}`;
    }
    if (!ATTACH.test(name)) { toast(`${file.name || 'That file'}: only images and PDFs can be attached`, true); continue; }
    if (file.size > 15 * 1024 * 1024) { toast(`${name} is larger than 15 MB`, true); continue; }
    const data = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(',')[1] || '');
      r.onerror = () => reject(r.error);
      r.readAsDataURL(file);
    });
    try {
      const res = await post('upload', { note: notePath || '', folder: folder || '', name, data });
      out.push(res.path);
    } catch (e) { fail(e); }
  }
  if (!out.length) return '';
  await loadIndex({ quiet: true });
  toast(out.length > 1 ? `Attached ${out.length} files` : `Attached ${basename(out[0])}`);
  return out.map((p) => `![[${basename(p)}]]`).join('\n');
}

// Templates: notes in knowledge/templates, with {{title}}, {{date}}, {{time}}
const TEMPLATE_DIR = 'knowledge/templates';
const STARTER_TEMPLATES = {
  'Meeting notes': '---\ntags: [meeting]\ndate: {{date}}\nattendees: []\n---\n\n# {{title}}\n\n## Agenda\n- \n\n## Notes\n\n## Decisions\n- \n\n## Action items\n- [ ] \n',
  'Project': '---\ntags: [project]\nstatus: active\nowner: \ndue: \n---\n\n# {{title}}\n\n## Goal\n\n## Plan\n- [ ] \n\n## Links\n- \n\n## Log\n- {{date}}: started\n',
  'Client': '---\ntags: [client]\ncompany: \ncontact: \nphone: \nemail: \nstatus: lead\n---\n\n# {{title}}\n\n## About\n\n## Needs\n\n## History\n- {{date}}: \n\n## Next step\n- [ ] \n',
  'Daily note': '# {{date:dddd, D MMMM YYYY}}\n\n## Plan\n- [ ] \n\n## Notes\n\n## Done\n- \n',
};
function fillTemplate(text, title) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const fmt = (f) => f.replace(/YYYY|MMMM|MM|M|dddd|DD|D|HH|mm/g, (k) => ({
    YYYY: now.getFullYear(), MMMM: MONTHS[now.getMonth()], MM: pad(now.getMonth() + 1), M: now.getMonth() + 1,
    dddd: DAYS[now.getDay()], DD: pad(now.getDate()), D: now.getDate(), HH: pad(now.getHours()), mm: pad(now.getMinutes()),
  })[k]);
  return text.replace(/\{\{\s*(title|date|time)(?::([^}]+))?\s*\}\}/g, (_, key, f) => (
    key === 'title' ? title : key === 'date' ? fmt(f || 'YYYY-MM-DD') : fmt(f || 'HH:mm')));
}
const templates = () => [...S.notes.keys()].filter((p) => p.startsWith(`${TEMPLATE_DIR}/`)).sort();
async function readNote(path) { return (await api('read', { path })).content; }
async function ensureTemplates() {
  if (templates().length) return true;
  const ok = await ask({ title: 'No templates yet', text: `Create four starter templates (Meeting notes, Project, Client, Daily note) in <b>${TEMPLATE_DIR}</b>? You can edit them like any note.`, ok: 'Create them' });
  if (!ok) return false;
  try {
    await post('create', { path: TEMPLATE_DIR, kind: 'folder' }).catch(() => {});
    for (const [name, text] of Object.entries(STARTER_TEMPLATES)) await post('create', { path: `${TEMPLATE_DIR}/${name}.md`, content: text }).catch(() => {});
    await loadIndex({ quiet: true });
    return true;
  } catch (e) { fail(e); return false; }
}
function pickTemplate(onPick) {
  picker({
    placeholder: 'Choose a template…',
    footer: `<span>Templates live in <b>${TEMPLATE_DIR}</b></span>`,
    items: (q) => templates().map((p) => ({ path: p, html: esc(stem(p)), score: fuzzy(q, stem(p)) })).filter((x) => x.score > 0).sort((a, b) => b.score - a.score),
    onPick: (it) => onPick(it.path),
  });
}
async function insertTemplate() {
  const tab = activeTab();
  if (tab?.kind !== 'note' || !tab.editable) return toast('Open a note you can edit first', true);
  if (!(await ensureTemplates())) return;
  pickTemplate(async (path) => {
    const text = fillTemplate(await readNote(path), stem(tab.path));
    if (tab.mode !== 'edit') { tab.mode = 'edit'; renderView(); }
    curEd()?.insert(text);
  });
}
async function newFromTemplate() {
  if (!(await ensureTemplates())) return;
  pickTemplate(async (path) => {
    const name = await ask({ title: `New note from “${stem(path)}”`, value: stem(path) === 'Meeting notes' ? `Meeting ${today()}` : '', ok: 'Create' });
    if (!name) return;
    let folder = S.folder && (SECTIONS.includes(S.folder) || S.dirs.has(S.folder)) && !S.folder.startsWith(TEMPLATE_DIR) ? S.folder : 'knowledge';
    const target = uniquePath(folder, name.replace(/[\\/<>:"|?*#^[\]]/g, ' ').trim() || 'Untitled');
    createNote(target, fillTemplate(await readNote(path), stem(target)), { mode: 'edit' });
  });
}

function noteMenu(x, y) {
  const tab = activeTab();
  if (!tab?.path) return;
  const locked = tab.path === ROOT_FILE || !tab.editable;
  const items = [
    { icon: 'fa-solid fa-link', label: 'Copy [[link]]', fn: () => copy(`[[${linkName(tab.path)}]]`) },
    { icon: 'fa-regular fa-folder-open', label: 'Reveal in file explorer', fn: () => reveal(tab.path) },
    { icon: 'fa-solid fa-rotate', label: 'Reload from disk', fn: () => loadTab(tab) },
  ];
  if (!locked) {
    items.push('-', { icon: 'fa-solid fa-folder-tree', label: 'Move to…', fn: () => renamePath(tab.path) });
    items.push({ icon: 'fa-regular fa-trash-can', label: 'Delete', danger: true, fn: () => deletePath(tab.path, false) });
  }
  showMenu(x, y, items);
}

function reveal(path) {
  const parts = path.split('/');
  for (let i = 1; i < parts.length; i++) S.expanded.add(parts.slice(0, i).join('/'));
  setPane('left', true); setLeftTab('files'); renderTree();
  document.querySelector(`.tree-row[data-path="${CSS.escape(path)}"]`)?.scrollIntoView({ block: 'center' });
}

const view = $('#panes');
view.addEventListener('click', async (e) => {
  const tab = activeTab();
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act) {
    if (act === 'mode') return setMode();
    if (act === 'find') return curEd()?.find();
    if (act === 'bookmark') return toggleBookmark(tab.path);
    if (act === 'split') return S.panes.length > 1 ? closePane(S.focus === 0 ? 1 : 0) : openSplit(tab.path);
    if (act === 'source') return toggleSourceMode();
    if (act === 'graph') return openGraph(tab.path);
    if (act === 'more') { const r = e.target.closest('[data-act]').getBoundingClientRect(); return noteMenu(r.right - 190, r.bottom + 4); }
    if (act === 'back' && tab.history.length) { tab.fwd.push(tab.path); return openPath(tab.history.pop(), { fromHistory: true }); }
    if (act === 'fwd' && tab.fwd.length) { tab.history.push(tab.path); return openPath(tab.fwd.pop(), { fromHistory: true }); }
    if (act === 'theirs') { tab.conflict = false; return loadTab(tab); }
    if (act === 'mine') { tab.conflict = false; renderBanner(); return saveTab(tab, { force: true }); }
    if (act === 'copy-mine') return copy(tab.content);
    if (act === 'new') return newNote();
    if (act === 'switcher') return quickSwitcher();
    if (act === 'daily') return dailyNote();
    if (act === 'open-graph') return openGraph();
    if (act === 'topo') return openTopology();
    if (act === 'setup') return setupVault();
    if (act === 'home') return openPath('knowledge/Home.md');
  }
  const folder = e.target.closest('[data-folder]');
  if (folder) { reveal(folder.dataset.folder); return; }
  const open = e.target.closest('[data-open]');
  if (open) { e.preventDefault(); return openPath(open.dataset.open, { newTab: mod(e) }); }
  const tag = e.target.closest('[data-tag]');
  if (tag) { e.preventDefault(); return searchFor(`tag:#${tag.dataset.tag}`); }
  const title = e.target.closest('.callout.foldable > .callout-title');
  if (title) { title.parentElement.classList.toggle('folded'); return; }
  const link = e.target.closest('a.internal[data-href]');
  if (link) {
    e.preventDefault();
    if (link.dataset.create) return createFromLink(link.dataset.href, tab.path);
    return openPath(link.dataset.href, { newTab: mod(e), heading: link.dataset.heading || '' });
  }
  const box = e.target.closest('input[type=checkbox][data-task]');
  if (box && tab?.kind === 'note' && tab.editable && !box.closest('.embed')) {
    const next = toggleTask(tab.content, Number(box.dataset.task));
    if (next != null) { tab.content = next; box.closest('li')?.classList.toggle('done', box.checked); saveTab(tab); renderRight(); }
  }
});
view.addEventListener('auxclick', (e) => {
  const link = e.target.closest('a.internal[data-href]:not([data-create])');
  if (link && e.button === 1) { e.preventDefault(); openPath(link.dataset.href, { newTab: true }); }
});

// Inline title: renames the note, like Obsidian.
view.addEventListener('keydown', (e) => {
  if (e.target.id === 'inline-title' && e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
  if (e.target.id === 'inline-title' && e.key === 'Escape') { const tab = activeTab(); e.target.value = stem(tab.path); e.target.blur(); }
});
view.addEventListener('focusout', (e) => {
  if (e.target.id !== 'inline-title' || e.target.readOnly) return;
  const tab = activeTab();
  const name = e.target.value.trim();
  if (!name || name === stem(tab.path)) { e.target.value = stem(tab.path); return; }
  if (/[\\/<>:"|?*#^[\]]/.test(name)) { toast('Names can’t contain \\ / < > : " | ? * # ^ [ ]', true); e.target.value = stem(tab.path); return; }
  movePath(tab.path, `${dirname(tab.path)}/${name}.md`);
});

// Finding notes by name
function fuzzy(query, text) {
  const q = query.toLowerCase(); const t = text.toLowerCase();
  if (!q) return 1;
  const at = t.indexOf(q);
  if (at >= 0) return 100 - at - t.length / 100 + (at === 0 ? 50 : 0);
  let i = 0; let score = 0; let last = -1;
  for (let j = 0; j < t.length && i < q.length; j++) if (t[j] === q[i]) { score += j === last + 1 ? 3 : 1; last = j; i++; }
  return i === q.length ? score : 0;
}
function rankNotes(query, limit = 50) {
  const out = [];
  for (const [path, e] of S.notes) {
    const name = path === ROOT_FILE ? 'MEMORY' : stem(path);
    let score = Math.max(fuzzy(query, name) * 2, fuzzy(query, path));
    let alias = '';
    for (const a of e.aliases || []) { const s = fuzzy(query, a) * 2; if (s > score) { score = s; alias = a; } }
    if (score > 0) out.push({ path, name, alias, score: score + (query ? 0 : e.mtime / 1e10) });
  }
  return out.sort((a, b) => b.score - a.score || (S.notes.get(b.path).mtime - S.notes.get(a.path).mtime)).slice(0, limit);
}
// Bookmarks (saved on the server per agent)
async function loadPrefs() {
  try { S.bookmarks = (await api('prefs')).prefs.bookmarks || []; } catch { S.bookmarks = []; }
  renderBookmarks();
}
let prefsT = null;
function savePrefs() {
  renderBookmarks();
  clearTimeout(prefsT);
  prefsT = setTimeout(() => post('prefs', { prefs: { bookmarks: S.bookmarks } }).catch(fail), 300);
}
function toggleBookmark(path = activeTab()?.path) {
  if (!path) return;
  const had = S.bookmarks.includes(path);
  S.bookmarks = had ? S.bookmarks.filter((b) => b !== path) : [...S.bookmarks, path];
  savePrefs();
  toast(had ? 'Bookmark removed' : 'Bookmarked');
  renderAll();
}
function renderBookmarks() {
  const el = $('#bookmark-list');
  if (!el) return;
  el.innerHTML = S.bookmarks.length ? S.bookmarks.map((p, i) => {
    const icon = isCanvas(p) ? 'fa-regular fa-object-group' : isBase(p) ? 'fa-solid fa-table' : 'fa-regular fa-file-lines';
    const missing = !S.lc.has(p.toLowerCase());
    return `<div class="tree-row bm${missing ? ' missing' : ''}" data-bm="${esc(p)}" draggable="true" data-i="${i}"><i class="ico ${icon}"></i><span class="name">${esc(p === ROOT_FILE ? 'MEMORY' : basename(p).replace(/\.(md|canvas|base)$/i, ''))}</span><small class="muted">${esc(dirname(p))}</small>
      <button class="icon-btn more" data-unbm="${esc(p)}" title="Remove bookmark"><i class="fa-solid fa-xmark"></i></button></div>`;
  }).join('') : '<div class="hint">No bookmarks yet. Use the ☆ button on a note, or the command palette.</div>';
}

$('#bookmark-list').addEventListener('click', (e) => {
  const un = e.target.closest('[data-unbm]');
  if (un) { e.stopPropagation(); S.bookmarks = S.bookmarks.filter((b) => b !== un.dataset.unbm); savePrefs(); renderAll(); return; }
  const row = e.target.closest('[data-bm]');
  if (row && S.lc.has(row.dataset.bm.toLowerCase())) { openPath(row.dataset.bm, { newTab: mod(e) }); if (small()) setPane('left', false); }
});
let bmDrag = null;
$('#bookmark-list').addEventListener('dragstart', (e) => { bmDrag = Number(e.target.closest('[data-bm]')?.dataset.i); });
$('#bookmark-list').addEventListener('dragover', (e) => { if (bmDrag != null) e.preventDefault(); });
$('#bookmark-list').addEventListener('drop', (e) => {
  const row = e.target.closest('[data-bm]');
  if (bmDrag == null || !row) return;
  e.preventDefault();
  const to = Number(row.dataset.i);
  const [item] = S.bookmarks.splice(bmDrag, 1);
  S.bookmarks.splice(to, 0, item);
  bmDrag = null; savePrefs();
});

// Unlinked mentions: notes that name this one without linking to it
let mentionList = [];
async function loadMentions(tab) {
  const box = $('#mentions');
  try {
    const { mentions } = await api('mentions', { path: tab.path });
    if (activeTab() !== tab || S.rightTab !== 'links') return;
    const linked = S.back.get(tab.path) || new Set();
    mentionList = mentions.filter((m) => !linked.has(m.path)).map((m) => ({ ...m, target: tab.path }));
    $('#mention-count').textContent = mentionList.length;
    box.querySelector('.hint')?.remove();
    box.insertAdjacentHTML('beforeend', mentionList.map((m, i) => `<div class="mention"><a class="side-item" data-open="${esc(m.path)}">${esc(stem(m.path))} <small>line ${m.line}</small></a>
      <div class="mention-text">${esc(m.text).replace(new RegExp(m.match.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'), (x) => `<mark>${x}</mark>`)}</div>
      ${S.notes.get(m.path)?.readonly ? '' : `<button class="btn mini" data-link-mention="${i}">Link</button>`}</div>`).join('') || '<div class="hint">None: no other note names this one without linking it.</div>');
  } catch (e) { if (box) box.querySelector('.hint').textContent = e.message; }
}
async function linkMention(i) {
  const m = mentionList[i];
  if (!m) return;
  try {
    const data = await api('read', { path: m.path });
    const lines = data.content.split('\n');
    const line = lines[m.line - 1] ?? '';
    if (line.slice(m.col, m.col + m.match.length).toLowerCase() !== m.match.toLowerCase()) throw new Error('That note changed; refresh and try again');
    const name = linkName(m.target);
    const link = m.match === stem(m.target) ? `[[${name}]]` : `[[${name}|${m.match}]]`;
    lines[m.line - 1] = line.slice(0, m.col) + link + line.slice(m.col + m.match.length);
    await post('write', { path: m.path, content: lines.join('\n'), expected_mtime: data.mtime });
    toast(`Linked in ${stem(m.path)}`);
    await loadIndex({ quiet: true });
    renderRight();
  } catch (e) { fail(e); }
}

// Calendar of daily notes (memory/YYYY-MM-DD.md)
function calendarHtml() {
  const now = new Date();
  const base = S.calMonth ?? 0;
  const first = new Date(now.getFullYear(), now.getMonth() + base, 1);
  const days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7; // weeks start on Monday
  const iso = (d) => `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push('<span></span>');
  for (let d = 1; d <= days; d++) {
    const day = iso(d);
    const has = S.notes.has(`memory/${day}.md`);
    const isToday = day === today();
    const open = activeTab()?.path === `memory/${day}.md`;
    cells.push(`<button class="cal-day${has ? ' has' : ''}${isToday ? ' today' : ''}${open ? ' open' : ''}" data-day="${day}" title="${has ? 'Open' : 'Create'} the note for ${day}">${d}</button>`);
  }
  const count = [...S.notes.keys()].filter((p) => p.startsWith(`memory/${iso(1).slice(0, 7)}`)).length;
  return `<div class="cal"><div class="cal-head"><button class="icon-btn" data-month="${base - 1}" title="Previous month"><i class="fa-solid fa-chevron-left"></i></button>
    <b>${first.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })}</b>
    <button class="icon-btn" data-month="${base + 1}" title="Next month"><i class="fa-solid fa-chevron-right"></i></button></div>
    <div class="cal-grid">${['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((x) => `<span class="cal-dow">${x}</span>`).join('')}${cells.join('')}</div>
    <div class="hint">${count} daily note${count === 1 ? '' : 's'} this month. Click a day to open it, or to create it.${base ? ' <button class="link-btn" data-month="0">Today</button>' : ''}</div></div>`;
}

// Right pane: backlinks, outgoing, outline, info
function headingsOf(text) {
  const out = []; let fence = false;
  const { body } = splitFrontmatter(text);
  const offset = text.length - body.length;
  const lines = body.split('\n'); let lineNo = text.slice(0, offset).split('\n').length - 1;
  for (const l of lines) {
    lineNo++;
    if (/^\s*(```|~~~)/.test(l)) fence = !fence;
    const m = !fence && /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(l);
    if (m) out.push({ level: m[1].length, text: m[2], line: lineNo });
  }
  return out;
}
function statsOf(text) {
  const { body } = splitFrontmatter(text);
  const words = (body.match(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu) || []).length;
  return { words, chars: body.length };
}

let rightTimer = null;
const renderRightSoon = () => { clearTimeout(rightTimer); rightTimer = setTimeout(renderRight, 400); };
function renderRight() {
  const el = $('#right-body');
  const tab = activeTab();
  document.querySelectorAll('[data-right]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.right === S.rightTab)));
  $('#body').classList.toggle('dock-graph', S.rightTab === 'graph');
  if (S.rightTab === 'graph') return renderDockGraph(el, tab);
  stopDock();
  if (S.rightTab === 'calendar') { el.innerHTML = calendarHtml(); return; }
  if (!tab || tab.kind !== 'note') {
    el.innerHTML = `<div class="hint">${tab?.kind === 'graph' ? 'Click a dot to open its note. Drag to rearrange, scroll to zoom.' : 'Open a note to see its links, outline and properties.'}</div>`;
    return;
  }
  const item = (p, extra = '') => `<a class="side-item" data-open="${esc(p)}" title="${esc(p)}">${esc(p === ROOT_FILE ? 'MEMORY' : stem(p))} <small>${esc(extra || dirname(p))}</small></a>`;
  if (S.rightTab === 'links') {
    const back = [...(S.back.get(tab.path) || [])].sort();
    const out = [...(S.out.get(tab.path) || [])].sort();
    const missing = [...(S.unresolved.get(tab.path) || [])].sort();
    el.innerHTML = `<div class="side-section"><div class="side-head"><i class="fa-solid fa-arrow-right-to-bracket"></i>Backlinks<span class="count">${back.length}</span></div>
        ${back.map((p) => item(p)).join('') || '<div class="hint">No notes link here yet.</div>'}</div>
      <div class="side-section"><div class="side-head"><i class="fa-solid fa-arrow-right-from-bracket"></i>Outgoing links<span class="count">${out.length + missing.length}</span></div>
        ${out.map((p) => item(p)).join('')}${missing.map((n) => `<a class="side-item unresolved" data-create-link="${esc(n)}" title="Not created yet: click to create">${esc(n)} <small>not created</small></a>`).join('')}
        ${out.length + missing.length ? '' : '<div class="hint">This note links nowhere.</div>'}</div>
      <div class="side-section" id="mentions"><div class="side-head"><i class="fa-solid fa-quote-left"></i>Unlinked mentions<span class="count" id="mention-count"></span></div><div class="hint">Looking…</div></div>`;
    loadMentions(tab);
  } else if (S.rightTab === 'calendar') {
    el.innerHTML = calendarHtml();
  } else if (S.rightTab === 'outline') {
    const hs = headingsOf(tab.content);
    const min = Math.min(...hs.map((h) => h.level));
    el.innerHTML = hs.map((h) => `<a class="side-item outline-item" style="--lvl:${h.level - min}" data-heading-go="${esc(h.text)}" data-line="${h.line}">${esc(h.text)}</a>`).join('') || '<div class="hint">No headings in this note.</div>';
  } else {
    const entry = S.notes.get(tab.path) || {};
    const { words, chars } = statsOf(tab.content);
    const { props } = splitFrontmatter(tab.content);
    const tags = entry.tags || [];
    el.innerHTML = `<div class="side-section"><div class="side-head"><i class="fa-solid fa-hashtag"></i>Tags<span class="count">${tags.length}</span></div>
        <div style="padding:0 4px">${tags.map((t) => `<button class="tag-chip" data-tag="${esc(t)}">#${esc(t)}</button>`).join('') || '<div class="hint">No tags.</div>'}</div></div>
      ${props?.length ? `<div class="side-section"><div class="side-head"><i class="fa-solid fa-sliders"></i>Properties</div><div style="padding:0 4px">${propsHtml(props)}</div></div>` : ''}
      <div class="side-section"><div class="side-head"><i class="fa-solid fa-circle-info"></i>File</div><div style="padding:0 8px;font-size:12px;color:var(--text-2);display:grid;gap:3px">
        <div>${esc(tab.path)}</div><div>${words.toLocaleString('en-US')} words · ${chars.toLocaleString('en-US')} characters</div>
        ${entry.mtime ? `<div>Changed ${ago(entry.mtime)}</div>` : ''}
        <div class="muted">${tab.path === ROOT_FILE ? 'Long-term memory: the agent reads it in every conversation.' : sectionOf(tab.path) === 'memory' ? 'Agent memory: what this agent remembers; found by memory search.' : 'Your vault: every agent can find this note with memory search.'}</div></div></div>`;
  }
}
$('#right-body').addEventListener('click', (e) => {
  const day = e.target.closest('[data-day]');
  if (day) { dailyNote(day.dataset.day); return; }
  const month = e.target.closest('[data-month]');
  if (month) { S.calMonth = Number(month.dataset.month); renderRight(); return; }
  const linkIt = e.target.closest('[data-link-mention]');
  if (linkIt) { linkMention(Number(linkIt.dataset.linkMention)); return; }
  const open = e.target.closest('[data-open]');
  if (open) return openPath(open.dataset.open, { newTab: mod(e) });
  const create = e.target.closest('[data-create-link]');
  if (create) return createFromLink(create.dataset.createLink, activeTab()?.path);
  const tag = e.target.closest('[data-tag]');
  if (tag) return searchFor(`tag:#${tag.dataset.tag}`);
  const h = e.target.closest('[data-heading-go]');
  if (h) { if (activeTab()?.mode === 'edit') caretToLine(Number(h.dataset.line)); else scrollToHeading(h.dataset.headingGo); }
});
document.querySelectorAll('[data-right]').forEach((b) => b.addEventListener('click', () => { S.rightTab = b.dataset.right; saveState(); renderRight(); }));

// Status bar
let statusTimer = null;
const renderStatusSoon = () => { clearTimeout(statusTimer); statusTimer = setTimeout(renderStatus, 300); };
function renderStatus() {
  const tab = activeTab(); const el = $('#status');
  if (!tab) { el.innerHTML = `<span>${S.notes.size.toLocaleString('en-US')} notes</span>`; return; }
  if (tab.kind !== 'note') { el.innerHTML = `<span>${S.notes.size.toLocaleString('en-US')} notes</span>`; return; }
  const { words, chars } = statsOf(tab.content);
  const back = S.back.get(tab.path)?.size || 0;
  const state = !tab.editable ? 'Read-only' : tab.conflict ? '<span class="conflict">Not saved: conflict</span>' : tab.saving ? 'Saving…' : tab.content !== tab.saved ? 'Unsaved' : 'Saved';
  el.innerHTML = `<span>${back} backlink${back === 1 ? '' : 's'}</span><span>${words.toLocaleString('en-US')} words</span><span class="hide-sm">${chars.toLocaleString('en-US')} characters</span><span>${state}</span>`;
}

// Search & tags (left pane)
function setLeftTab(name) {
  S.leftTab = name;
  for (const n of ['files', 'search', 'tags', 'bookmarks']) $(`#left-${n}`).hidden = n !== name;
  document.querySelectorAll('[data-left]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.left === name)));
  saveState();
}
document.querySelectorAll('[data-left]').forEach((b) => b.addEventListener('click', () => { setLeftTab(b.dataset.left); if (b.dataset.left === 'search') $('#search-q').focus(); }));

function searchFor(q) { setPane('left', true); setLeftTab('search'); $('#search-q').value = q; runSearch(); }
let searchTimer = null; let searchSeq = 0;
$('#search-q').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });
async function runSearch() {
  const q = $('#search-q').value.trim();
  const out = $('#search-results');
  const seq = ++searchSeq;
  if (!q) {
    out.innerHTML = `<div class="hint search-help">Search every note's text. Combine:<br>
      <code>meeting budi</code> both words · <code>"exact phrase"</code> · <code>-draft</code> leave out<br>
      <code>tag:#project</code> · <code>path:knowledge/clients</code> · <code>file:alpha</code> (note name)</div>`;
    return;
  }
  out.innerHTML = '<div class="hint">Searching…</div>';
  try {
    const data = await api('search', { q });
    if (seq !== searchSeq) return;
    const terms = (data.terms || []).filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const re = terms.length ? new RegExp(`(${terms.join('|')})`, 'gi') : null;
    const mark = (s) => (re ? esc(s).replace(re, '<mark>$1</mark>') : esc(s));
    out.innerHTML = `<div class="hint">${data.total} result${data.total === 1 ? '' : 's'}${data.total > 100 ? ' (showing 100)' : ''}</div>` + data.results.map((r) =>
      `<div class="result" data-open="${esc(r.path)}" data-line="${r.hits[0]?.line || 0}"><div class="r-title">${mark(r.path === ROOT_FILE ? 'MEMORY' : stem(r.path))}</div><div class="r-path">${esc(r.path)}</div>
        ${r.hits.map((h) => `<div class="r-hit">${mark(h.text)}</div>`).join('')}</div>`).join('');
  } catch (e) { out.innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
}
$('#search-results').addEventListener('click', (e) => {
  const r = e.target.closest('[data-open]');
  if (r) { openPath(r.dataset.open, { newTab: mod(e), line: Number(r.dataset.line || 0) }); if (small()) setPane('left', false); }
});

function renderTags() {
  const tags = [...S.tags.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  $('#tag-list').innerHTML = tags.length ? tags.map(([t, list]) => `<button class="tag-chip" data-tag="${esc(t)}">#${esc(t)} <small>${list.length}</small></button>`).join('')
    : '<div class="hint">No tags yet. Add <code>#tags</code> in a note or <code>tags:</code> in its properties.</div>';
}
$('#tag-list').addEventListener('click', (e) => { const t = e.target.closest('[data-tag]'); if (t) searchFor(`tag:#${t.dataset.tag}`); });

// Graph view
// Docked on the right (the default layout): Topologies of Thoughts around the open note,
// or the classic graph (this note's neighbourhood or the whole vault).
const dock = { sim: null, key: '', topo: null, topoKey: '' };
function stopDock() {
  if (dock.sim) { dock.sim.stop(); dock.sim = null; }
  dock.topo?.destroy(); dock.topo = null; dock.topoKey = '';
  dock.key = '';
}
function topoData() {
  const { nodes, links } = graphData({ center: null }, { ...S.graph, filter: '', orphans: true, tags: false, unresolved: false });
  return {
    nodes: nodes.map((n) => ({ id: n.id, label: n.label, group: n.group, deg: n.deg, mtime: S.notes.get(n.id)?.mtime || 0 })),
    links: links.map((l) => ({ source: l.source, target: l.target })),
  };
}
function renderDockGraph(el, tab) {
  const view = ui.dockView === 'graph' ? 'graph' : 'topo';
  const note = tab?.kind === 'note' ? tab.path : null;
  let linkCount = 0;
  for (const set of S.out.values()) linkCount += set.size;
  const dataKey = `${S.agent}|${S.notes.size}|${linkCount}`;
  const head = `<div class="dock-graph-head">
      <div class="seg"><button class="seg-btn" data-dock="topo" aria-pressed="${view === 'topo'}">Topologies</button><button class="seg-btn" data-dock="graph" aria-pressed="${view === 'graph'}">Graph</button></div>
      ${view === 'graph' ? `<div class="seg"><button class="seg-btn" data-dock="note" aria-pressed="${!ui.dockWhole}">This note</button><button class="seg-btn" data-dock="whole" aria-pressed="${Boolean(ui.dockWhole)}">Whole vault</button></div>` : ''}
      <span class="spacer"></span>
      ${view === 'graph' ? '<button class="icon-btn" data-dock="open" title="Open as a tab"><i class="fa-solid fa-up-right-from-square"></i></button>' : ''}
      <button class="icon-btn" data-dock="full" title="Topologies of Thoughts, full screen"><i class="fa-solid fa-expand"></i></button>
    </div>`;
  const wire = () => el.querySelectorAll('[data-dock]').forEach((b) => b.addEventListener('click', () => {
    const what = b.dataset.dock;
    if (what === 'full') return openTopology(note);
    if (what === 'open') return openGraph(ui.dockWhole ? null : note);
    if (what === 'topo' || what === 'graph') ui.dockView = what; else ui.dockWhole = what === 'whole';
    saveState(); stopDock(); renderRight();
  }));
  if (view === 'topo') {
    // Same notes as last time: keep the field and move its centre to the open note.
    if (dock.topo && dock.topoKey === dataKey && el.querySelector('.dock-topo')) { if (note) dock.topo.focusOn(note); return; }
    stopDock();
    el.innerHTML = `${head}<div class="dock-topo"></div>`;
    wire();
    if (!window.LrmTopology || !window.d3) { el.querySelector('.dock-topo').innerHTML = '<div class="hint">Topologies did not load.</div>'; return; }
    dock.topoKey = dataKey;
    const host = el.querySelector('.dock-topo');
    requestAnimationFrame(() => {
      if (!host.isConnected) return;
      dock.topo = window.LrmTopology.open(host, {
        ...topoData(), embedded: true, focus: note, mode: ui.topoMode || 'centralized',
        dark: document.documentElement.classList.contains('dark'),
        onMode: (m) => { ui.topoMode = m; saveState(); },
        onOpen: (id) => openPath(id),
        onExpand: (id) => openTopology(id),
      });
    });
    return;
  }
  const center = !ui.dockWhole && note ? note : null;
  const key = `${center}|${dataKey}|${ui.dockWhole ? 1 : 0}`;
  if (dock.key === key && el.querySelector('.dock-graph-wrap')) return;
  stopDock();
  dock.key = key;
  el.innerHTML = `${head}<div class="graph-wrap dock-graph-wrap"><svg role="img" aria-label="Graph"></svg><div class="graph-legend"></div></div>`;
  wire();
  requestAnimationFrame(() => drawGraph({ center }, el.querySelector('.dock-graph-wrap'),
    { settings: { ...S.graph, depth: 2, filter: '', orphans: true }, keep: (sim) => { dock.sim = sim; }, sameTab: true }));
}

// "Set up my vault": the numbered folder structure (Johnny.Decimal-style, as in the
// reference setup) plus a Home note. Only adds what is missing; nothing is moved.
const VAULT_FOLDERS = ['00 Inbox', '10 Wiki', '12 Notes', '14 References', '14 References/Articles', '14 References/Books',
  '14 References/Courses', '14 References/Movies and TV Shows', '14 References/Websites', '15 Courses', '20 Projects', '30 Areas', '90 Archive'];
const HOME_NOTE = `---
tags: [home]
---
Start here. New ideas go to [[00 Inbox]] first; sort them when you have a moment.

## Folders
- **00 Inbox**: quick captures, WhatsApp notes, anything not sorted yet
- **10 Wiki**: lasting notes about a topic, one idea per note, linked with [[wikilinks]]
- **12 Notes**: working notes, meetings, drafts
- **14 References**: what you read and watch (Articles, Books, Courses, Movies and TV Shows, Websites)
- **15 Courses**: what you are learning
- **20 Projects** / **30 Areas**: work with a deadline / ongoing responsibilities
- **90 Archive**: finished things

## Shortcuts
- Today's daily note: the calendar button at the top
- See how everything connects: the graph on the right, or Topologies of Thoughts (◎)
- Every agent can read this vault; ask your agent to "add a note to my inbox"
`;
async function setupVault() {
  const missing = VAULT_FOLDERS.filter((f) => !S.dirs.has(`knowledge/${f}`));
  const homeMissing = !S.lc.has('knowledge/home.md');
  if (!missing.length && !homeMissing) { toast('Your vault already has this structure.'); return openPath('knowledge/Home.md'); }
  const ok = await ask({ title: 'Set up my vault', ok: 'Set up',
    text: `Adds ${missing.length} folder${missing.length === 1 ? '' : 's'}${homeMissing ? ' and a Home note' : ''} to your vault: ${missing.map((f) => esc(f)).join(', ')}. Nothing you have is moved or changed.` });
  if (!ok) return;
  try {
    for (const f of missing) await post('create', { path: `knowledge/${f}`, kind: 'folder' }).catch(() => {});
    if (homeMissing) await post('create', { path: 'knowledge/Home.md', content: HOME_NOTE }).catch(() => {});
    await loadIndex({ quiet: true });
    S.expanded.add('knowledge'); saveState(); renderTree();
    openPath('knowledge/Home.md');
    toast('Your vault is set up');
  } catch (e) { fail(e); }
}

// Topologies of Thoughts: the full-screen view in topology.js (mouse, touch and hand gestures).
let topo = null;
function openTopology(center = null) {
  if (!window.LrmTopology) return toast('The Topologies view did not load.');
  const focus = center || (activeTab()?.kind === 'note' ? activeTab().path : null);
  topo?.destroy();
  topo = window.LrmTopology.open(document.body, {
    ...topoData(),
    focus, dark: document.documentElement.classList.contains('dark'), mode: ui.topoMode || 'centralized',
    onMode: (m) => { ui.topoMode = m; saveState(); },
    onOpen: (id) => { topo?.destroy(); topo = null; openPath(id); },
    onClose: () => { topo = null; dock.topo?.destroy(); dock.topo = null; dock.topoKey = ''; renderRight(); },
  });
}
const PALETTE = ['#0b7a5a', '#2f6fd6', '#c2410c', '#7c3aed', '#0e7490', '#b91c1c', '#a16207', '#be185d', '#4d7c0f', '#475569'];
function groupOf(path) {
  if (path === ROOT_FILE) return 'MEMORY';
  const parts = path.split('/');
  if (parts[0] === 'memory') return 'memory';
  return parts.length > 2 ? `knowledge/${parts[1]}` : 'knowledge';
}
function openGraph(center = null) {
  const at = S.tabs.findIndex((t) => t.kind === 'graph' && (t.center || null) === center);
  if (at >= 0) return activate(at);
  const tab = newTabObj('graph', null, 'read');
  tab.center = center;
  S.tabs.splice(S.active + 1, 0, tab);
  S.active += 1;
  saveState(); renderTabs(); renderView();
}
function stopGraph() { const pane = S.panes[S.focus]; if (pane?.sim) { pane.sim.stop(); pane.sim = null; } }

function graphData(tab, g = S.graph) {
  const local = tab.center;
  let keep = new Set(S.notes.keys());
  if (local) {
    keep = new Set([local]);
    let frontier = [local];
    for (let d = 0; d < g.depth; d++) {
      const next = [];
      for (const p of frontier) for (const q of [...(S.out.get(p) || []), ...(S.back.get(p) || [])]) if (!keep.has(q)) { keep.add(q); next.push(q); }
      frontier = next;
    }
  }
  if (g.filter) { const f = g.filter.toLowerCase(); keep = new Set([...keep].filter((p) => p.toLowerCase().includes(f) || p === local)); }
  const nodes = new Map(); const links = [];
  for (const p of keep) nodes.set(p, { id: p, label: p === ROOT_FILE ? 'MEMORY' : stem(p), group: groupOf(p), kind: 'note', deg: 0 });
  for (const p of keep) {
    for (const q of S.out.get(p) || []) if (nodes.has(q)) links.push({ source: p, target: q });
    if (g.unresolved) for (const n of S.unresolved.get(p) || []) {
      const id = `?${n.toLowerCase()}`;
      if (!nodes.has(id)) nodes.set(id, { id, label: n, group: 'unresolved', kind: 'ghost', deg: 0 });
      links.push({ source: p, target: id });
    }
    if (g.tags) for (const t of S.notes.get(p)?.tags || []) {
      const id = `#${t}`;
      if (!nodes.has(id)) nodes.set(id, { id, label: `#${t}`, group: 'tag', kind: 'tag', deg: 0 });
      links.push({ source: p, target: id });
    }
  }
  for (const l of links) { nodes.get(l.source).deg++; nodes.get(l.target).deg++; }
  let list = [...nodes.values()];
  if (!g.orphans && !local) list = list.filter((n) => n.deg > 0);
  const ids = new Set(list.map((n) => n.id));
  return { nodes: list, links: links.filter((l) => ids.has(l.source) && ids.has(l.target)) };
}

function renderGraph(viewEl, tab) {
  stopGraph();
  const g = S.graph;
  viewEl.innerHTML = `<div class="graph-wrap" id="graph"><svg role="img" aria-label="Graph of linked notes"></svg>
    <div class="graph-ctrl">
      <input class="pane-input" id="g-filter" placeholder="Filter by name or folder…" value="${esc(g.filter)}">
      ${tab.center ? `<div class="muted">Around <b>${esc(stem(tab.center))}</b></div><label>Depth <input type="range" id="g-depth" min="1" max="4" value="${g.depth}"></label>` : ''}
      <label>Tags <input type="checkbox" id="g-tags" ${g.tags ? 'checked' : ''}></label>
      <label>Unresolved links <input type="checkbox" id="g-unresolved" ${g.unresolved ? 'checked' : ''}></label>
      ${tab.center ? '' : `<label>Orphans <input type="checkbox" id="g-orphans" ${g.orphans ? 'checked' : ''}></label>`}
    </div><div class="graph-legend" id="g-legend"></div></div>`;
  const ctrl = (id, key, fn = (el) => el.checked) => { const el = $(id, viewEl); if (el) el.oninput = () => { g[key] = fn(el); saveState(); drawGraph(tab); }; };
  ctrl('#g-tags', 'tags'); ctrl('#g-unresolved', 'unresolved'); ctrl('#g-orphans', 'orphans'); ctrl('#g-depth', 'depth', (el) => Number(el.value));
  const filter = $('#g-filter', viewEl);
  let ft = null;
  filter.oninput = () => { clearTimeout(ft); ft = setTimeout(() => { g.filter = filter.value.trim(); saveState(); drawGraph(tab); }, 250); };
  drawGraph(tab);
}

function drawGraph(tab, wrap = null, { settings = S.graph, keep = null, sameTab = false } = {}) {
  if (!wrap) { stopGraph(); wrap = $v('#graph'); }
  if (!wrap || !window.d3) return;
  const d3 = window.d3;
  const { nodes, links } = graphData(tab, settings);
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  const groups = [...new Set(nodes.filter((n) => n.kind === 'note').map((n) => n.group))].sort();
  const colorOf = (n) => {
    if (n.kind === 'ghost') return v('--text-3');
    if (n.kind === 'tag') return '#8b5cf6';
    if (n.group === 'MEMORY') return v('--brand');
    return PALETTE[groups.indexOf(n.group) % PALETTE.length];
  };
  const legend = wrap.querySelector('.graph-legend');
  if (legend) legend.innerHTML = groups.map((gr) => `<span><i style="background:${colorOf({ kind: 'note', group: gr })}"></i>${esc(gr === 'MEMORY' ? 'MEMORY.md' : gr)}</span>`).join('');
  const svg = d3.select(wrap).select('svg');
  svg.selectAll('*').remove();
  if (!nodes.length) {
    svg.append('text').attr('x', '50%').attr('y', '50%').attr('text-anchor', 'middle').attr('class', 'g-label').text(S.notes.size ? 'No notes match.' : 'No notes yet.');
    return;
  }
  const { width, height } = wrap.getBoundingClientRect();
  const root = svg.append('g');
  const zoom = d3.zoom().scaleExtent([0.1, 6]).on('zoom', (ev) => {
    root.attr('transform', ev.transform);
    label.attr('opacity', (d) => (ev.transform.k > 1.15 || d.deg > 6 || d.id === tab.center ? 1 : 0));
  });
  svg.call(zoom).on('dblclick.zoom', null);
  const link = root.append('g').attr('stroke', v('--line')).attr('stroke-width', 1).selectAll('line').data(links).join('line');
  const radius = (d) => (d.kind === 'note' ? 4 + Math.sqrt(d.deg) * 1.8 : 3.5);
  const node = root.append('g').selectAll('circle').data(nodes).join('circle')
    .attr('r', radius).attr('fill', (d) => (d.kind === 'ghost' ? 'none' : colorOf(d)))
    .attr('stroke', (d) => (d.id === tab.center ? v('--text') : d.kind === 'ghost' ? colorOf(d) : v('--surface'))).attr('stroke-width', (d) => (d.id === tab.center ? 2.5 : 1.2))
    .style('cursor', (d) => (d.kind === 'note' || d.kind === 'tag' || d.kind === 'ghost' ? 'pointer' : 'default'));
  node.append('title').text((d) => d.id.replace(/^\?/, ''));
  const label = root.append('g').selectAll('text').data(nodes).join('text').attr('class', 'g-label').attr('text-anchor', 'middle').attr('dy', (d) => -radius(d) - 4)
    .text((d) => d.label).attr('opacity', (d) => (d.deg > 6 || d.id === tab.center || nodes.length < 40 ? 1 : 0));
  const neighbours = new Map(nodes.map((n) => [n.id, new Set([n.id])]));
  links.forEach((l) => { neighbours.get(l.source)?.add(l.target); neighbours.get(l.target)?.add(l.source); });
  node.on('mouseenter', (ev, d) => {
    const near = neighbours.get(d.id);
    node.attr('opacity', (n) => (near.has(n.id) ? 1 : 0.15));
    label.attr('opacity', (n) => (near.has(n.id) ? 1 : 0.08));
    link.attr('stroke', (l) => (l.source.id === d.id || l.target.id === d.id ? v('--brand') : v('--line'))).attr('opacity', (l) => (l.source.id === d.id || l.target.id === d.id ? 1 : 0.2));
  }).on('mouseleave', () => {
    node.attr('opacity', 1); link.attr('stroke', v('--line')).attr('opacity', 1);
    const k = d3.zoomTransform(svg.node()).k;
    label.attr('opacity', (d) => (k > 1.15 || d.deg > 6 || d.id === tab.center || nodes.length < 40 ? 1 : 0));
  }).on('click', (ev, d) => {
    if (d.kind === 'note') openPath(d.id, { newTab: !sameTab });
    else if (d.kind === 'tag') searchFor(`tag:${d.id}`);
    else createFromLink(d.label, null);
  });
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id).distance(46).strength(0.6))
    .force('charge', d3.forceManyBody().strength(nodes.length > 600 ? -40 : -110))
    .force('x', d3.forceX(width / 2).strength(0.05)).force('y', d3.forceY(height / 2).strength(0.05))
    .force('collide', d3.forceCollide().radius((d) => radius(d) + 2))
    .on('tick', () => {
      link.attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y).attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
      node.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
      label.attr('x', (d) => d.x).attr('y', (d) => d.y);
    });
  node.call(d3.drag()
    .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
    .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
  if (keep) keep(sim); else S.panes[S.focus].sim = sim;
}

// Canvas: Obsidian's JSON Canvas boards (.canvas)
// Cards (text, notes, images, links) and groups on an endless board, joined by arrows.
// Drag to move, drag the corner to resize, drag a side dot onto another card to connect,
// double-click the board for a new card, double-click a card to edit it, Delete removes.
const CANVAS_COLORS = { 1: '#e5484d', 2: '#f76b15', 3: '#e2b600', 4: '#30a46c', 5: '#05a2c2', 6: '#8e4ec6' };
const cvColor = (c) => (c ? CANVAS_COLORS[c] || c : '');
const cvId = () => [...crypto.getRandomValues(new Uint8Array(8))].map((b) => b.toString(16).padStart(2, '0')).join('');
const noteCache = new Map();

async function newBoard(kind, folder = S.folder) {
  if (!folder || folder === ROOT_FILE || !(SECTIONS.includes(folder) || S.dirs.has(folder))) folder = 'knowledge';
  const label = kind === 'canvas' ? 'canvas' : 'table view';
  const name = await ask({ title: `New ${label}`, text: `In <b>${esc(folder)}</b>`, value: kind === 'canvas' ? 'Untitled canvas' : 'Untitled table', ok: 'Create' });
  if (!name) return;
  const ext = kind === 'canvas' ? '.canvas' : '.base';
  const path = uniquePath(folder, name.replace(/[\\/<>:"|?*#^[\]]/g, ' ').trim() || 'Untitled', ext);
  const content = kind === 'canvas' ? JSON.stringify({ nodes: [], edges: [] }, null, '\t')
    : window.jsyaml.dump({ filters: { and: [`file.inFolder("${folder}")`] }, views: [{ type: 'table', name: 'Table', order: ['file.name', 'tags', 'file.mtime'] }] });
  try {
    await post('create', { path, content });
    await loadIndex({ quiet: true });
    openPath(path);
  } catch (e) { fail(e); }
}

function boardHead(tab, extra = '') {
  return `<div class="note-head"><div class="crumbs">${crumbs(tab.path)}</div>${extra}
    <button class="icon-btn" data-act="bookmark" title="Bookmark"><i class="fa-${S.bookmarks.includes(tab.path) ? 'solid' : 'regular'} fa-star"></i></button>
    <button class="icon-btn hide-sm" data-act="split" title="${S.panes.length > 1 ? 'Close the split' : 'Open beside (split view)'}"><i class="fa-solid ${S.panes.length > 1 ? 'fa-window-maximize' : 'fa-table-columns'}"></i></button>
    <button class="icon-btn" data-act="more" title="More"><i class="fa-solid fa-ellipsis-vertical"></i></button></div>`;
}

function renderCanvas(view, tab) {
  let data;
  try { data = JSON.parse(tab.content || '{}'); } catch { view.innerHTML = `${boardHead(tab)}<div class="doc"><div class="banner warn">This canvas file is damaged and cannot be shown.</div></div>`; return; }
  data.nodes = Array.isArray(data.nodes) ? data.nodes : []; data.edges = Array.isArray(data.edges) ? data.edges : [];
  tab.cv ||= { x: 0, y: 0, z: 1, fit: true };
  const tools = `<div class="cv-tools">
    <button class="icon-btn" data-cv="card" title="Add a card (or double-click the board)"><i class="fa-regular fa-note-sticky"></i></button>
    <button class="icon-btn" data-cv="note" title="Add a note from the vault"><i class="fa-regular fa-file-lines"></i></button>
    <button class="icon-btn" data-cv="link" title="Add a web link"><i class="fa-solid fa-link"></i></button>
    <button class="icon-btn" data-cv="group" title="Add a group"><i class="fa-regular fa-square"></i></button>
    <span class="cv-sep"></span>
    <button class="icon-btn" data-cv="out" title="Zoom out"><i class="fa-solid fa-minus"></i></button>
    <button class="icon-btn" data-cv="in" title="Zoom in"><i class="fa-solid fa-plus"></i></button>
    <button class="icon-btn" data-cv="fit" title="Zoom to fit"><i class="fa-solid fa-expand"></i></button></div>`;
  view.innerHTML = `${boardHead(tab)}<div class="cv" id="cv" tabindex="0"><div class="cv-world" id="cv-world"><svg class="cv-edges" id="cv-edges"><defs>
    <marker id="cv-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker></defs></svg></div>
    ${tools}<div class="cv-hint">${data.nodes.length ? '' : 'Double-click to add a card. Drag a card\'s side dot onto another card to connect them.'}</div></div>`;
  const root = $v('#cv'); const world = $v('#cv-world'); const svg = $v('#cv-edges');
  let sel = null; // {type: 'node'|'edge', id}
  let saveT = null;
  const save = () => {
    clearTimeout(saveT);
    saveT = setTimeout(() => { tab.content = JSON.stringify(data, null, '\t'); renderTabs(); scheduleSave(tab); }, 400);
  };
  const byId = (id) => data.nodes.find((n) => n.id === id);
  const applyView = () => { world.style.transform = `translate(${tab.cv.x}px, ${tab.cv.y}px) scale(${tab.cv.z})`; root.style.setProperty('--cvz', tab.cv.z); };
  const toWorld = (cx, cy) => { const r = root.getBoundingClientRect(); return { x: (cx - r.left - tab.cv.x) / tab.cv.z, y: (cy - r.top - tab.cv.y) / tab.cv.z }; };
  const fit = () => {
    if (!data.nodes.length) { tab.cv = { x: root.clientWidth / 2, y: root.clientHeight / 2, z: 1 }; applyView(); return; }
    const xs = data.nodes.flatMap((n) => [n.x, n.x + n.width]); const ys = data.nodes.flatMap((n) => [n.y, n.y + n.height]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const z = Math.min(1.2, Math.max(0.1, Math.min((root.clientWidth - 80) / (x1 - x0 || 1), (root.clientHeight - 100) / (y1 - y0 || 1))));
    tab.cv = { z, x: (root.clientWidth - (x1 - x0) * z) / 2 - x0 * z, y: (root.clientHeight - (y1 - y0) * z) / 2 - y0 * z + 20 };
    applyView();
  };

  const side = (n, s) => ({ top: [n.x + n.width / 2, n.y], bottom: [n.x + n.width / 2, n.y + n.height], left: [n.x, n.y + n.height / 2], right: [n.x + n.width, n.y + n.height / 2] }[s]);
  const nearestSide = (n, x, y) => ['top', 'bottom', 'left', 'right'].map((s) => [s, Math.hypot(side(n, s)[0] - x, side(n, s)[1] - y)]).sort((a, b) => a[1] - b[1])[0][0];
  const autoSides = (a, b) => {
    const dx = (b.x + b.width / 2) - (a.x + a.width / 2); const dy = (b.y + b.height / 2) - (a.y + a.height / 2);
    return Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? ['right', 'left'] : ['left', 'right']) : (dy > 0 ? ['bottom', 'top'] : ['top', 'bottom']);
  };
  const normal = { top: [0, -1], bottom: [0, 1], left: [-1, 0], right: [1, 0] };
  const curve = ([x1, y1], s1, [x2, y2], s2) => {
    const d = Math.max(40, Math.hypot(x2 - x1, y2 - y1) / 3);
    return `M${x1},${y1} C${x1 + normal[s1][0] * d},${y1 + normal[s1][1] * d} ${x2 + normal[s2][0] * d},${y2 + normal[s2][1] * d} ${x2},${y2}`;
  };

  function drawEdges() {
    const parts = [svg.querySelector('defs').outerHTML];
    for (const e of data.edges) {
      const a = byId(e.fromNode); const b = byId(e.toNode);
      if (!a || !b) continue;
      const [sa, sb] = autoSides(a, b);
      const s1 = e.fromSide || sa; const s2 = e.toSide || sb;
      const path = curve(side(a, s1), s1, side(b, s2), s2);
      const color = cvColor(e.color) || 'var(--text-3)';
      const chosen = sel?.type === 'edge' && sel.id === e.id;
      const start = e.fromEnd === 'arrow' ? ' marker-start="url(#cv-arrow)"' : '';
      const end = e.toEnd === 'none' ? '' : ' marker-end="url(#cv-arrow)"';
      parts.push(`<path d="${path}" class="cv-edge-hit" data-edge="${esc(e.id)}"/><path d="${path}" class="cv-edge${chosen ? ' sel' : ''}" stroke="${esc(color)}"${start}${end}/>`);
      if (e.label) {
        const p1 = side(a, s1); const p2 = side(b, s2);
        parts.push(`<foreignObject x="${(p1[0] + p2[0]) / 2 - 80}" y="${(p1[1] + p2[1]) / 2 - 14}" width="160" height="28"><div class="cv-edge-label" data-edge="${esc(e.id)}">${esc(e.label)}</div></foreignObject>`);
      }
    }
    svg.innerHTML = parts.join('');
  }

  function cardBody(n) {
    if (n.type === 'text') return `<div class="md cv-md">${renderMarkdown(n.text || '', tab.path).html || '<p class="muted">Empty card. Double-click to write.</p>'}</div>`;
    if (n.type === 'link') {
      let host = n.url; try { host = new URL(n.url).hostname; } catch { /* as typed */ }
      return `<a class="cv-link" href="${esc(n.url)}" target="_blank" rel="noopener noreferrer"><i class="fa-solid fa-globe"></i> ${esc(host)}<small>${esc(n.url)}</small></a>`;
    }
    if (n.type === 'file') {
      const target = resolveLink(n.file, tab.path) || (S.lc.get(String(n.file).toLowerCase()) || '');
      if (!target) return `<div class="cv-missing"><i class="fa-solid fa-triangle-exclamation"></i> ${esc(n.file)} is missing</div>`;
      if (isImage(target)) return `<img class="cv-img" src="${esc(fileUrl(target))}" alt="${esc(basename(target))}" draggable="false">`;
      if (/\.pdf$/i.test(target)) return `<div class="cv-file-title" data-open="${esc(target)}"><i class="fa-regular fa-file-pdf"></i> ${esc(basename(target))}</div>`;
      return `<div class="cv-file-title" data-open="${esc(target)}"><i class="fa-regular fa-file-lines"></i> ${esc(stem(target))}</div><div class="md cv-md" data-note="${esc(target)}"></div>`;
    }
    return '';
  }

  function drawNodes() {
    world.querySelectorAll('.cv-node').forEach((el) => el.remove());
    const ordered = [...data.nodes.filter((n) => n.type === 'group'), ...data.nodes.filter((n) => n.type !== 'group')];
    for (const n of ordered) {
      const el = document.createElement('div');
      el.className = `cv-node cv-${n.type}${sel?.type === 'node' && sel.id === n.id ? ' sel' : ''}`;
      el.dataset.node = n.id;
      Object.assign(el.style, { left: `${n.x}px`, top: `${n.y}px`, width: `${n.width}px`, height: `${n.height}px` });
      const color = cvColor(n.color);
      if (color) el.style.setProperty('--cv-c', color);
      el.innerHTML = (n.type === 'group' ? `<div class="cv-group-label">${esc(n.label || 'Group')}</div>` : `<div class="cv-body">${cardBody(n)}</div>`)
        + ['top', 'right', 'bottom', 'left'].map((s) => `<span class="cv-dot cv-dot-${s}" data-side="${s}"></span>`).join('')
        + '<span class="cv-resize" data-resize="1"></span>';
      world.appendChild(el);
    }
    world.querySelectorAll('.cv-md').forEach((el) => { highlightCode(el); fillRich(el); });
    world.querySelectorAll('[data-note]').forEach(async (el) => {
      const path = el.dataset.note;
      const entry = S.notes.get(path);
      const key = `${path}@${entry?.mtime}`;
      try {
        if (!noteCache.has(key)) noteCache.set(key, (await api('read', { path })).content);
        el.innerHTML = renderMarkdown(noteCache.get(key), path).html;
        highlightCode(el); fillRich(el);
      } catch { el.textContent = ''; }
    });
    drawEdges();
    drawToolbar();
  }

  function drawToolbar() {
    root.querySelector('.cv-node-bar')?.remove();
    if (sel?.type !== 'node' || !tab.editable) return;
    const n = byId(sel.id); if (!n) return;
    const bar = document.createElement('div');
    bar.className = 'cv-node-bar';
    bar.innerHTML = Object.keys(CANVAS_COLORS).map((c) => `<button data-color="${c}" style="background:${CANVAS_COLORS[c]}" title="Colour"></button>`).join('')
      + '<button data-color="" title="No colour" class="cv-nocolor"><i class="fa-solid fa-ban"></i></button>'
      + (n.type === 'group' ? '<button data-cv="label" title="Rename group"><i class="fa-solid fa-i-cursor"></i></button>' : '')
      + (n.type === 'file' ? '<button data-cv="open" title="Open the note"><i class="fa-solid fa-arrow-up-right-from-square"></i></button>' : '')
      + '<button data-cv="delete" title="Delete (Del)"><i class="fa-regular fa-trash-can"></i></button>';
    root.appendChild(bar);
    const r = world.querySelector(`[data-node="${CSS.escape(n.id)}"]`)?.getBoundingClientRect(); const rr = root.getBoundingClientRect();
    if (r) { bar.style.left = `${Math.max(8, r.left - rr.left)}px`; bar.style.top = `${Math.max(8, r.top - rr.top - 44)}px`; }
  }

  const addNode = (node) => { data.nodes.push({ id: cvId(), ...node }); sel = { type: 'node', id: data.nodes[data.nodes.length - 1].id }; drawNodes(); save(); return data.nodes[data.nodes.length - 1]; };
  const center = () => { const r = root.getBoundingClientRect(); return toWorld(r.left + r.width / 2, r.top + r.height / 2); };
  const remove = () => {
    if (!sel || !tab.editable) return;
    if (sel.type === 'node') {
      data.nodes = data.nodes.filter((n) => n.id !== sel.id);
      data.edges = data.edges.filter((e) => e.fromNode !== sel.id && e.toNode !== sel.id);
    } else data.edges = data.edges.filter((e) => e.id !== sel.id);
    sel = null; drawNodes(); save();
  };

  function editText(n) {
    if (!tab.editable) return;
    const el = world.querySelector(`[data-node="${CSS.escape(n.id)}"] .cv-body`);
    if (!el) return;
    if (n.type === 'group') { renameGroup(n); return; }
    if (n.type !== 'text') return;
    el.innerHTML = '<textarea class="cv-edit" spellcheck="true"></textarea>';
    const ta = el.querySelector('textarea');
    ta.value = n.text || '';
    ta.focus();
    tab.cvBusy = true;
    ta.addEventListener('keydown', (e) => { e.stopPropagation(); if (e.key === 'Escape') ta.blur(); });
    ta.addEventListener('blur', () => { tab.cvBusy = false; if (ta.value !== (n.text || '')) { n.text = ta.value; save(); } drawNodes(); });
  }
  async function renameGroup(n) {
    const label = await ask({ title: 'Group name', value: n.label || '', ok: 'Save' });
    if (label != null) { n.label = label; drawNodes(); save(); }
  }

  // Pointer interactions
  let drag = null;
  root.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.cv-tools, .cv-node-bar, textarea, a, [data-open]')) return;
    root.focus({ preventScroll: true });
    const p = toWorld(e.clientX, e.clientY);
    const nodeEl = e.target.closest('.cv-node');
    const edgeEl = e.target.closest('[data-edge]');
    if (edgeEl && !nodeEl) { sel = { type: 'edge', id: edgeEl.dataset.edge }; drawNodes(); return; }
    if (!nodeEl) { sel = null; drawNodes(); drag = { kind: 'pan', sx: e.clientX, sy: e.clientY, ox: tab.cv.x, oy: tab.cv.y }; root.setPointerCapture(e.pointerId); return; }
    const n = byId(nodeEl.dataset.node);
    if (!n) return;
    if (sel?.id !== n.id) { sel = { type: 'node', id: n.id }; drawNodes(); }
    if (!tab.editable) return;
    if (e.target.dataset.side) {
      drag = { kind: 'link', from: n, side: e.target.dataset.side, temp: null };
    } else if (e.target.dataset.resize) {
      drag = { kind: 'resize', n, sx: p.x, sy: p.y, w: n.width, h: n.height };
    } else {
      const inside = n.type === 'group' ? data.nodes.filter((m) => m !== n && m.x >= n.x && m.y >= n.y && m.x + m.width <= n.x + n.width && m.y + m.height <= n.y + n.height) : [];
      drag = { kind: 'move', n, sx: p.x, sy: p.y, ox: n.x, oy: n.y, inside: inside.map((m) => [m, m.x, m.y]), moved: false };
    }
    tab.cvBusy = true;
    root.setPointerCapture(e.pointerId);
  });
  root.addEventListener('pointermove', (e) => {
    if (!drag) return;
    if (drag.kind === 'pan') { tab.cv.x = drag.ox + e.clientX - drag.sx; tab.cv.y = drag.oy + e.clientY - drag.sy; applyView(); drawToolbar(); return; }
    const p = toWorld(e.clientX, e.clientY);
    const snap = (v) => Math.round(v / 10) * 10;
    if (drag.kind === 'move') {
      const dx = p.x - drag.sx; const dy = p.y - drag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
      drag.n.x = snap(drag.ox + dx); drag.n.y = snap(drag.oy + dy);
      for (const [m, mx, my] of drag.inside) { m.x = snap(mx + dx); m.y = snap(my + dy); }
      for (const m of [drag.n, ...drag.inside.map((x) => x[0])]) {
        const el = world.querySelector(`[data-node="${CSS.escape(m.id)}"]`);
        if (el) { el.style.left = `${m.x}px`; el.style.top = `${m.y}px`; }
      }
      drawEdges(); drawToolbar();
    } else if (drag.kind === 'resize') {
      drag.n.width = Math.max(80, snap(drag.w + p.x - drag.sx)); drag.n.height = Math.max(40, snap(drag.h + p.y - drag.sy));
      const el = world.querySelector(`[data-node="${CSS.escape(drag.n.id)}"]`);
      if (el) { el.style.width = `${drag.n.width}px`; el.style.height = `${drag.n.height}px`; }
      drawEdges();
    } else if (drag.kind === 'link') {
      drawEdges();
      const [x1, y1] = side(drag.from, drag.side);
      svg.insertAdjacentHTML('beforeend', `<path d="${curve([x1, y1], drag.side, [p.x, p.y], 'top')}" class="cv-edge cv-edge-temp" marker-end="url(#cv-arrow)"/>`);
    }
  });
  root.addEventListener('pointerup', (e) => {
    const d = drag; drag = null; tab.cvBusy = false;
    if (!d) return;
    if (d.kind === 'link') {
      const target = document.elementsFromPoint(e.clientX, e.clientY).map((el) => el.closest?.('.cv-node')).find(Boolean);
      const to = target && byId(target.dataset.node);
      if (to && to.id !== d.from.id) {
        const p = toWorld(e.clientX, e.clientY);
        data.edges.push({ id: cvId(), fromNode: d.from.id, fromSide: d.side, toNode: to.id, toSide: nearestSide(to, p.x, p.y) });
        save();
      }
      drawEdges();
    } else if (d.kind === 'move' && d.moved) save();
    else if (d.kind === 'resize') { drawNodes(); save(); }
  });
  root.addEventListener('wheel', (e) => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      const r = root.getBoundingClientRect(); const mx = e.clientX - r.left; const my = e.clientY - r.top;
      const z = Math.min(3, Math.max(0.1, tab.cv.z * Math.exp(-e.deltaY / 400)));
      tab.cv.x = mx - (mx - tab.cv.x) * (z / tab.cv.z); tab.cv.y = my - (my - tab.cv.y) * (z / tab.cv.z); tab.cv.z = z;
    } else { tab.cv.x -= e.deltaX; tab.cv.y -= e.deltaY; }
    applyView(); drawToolbar();
  }, { passive: false });
  root.addEventListener('dblclick', (e) => {
    if (e.target.closest('.cv-tools, .cv-node-bar, textarea')) return;
    const edgeEl = e.target.closest('[data-edge]');
    const nodeEl = e.target.closest('.cv-node');
    if (edgeEl && !nodeEl && tab.editable) {
      const edge = data.edges.find((x) => x.id === edgeEl.dataset.edge);
      ask({ title: 'Arrow label', value: edge.label || '', ok: 'Save' }).then((v) => { if (v != null) { edge.label = v; drawEdges(); save(); } });
      return;
    }
    if (nodeEl) { editText(byId(nodeEl.dataset.node)); return; }
    if (!tab.editable) return;
    const p = toWorld(e.clientX, e.clientY);
    const n = addNode({ type: 'text', text: '', x: Math.round(p.x - 130), y: Math.round(p.y - 30), width: 260, height: 120 });
    setTimeout(() => editText(n), 30);
  });
  root.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
    if ((e.key === 'Delete' || e.key === 'Backspace') && sel) { e.preventDefault(); remove(); }
    if (e.key === 'Enter' && sel?.type === 'node') { e.preventDefault(); editText(byId(sel.id)); }
  });
  root.addEventListener('click', async (e) => {
    // Links and tags inside cards are handled by the page; everything else is the board's.
    if (!e.target.closest('a.internal, [data-tag]')) e.stopPropagation();
    const open = e.target.closest('[data-open]');
    if (open) { e.preventDefault(); openPath(open.dataset.open, { newTab: true }); return; }
    const color = e.target.closest('[data-color]');
    if (color && sel?.type === 'node') { const n = byId(sel.id); if (color.dataset.color) n.color = color.dataset.color; else delete n.color; drawNodes(); save(); return; }
    const b = e.target.closest('[data-cv]');
    if (!b) return;
    const act = b.dataset.cv;
    if (act === 'fit') return fit();
    if (act === 'in' || act === 'out') {
      const z = Math.min(3, Math.max(0.1, tab.cv.z * (act === 'in' ? 1.25 : 0.8)));
      const cx = root.clientWidth / 2; const cy = root.clientHeight / 2;
      tab.cv.x = cx - (cx - tab.cv.x) * (z / tab.cv.z); tab.cv.y = cy - (cy - tab.cv.y) * (z / tab.cv.z); tab.cv.z = z;
      return applyView();
    }
    if (act === 'delete') return remove();
    if (act === 'label') return renameGroup(byId(sel.id));
    if (act === 'open') { const n = byId(sel.id); const t = resolveLink(n.file, tab.path) || n.file; return openPath(t, { newTab: true }); }
    if (!tab.editable) return;
    const c = center();
    if (act === 'card') { const n = addNode({ type: 'text', text: '', x: Math.round(c.x - 130), y: Math.round(c.y - 60), width: 260, height: 120 }); setTimeout(() => editText(n), 30); }
    if (act === 'group') addNode({ type: 'group', label: 'Group', x: Math.round(c.x - 250), y: Math.round(c.y - 160), width: 500, height: 320 });
    if (act === 'link') {
      const url = await ask({ title: 'Add a web link', value: 'https://', ok: 'Add' });
      if (url && /^https?:\/\/\S+$/.test(url)) addNode({ type: 'link', url, x: Math.round(c.x - 200), y: Math.round(c.y - 140), width: 400, height: 280 });
    }
    if (act === 'note') {
      picker({
        placeholder: 'Add a note or image to the board…', footer: '<span><kbd>↵</kbd> add</span>',
        items: (q) => [...S.lc.values()].filter((p) => !isCanvas(p)).map((p) => ({ path: p, score: fuzzy(q, p), html: esc(isNote(p) ? stem(p) : basename(p)), sub: dirname(p) }))
          .filter((x) => x.score > 0).sort((a, b) => b.score - a.score).slice(0, 40),
        onPick: (it) => addNode({ type: 'file', file: it.path, x: Math.round(c.x - 200), y: Math.round(c.y - 150), width: 400, height: isImage(it.path) ? 300 : 400 }),
      });
    }
  });

  applyView();
  drawNodes();
  if (tab.cv.fit) { tab.cv.fit = false; requestAnimationFrame(fit); }
}

// Bases: table views of notes by their properties (.base, Obsidian's format)
// Supported filters: file.inFolder("x"), file.hasTag("a"), file.hasLink("x"), file.hasProperty("p"),
// prop.contains("x"), prop.isEmpty(), comparisons (prop == "x", file.name != "y", rating >= 3), with
// and / or / not groups. Anything else is skipped and named above the table.
const FILE_COLS = { 'file.name': 'Name', 'file.folder': 'Folder', 'file.mtime': 'Modified', 'file.size': 'Size', 'file.tags': 'Tags', 'file.links': 'Links', 'file.path': 'Path' };

function baseRow(e) {
  return { path: e.path, name: stem(e.path), folder: dirname(e.path), ext: 'md', mtime: e.mtime, size: e.size,
    tags: e.tags || [], links: [...(S.out.get(e.path) || [])], props: e.props || {} };
}
function baseValue(row, key) {
  key = String(key).replace(/^note\./, '');
  if (key === 'file.name') return row.name;
  if (key === 'file.folder') return row.folder;
  if (key === 'file.path') return row.path;
  if (key === 'file.ext') return row.ext;
  if (key === 'file.mtime') return row.mtime * 1000;
  if (key === 'file.size') return row.size;
  if (key === 'file.tags') return row.tags;
  if (key === 'file.links') return row.links.length;
  if (key === 'tags' && !(key in row.props)) return row.tags;
  return row.props[key];
}
function literal(text) {
  const t = String(text).trim();
  if (/^(["']).*\1$/.test(t)) return t.slice(1, -1);
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
  if (t === 'true' || t === 'false') return t === 'true';
  if (t === 'null') return null;
  const d = /^date\((["'])(.+)\1\)$/.exec(t);
  if (d) return new Date(d[2]).getTime();
  return undefined;
}
function compare(a, op, b) {
  if (Array.isArray(a)) return op === '!=' ? !a.map(String).includes(String(b)) : op === '==' ? a.map(String).includes(String(b)) : false;
  if (typeof b === 'number' && typeof a === 'string' && a !== '' && !Number.isNaN(Number(a))) a = Number(a);
  if (typeof b === 'number' && typeof a === 'string' && !Number.isNaN(Date.parse(a))) a = Date.parse(a);
  switch (op) {
    case '==': return a === b || String(a ?? '') === String(b ?? '');
    case '!=': return !(a === b || String(a ?? '') === String(b ?? ''));
    case '>': return a > b; case '>=': return a >= b; case '<': return a < b; case '<=': return a <= b;
    default: return false;
  }
}
function evalFilter(f, row, skipped) {
  if (f == null) return true;
  if (Array.isArray(f)) return f.every((x) => evalFilter(x, row, skipped));
  if (typeof f === 'object') {
    if (f.and) return (f.and || []).every((x) => evalFilter(x, row, skipped));
    if (f.or) return (f.or || []).some((x) => evalFilter(x, row, skipped));
    if (f.not) return !(f.not || []).some((x) => evalFilter(x, row, skipped));
    return true;
  }
  let expr = String(f).trim(); let negate = false;
  if (expr.startsWith('!') && !expr.startsWith('!=')) { negate = true; expr = expr.slice(1).trim(); }
  const result = evalExpr(expr, row, skipped);
  return negate ? !result : result;
}
function evalExpr(expr, row, skipped) {
  const args = (s) => [...s.matchAll(/(["'])(.*?)\1/g)].map((m) => m[2]);
  let m;
  if ((m = /^file\.inFolder\((.*)\)$/.exec(expr))) { const f = args(m[1])[0] || ''; return row.folder === f || row.folder.startsWith(`${f}/`); }
  if ((m = /^file\.hasTag\((.*)\)$/.exec(expr))) { const want = args(m[1]).map((t) => t.replace(/^#/, '').toLowerCase()); return row.tags.some((t) => want.some((w) => t.toLowerCase() === w || t.toLowerCase().startsWith(`${w}/`))); }
  if ((m = /^file\.hasLink\((.*)\)$/.exec(expr))) { const target = resolveLink(args(m[1])[0] || '', row.path); return Boolean(target) && row.links.includes(target); }
  if ((m = /^file\.hasProperty\((.*)\)$/.exec(expr))) return (args(m[1])[0] || '') in row.props;
  if ((m = /^([\w.-]+)\.contains\((.*)\)$/.exec(expr))) {
    const v = baseValue(row, m[1]); const want = String(args(m[2])[0] ?? literal(m[2]) ?? '').toLowerCase();
    return Array.isArray(v) ? v.some((x) => String(x).toLowerCase() === want || String(x).toLowerCase().includes(want)) : String(v ?? '').toLowerCase().includes(want);
  }
  if ((m = /^([\w.-]+)\.isEmpty\(\)$/.exec(expr))) { const v = baseValue(row, m[1]); return v == null || v === '' || (Array.isArray(v) && !v.length); }
  if ((m = /^([\w.-]+)\s*(==|!=|>=|<=|>|<)\s*(.+)$/.exec(expr))) {
    const right = literal(m[3]);
    if (right === undefined) { skipped.add(expr); return true; }
    return compare(baseValue(row, m[1]), m[2], right);
  }
  if ((m = /^([\w.-]+)$/.exec(expr))) { const v = baseValue(row, m[1]); return Array.isArray(v) ? v.length > 0 : Boolean(v); }
  skipped.add(expr);
  return true;
}
function cellHtml(value, key) {
  if (value == null || value === '') return '<span class="muted">—</span>';
  if (key === 'file.mtime') return esc(dmy(value));
  if (key === 'file.size') return esc(value > 1024 ? `${(value / 1024).toFixed(1)} KB` : `${value} B`);
  const one = (v) => {
    const s = String(v);
    const wl = /^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$/.exec(s);
    if (wl) { const t = resolveLink(wl[1]); return `<a href="#" class="internal${t ? '' : ' unresolved'}" data-href="${esc(t || wl[1])}"${t ? '' : ' data-create="1"'}>${esc(wl[2] || wl[1])}</a>`; }
    if (key === 'tags' || key === 'file.tags') return `<span class="tag-chip" data-tag="${esc(s.replace(/^#/, ''))}">#${esc(s.replace(/^#/, ''))}</span>`;
    if (typeof v === 'boolean') return v ? '<i class="fa-solid fa-square-check" style="color:var(--brand)"></i>' : '<i class="fa-regular fa-square"></i>';
    if (/^https?:\/\//.test(s)) return `<a href="${esc(s)}" target="_blank" rel="noopener noreferrer">${esc(s.replace(/^https?:\/\//, '').slice(0, 40))}</a>`;
    return esc(s);
  };
  return Array.isArray(value) ? value.map(one).join(' ') : one(value);
}

function renderBase(view, tab) {
  let doc;
  try { doc = window.jsyaml.load(tab.content || '') || {}; } catch (e) { doc = null; }
  if (!doc || typeof doc !== 'object') {
    view.innerHTML = `${boardHead(tab)}<div class="scroller"><div class="doc"><div class="banner warn">This table view's file could not be read: fix it below.</div><textarea class="base-yaml" id="base-yaml">${esc(tab.content)}</textarea><button class="btn primary" data-base="save-yaml">Save</button></div></div>`;
    return;
  }
  doc.views = Array.isArray(doc.views) && doc.views.length ? doc.views : [{ type: 'table', name: 'Table' }];
  tab.baseView = Math.min(tab.baseView || 0, doc.views.length - 1);
  const v = doc.views[tab.baseView];
  const skipped = new Set();
  let rows = [...S.notes.values()].filter((e) => !e.path.startsWith(`${TEMPLATE_DIR}/`)).map(baseRow)
    .filter((r) => evalFilter(doc.filters, r, skipped) && evalFilter(v.filters, r, skipped));
  const allProps = [...new Set(rows.flatMap((r) => Object.keys(r.props)))].sort();
  const cols = Array.isArray(v.order) && v.order.length ? v.order : ['file.name', ...allProps.slice(0, 4)];
  const label = (c) => doc.properties?.[c]?.displayName || doc.properties?.[`note.${c}`]?.displayName || FILE_COLS[c] || c.replace(/^note\./, '');
  const sort = Array.isArray(v.sort) && v.sort[0] ? v.sort[0] : null;
  if (sort) {
    const dir = String(sort.direction || 'ASC').toUpperCase() === 'DESC' ? -1 : 1;
    rows.sort((a, b) => {
      const x = baseValue(a, sort.property); const y = baseValue(b, sort.property);
      if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * dir;
    });
  }
  const quick = (tab.baseQuick || '').toLowerCase();
  if (quick) rows = rows.filter((r) => [r.path, ...Object.values(r.props).flat().map(String), ...r.tags].join(' ').toLowerCase().includes(quick));
  const limit = Number(v.limit) > 0 ? Number(v.limit) : 1000;
  const shown = rows.slice(0, limit);
  const tabsHtml = doc.views.map((x, i) => `<button class="base-tab${i === tab.baseView ? ' on' : ''}" data-view="${i}">${esc(x.name || `View ${i + 1}`)}</button>`).join('');
  const arrow = (c) => (sort?.property === c ? (String(sort.direction).toUpperCase() === 'DESC' ? ' ↓' : ' ↑') : '');
  view.innerHTML = `${boardHead(tab)}<div class="scroller"><div class="base">
    <div class="base-bar"><div class="base-tabs">${tabsHtml}${tab.editable ? '<button class="base-tab" data-base="add-view" title="Add a view"><i class="fa-solid fa-plus"></i></button>' : ''}</div>
      <input class="pane-input base-quick" id="base-quick" placeholder="Search in this table…" value="${esc(tab.baseQuick || '')}">
      ${tab.editable ? `<button class="btn" data-base="filters"><i class="fa-solid fa-filter"></i> Filter</button><button class="btn" data-base="columns"><i class="fa-solid fa-table-columns"></i> Columns</button>
      <button class="btn" data-base="yaml" title="Edit the .base file"><i class="fa-solid fa-code"></i></button>` : ''}</div>
    ${skipped.size ? `<div class="banner info"><i class="fa-solid fa-circle-info"></i>Some filters only work in Obsidian and were skipped here: <code>${esc([...skipped].join(', '))}</code></div>` : ''}
    <div class="base-wrap"><table class="base-table"><thead><tr>${cols.map((c) => `<th data-sort="${esc(c)}">${esc(label(c))}${arrow(c)}</th>`).join('')}</tr></thead>
    <tbody>${shown.map((r) => `<tr>${cols.map((c) => (c === 'file.name'
      ? `<td><a href="#" class="internal" data-href="${esc(r.path)}">${esc(r.name)}</a></td>`
      : `<td>${cellHtml(baseValue(r, c), c)}</td>`)).join('')}</tr>`).join('') || `<tr><td colspan="${cols.length}" class="muted">No notes match.</td></tr>`}</tbody></table></div>
    <div class="muted base-count">${rows.length} note${rows.length === 1 ? '' : 's'}${rows.length > shown.length ? `, showing ${shown.length}` : ''}</div></div></div>`;

  const saveDoc = () => { tab.content = window.jsyaml.dump(doc, { lineWidth: 120 }); renderTabs(); scheduleSave(tab); renderBase(view, tab); };
  const root = view;
  root.querySelector('#base-quick')?.addEventListener('input', (e) => {
    tab.baseQuick = e.target.value; clearTimeout(tab.baseT);
    tab.baseT = setTimeout(() => { renderBase(view, tab); const q = view.querySelector('#base-quick'); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }, 250);
  });
  root.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => { tab.baseView = Number(b.dataset.view); renderBase(view, tab); }));
  root.querySelectorAll('th[data-sort]').forEach((th) => th.addEventListener('click', () => {
    if (!tab.editable) return;
    const c = th.dataset.sort;
    const cur = v.sort?.[0];
    v.sort = [{ property: c, direction: cur?.property === c && String(cur.direction).toUpperCase() === 'ASC' ? 'DESC' : 'ASC' }];
    saveDoc();
  }));
  root.querySelectorAll('[data-base]').forEach((b) => b.addEventListener('click', async () => {
    const act = b.dataset.base;
    if (act === 'add-view') {
      const name = await ask({ title: 'New view', value: `View ${doc.views.length + 1}`, ok: 'Add' });
      if (name) { doc.views.push({ type: 'table', name, order: cols }); tab.baseView = doc.views.length - 1; saveDoc(); }
    }
    if (act === 'columns') baseColumns(doc, v, cols, allProps, saveDoc);
    if (act === 'filters') baseFilters(v, saveDoc);
    if (act === 'yaml') baseYaml(tab, () => renderBase(view, tab));
    if (act === 'save-yaml') { tab.content = view.querySelector('#base-yaml').value; scheduleSave(tab); renderBase(view, tab); }
  }));
}

function baseColumns(doc, v, cols, allProps, saveDoc) {
  const options = [...new Set([...cols, 'file.name', 'file.folder', 'file.mtime', 'file.tags', 'file.links', 'file.size', ...allProps])];
  const { overlay, close } = modal(`<div class="msg"><h3>Columns</h3><div class="muted">Tick the columns to show; use the arrows to order them.</div>
    <div class="base-cols">${options.map((c) => `<div class="base-col" data-col="${esc(c)}"><label><input type="checkbox" ${cols.includes(c) ? 'checked' : ''}> ${esc(FILE_COLS[c] || c)}</label>
    <span><button class="icon-btn" data-up title="Up"><i class="fa-solid fa-arrow-up"></i></button><button class="icon-btn" data-down title="Down"><i class="fa-solid fa-arrow-down"></i></button></span></div>`).join('')}</div></div>
    <div class="btns"><button class="btn" data-a="cancel">Cancel</button><button class="btn primary" data-a="ok">Save</button></div>`);
  const list = overlay.querySelector('.base-cols');
  list.addEventListener('click', (e) => {
    const row = e.target.closest('.base-col');
    if (e.target.closest('[data-up]') && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
    if (e.target.closest('[data-down]') && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
  });
  overlay.querySelector('[data-a=cancel]').onclick = close;
  overlay.querySelector('[data-a=ok]').onclick = () => {
    v.order = [...list.querySelectorAll('.base-col')].filter((r) => r.querySelector('input').checked).map((r) => r.dataset.col);
    close(); saveDoc();
  };
}

// Filters of a view, as simple rows (folder / tag / property). Others: edit the file's YAML.
function baseFilters(v, saveDoc) {
  const parts = !v.filters ? [] : typeof v.filters === 'string' ? [v.filters] : Array.isArray(v.filters?.and) ? v.filters.and : null;
  if (parts === null || parts.some((p) => typeof p !== 'string')) { toast('These filters are too advanced for the simple editor; use the </> button to edit the file', true); return; }
  const parse = (p) => {
    let m;
    if ((m = /^file\.inFolder\((["'])(.*)\1\)$/.exec(p))) return { k: 'folder', v: m[2] };
    if ((m = /^file\.hasTag\((["'])(.*)\1\)$/.exec(p))) return { k: 'tag', v: m[2] };
    if ((m = /^([\w.-]+)\.contains\((["'])(.*)\2\)$/.exec(p))) return { k: 'prop', p: m[1], op: 'contains', v: m[3] };
    if ((m = /^([\w.-]+)\s*(==|!=|>=|<=|>|<)\s*(.+)$/.exec(p))) return { k: 'prop', p: m[1], op: m[2], v: String(literal(m[3]) ?? m[3]) };
    return { k: 'raw', v: p };
  };
  const rows = parts.map(parse);
  const folders = ['knowledge', 'memory', ...[...S.dirs].sort()];
  const rowHtml = (r) => `<div class="base-frow">
    <select data-k>${['folder', 'tag', 'prop', 'raw'].map((k) => `<option value="${k}" ${r.k === k ? 'selected' : ''}>${{ folder: 'In folder', tag: 'Has tag', prop: 'Property', raw: 'Expression' }[k]}</option>`).join('')}</select>
    <input data-p placeholder="property" value="${esc(r.p || '')}" ${r.k === 'prop' ? '' : 'hidden'}>
    <select data-op ${r.k === 'prop' ? '' : 'hidden'}>${['==', '!=', 'contains', '>', '>=', '<', '<='].map((o) => `<option ${r.op === o ? 'selected' : ''}>${o}</option>`).join('')}</select>
    <input data-v list="${r.k === 'folder' ? 'base-folders' : ''}" placeholder="value" value="${esc(r.v || '')}">
    <button class="icon-btn" data-rm title="Remove"><i class="fa-solid fa-xmark"></i></button></div>`;
  const { overlay, close } = modal(`<div class="msg"><h3>Show notes where all of these are true</h3>
    <datalist id="base-folders">${folders.map((f) => `<option value="${esc(f)}">`).join('')}</datalist>
    <div class="base-frows">${rows.map(rowHtml).join('')}</div><button class="btn" data-add><i class="fa-solid fa-plus"></i> Add condition</button></div>
    <div class="btns"><button class="btn" data-a="cancel">Cancel</button><button class="btn primary" data-a="ok">Save</button></div>`);
  const box = overlay.querySelector('.base-frows');
  overlay.querySelector('[data-add]').onclick = () => box.insertAdjacentHTML('beforeend', rowHtml({ k: 'folder', v: '' }));
  box.addEventListener('click', (e) => { if (e.target.closest('[data-rm]')) e.target.closest('.base-frow').remove(); });
  box.addEventListener('change', (e) => {
    if (!e.target.matches('[data-k]')) return;
    const row = e.target.closest('.base-frow'); const k = e.target.value;
    row.querySelector('[data-p]').hidden = k !== 'prop'; row.querySelector('[data-op]').hidden = k !== 'prop';
    row.querySelector('[data-v]').setAttribute('list', k === 'folder' ? 'base-folders' : '');
  });
  overlay.querySelector('[data-a=cancel]').onclick = close;
  overlay.querySelector('[data-a=ok]').onclick = () => {
    const q = (s) => JSON.stringify(String(s));
    const out = [...box.querySelectorAll('.base-frow')].map((row) => {
      const k = row.querySelector('[data-k]').value; const val = row.querySelector('[data-v]').value.trim();
      const prop = row.querySelector('[data-p]').value.trim(); const op = row.querySelector('[data-op]').value;
      if (!val && k !== 'prop') return null;
      if (k === 'folder') return `file.inFolder(${q(val.replace(/\/+$/, ''))})`;
      if (k === 'tag') return `file.hasTag(${q(val.replace(/^#/, ''))})`;
      if (k === 'raw') return val;
      if (!prop) return null;
      if (op === 'contains') return `${prop}.contains(${q(val)})`;
      return `${prop} ${op} ${/^-?\d+(\.\d+)?$/.test(val) || val === 'true' || val === 'false' ? val : q(val)}`;
    }).filter(Boolean);
    if (out.length) v.filters = { and: out }; else delete v.filters;
    close(); saveDoc();
  };
}

function baseYaml(tab, done) {
  const { overlay, close } = modal(`<div class="msg"><h3>Edit the table view file</h3><div class="muted">Obsidian Bases format (YAML).</div>
    <textarea class="base-yaml">${esc(tab.content)}</textarea></div>
    <div class="btns"><button class="btn" data-a="cancel">Cancel</button><button class="btn primary" data-a="ok">Save</button></div>`);
  overlay.querySelector('.modal').style.width = 'min(760px, calc(100vw - 32px))';
  overlay.querySelector('[data-a=cancel]').onclick = close;
  overlay.querySelector('[data-a=ok]').onclick = () => {
    const text = overlay.querySelector('textarea').value;
    try { window.jsyaml.load(text); } catch (e) { toast(`Not valid YAML: ${e.message.split('\n')[0]}`, true); return; }
    tab.content = text; renderTabs(); scheduleSave(tab); close(); done();
  };
}

// Quick switcher & command palette
function picker({ placeholder, items, onPick, footer }) {
  const { overlay, close } = modal(`<input class="q" placeholder="${esc(placeholder)}" spellcheck="false" autocomplete="off"><div class="list"></div><div class="foot">${footer}</div>`);
  const input = $('.q', overlay); const list = $('.list', overlay);
  let sel = 0; let current = [];
  const draw = () => {
    current = items(input.value.trim());
    sel = Math.min(sel, Math.max(0, current.length - 1));
    list.innerHTML = current.map((it, i) => `<div class="opt${i === sel ? ' sel' : ''}" data-i="${i}">${it.html}<span class="o-sub">${esc(it.sub || '')}</span></div>`).join('') || '<div class="hint">Nothing found.</div>';
  };
  const pick = (i, e) => { const it = current[i]; if (!it) return; close(); onPick(it, e); };
  input.addEventListener('input', () => { sel = 0; draw(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + current.length) % Math.max(1, current.length);
      list.querySelectorAll('.opt').forEach((o, i) => o.classList.toggle('sel', i === sel));
      list.querySelector('.sel')?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') { e.preventDefault(); pick(e.shiftKey ? current.findIndex((c) => c.create) : sel, e); }
  });
  list.addEventListener('click', (e) => { const o = e.target.closest('[data-i]'); if (o) pick(Number(o.dataset.i), e); });
  draw(); input.focus();
}

function quickSwitcher(newTab = false) {
  picker({
    placeholder: 'Find or create a note…',
    footer: `<span><kbd>↑↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span><kbd>${isMac ? '⌘' : 'Ctrl'} ↵</kbd> new tab</span><span><kbd>⇧ ↵</kbd> create</span>`,
    items: (q) => {
      const found = rankNotes(q, 40).map((it) => ({ path: it.path, html: `${esc(it.alias || it.name)}${it.alias ? ` <span class="muted">→ ${esc(it.name)}</span>` : ''}`, sub: dirname(it.path) || 'vault' }));
      for (const e of S.entries) {
        if (e.type !== 'file' || !(isCanvas(e.path) || isBase(e.path)) || !fuzzy(q, basename(e.path))) continue;
        found.push({ path: e.path, html: `<i class="fa-solid ${isCanvas(e.path) ? 'fa-object-group' : 'fa-table'}" style="opacity:.6"></i> ${esc(basename(e.path))}`, sub: dirname(e.path) });
      }
      if (q && !S.notes.has(`${q}.md`)) found.push({ create: q, html: `<i class="fa-solid fa-plus" style="color:var(--brand)"></i> Create “${esc(q)}”`, sub: q.includes('/') ? '' : `in ${S.folder && S.folder !== ROOT_FILE ? S.folder : 'knowledge'}` });
      return found;
    },
    onPick: (it, e) => {
      if (it.create) {
        let path = it.create.includes('/') ? it.create : `${S.folder && (SECTIONS.includes(S.folder) || S.dirs.has(S.folder)) ? S.folder : 'knowledge'}/${it.create}`;
        if (!SECTIONS.includes(sectionOf(path))) path = `knowledge/${path}`;
        if (!isNote(path)) path += '.md';
        createNote(path, sectionOf(path) === 'knowledge' ? `# ${stem(path)}\n\n` : '', { newTab: newTab || mod(e) });
      } else openPath(it.path, { newTab: newTab || mod(e) });
    },
  });
}

// Git: sync with a private GitHub repo, opened as a vault in Obsidian
const GIT_STATE = {
  OK: ['ok', 'Synced'], RETRY: ['busy', 'Will retry'], CONFLICT: ['bad', 'Conflict'], FAIL: ['bad', 'Problem'],
};
function gitBadge(st) {
  if (!st?.available || !st.ready) return ['', 'Not set up'];
  if (st.syncing || st.pending_sync) return ['busy', 'Syncing…'];
  if (st.pending_config) return ['busy', 'Saving…'];
  if (!st.repo) return ['', 'Off'];
  return GIT_STATE[st.sync_status] || ['busy', 'Waiting for first sync'];
}
async function gitDot() {
  try {
    const st = await api('git');
    const [cls] = gitBadge(st);
    const dot = $('#git-dot');
    dot.hidden = !st.repo;
    dot.className = `git-dot ${cls}`;
  } catch { /* ignore */ }
}

// Vault updates: what agents propose, Lorium checked, waiting for the owner
async function updatesDot() {
  try {
    const r = await fetch('/api/lrm/vault-updates', { credentials: 'same-origin' });
    const d = await r.json();
    const n = (d.pending || []).length;
    const el = $('#upd-count'); el.hidden = !n; el.textContent = n;
    return d;
  } catch { return null; }
}
function diffHtml(diff) {
  return diff.split('\n').map((l) => `<span class="${l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : ''}">${esc(l)}</span>`).join('\n');
}
async function updatesPanel() {
  const d = await updatesDot();
  if (!d) return toast('Could not load the updates.');
  const card = (u) => `<div class="upd" data-id="${esc(u.id)}">
      <h4>${esc(u.title)}</h4>
      <div class="meta">${esc(u.path)} · ${u.exists ? 'updates an existing note' : 'new note'} · from ${esc(u.agent_name)}${u.agent_name !== d.reviewer ? `, checked by ${esc(d.reviewer)}` : ''}</div>
      ${u.summary ? `<div>${esc(u.summary)}</div>` : ''}${u.review_note ? `<div class="muted">Note from ${esc(d.reviewer)}: ${esc(u.review_note)}</div>` : ''}
      ${u.sources?.length ? `<div class="muted">Sources: ${u.sources.map(esc).join(', ')}</div>` : ''}
      <pre>${u.exists && u.diff ? diffHtml(u.diff) : esc(u.content)}</pre>
      <div class="row"><button class="btn primary" data-a="approve">Approve and save</button><button class="btn" data-a="edit">Edit first</button><button class="btn danger" data-a="reject">Reject</button></div>
    </div>`;
  const reviewing = (d.reviewing || []).map((u) => `<li>${esc(u.title)} <small class="muted">from ${esc(u.agent_name)}${u.others?.length ? ' · merging several versions' : ''}</small></li>`).join('');
  const day = (t) => new Date(t * 1000).toLocaleDateString('en-GB').replace(/\//g, '-');
  const auto = (d.recent || []).filter((u) => u.auto && u.status === 'applied').map((u) => `<li data-id="${esc(u.id)}">${esc(u.title)} <small class="muted">${esc(u.summary || 'corrected')} · ${esc(u.agent_name)} · ${day(u.updated)}</small> <button class="btn" data-a="undo" style="padding:2px 8px">Undo</button></li>`).join('');
  const many = (d.pending || []).length > 1;
  const { overlay, close } = modal(`<div class="msg"><h3>Vault updates</h3>
      <div class="muted" style="margin-bottom:12px">Agents propose, ${esc(d.reviewer)} checks, you approve new notes and big changes. Small corrections ${esc(d.reviewer)} checked are saved on their own and listed below (and in an evening WhatsApp digest). Waiting updates expire after 14 days.</div>
      ${many ? '<div class="row" style="margin-bottom:10px"><button class="btn primary" data-a="approve-all">Approve all</button><button class="btn danger" data-a="reject-all">Reject all</button></div>' : ''}
      ${(d.pending || []).map(card).join('') || '<div class="muted" style="margin:10px 0 16px">Nothing is waiting for you.</div>'}
      ${reviewing ? `<div class="side-head" style="margin-top:8px">With ${esc(d.reviewer)} for checking</div><ul>${reviewing}</ul>` : ''}
      ${auto ? `<div class="side-head" style="margin-top:8px">Small corrections saved on their own</div><ul class="auto-list">${auto}</ul>` : ''}</div>
      <div class="btns"><button class="btn" data-a="close">Close</button></div>`);
  overlay.querySelector('.modal').classList.add('wide');
  overlay.addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-a]'); if (!b) return;
    if (b.dataset.a === 'close') return close();
    const post = async (body) => {
      const r = await fetch('/api/lrm/vault-updates', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const res = await r.json(); if (!r.ok) throw new Error(res.error || r.status); return res;
    };
    if (b.dataset.a === 'approve-all' || b.dataset.a === 'reject-all') {
      const yes = b.dataset.a === 'approve-all';
      if (!confirm(yes ? `Save all ${d.pending.length} updates to the vault?` : `Reject all ${d.pending.length} updates?`)) return;
      try { const res = await post({ all: true, decision: yes ? 'approve' : 'reject' }); toast(`${yes ? 'Saved' : 'Rejected'} ${res.items.length}`); if (yes) await loadIndex({ quiet: true }); updatesDot(); close(); } catch (err) { toast(err.message); }
      return;
    }
    if (b.dataset.a === 'undo') {
      const li = b.closest('li');
      try { await post({ id: li.dataset.id, decision: 'undo' }); li.remove(); toast('Undone: the note is back to how it was.'); await loadIndex({ quiet: true }); } catch (err) { toast(err.message); }
      return;
    }
    const box = b.closest('.upd'); const id = box?.dataset.id; if (!id) return;
    const u = d.pending.find((x) => x.id === id);
    if (b.dataset.a === 'edit') {
      box.querySelector('pre').outerHTML = `<textarea>${esc(u.content)}</textarea>`;
      b.textContent = 'Editing'; b.disabled = true; return;
    }
    const content = box.querySelector('textarea')?.value;
    try {
      const r = await fetch('/api/lrm/vault-updates', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, decision: b.dataset.a, content }) });
      const res = await r.json(); if (!r.ok) throw new Error(res.error || r.status);
      box.remove(); toast(b.dataset.a === 'approve' ? `Saved: ${u.path}` : 'Rejected');
      if (b.dataset.a === 'approve') { await loadIndex({ quiet: true }); openPath(u.path); }
      updatesDot();
      if (!overlay.querySelector('.upd')) close();
    } catch (err) { toast(`Could not ${b.dataset.a}: ${err.message}`); }
  });
}

function gitPanel() {
  const { overlay, close } = modal('<div class="git" id="git-body">Loading…</div>');
  overlay.querySelector('.modal').style.width = 'min(640px, calc(100vw - 32px))';
  let timer = null; let typing = false;
  const stop = () => { clearTimeout(timer); };
  overlay.addEventListener('cancel', stop);
  const draw = (st) => {
    const body = $('#git-body', overlay);
    if (!body) return stop();
    if (!st.available) {
      body.innerHTML = '<h3><i class="fa-brands fa-github"></i>Sync with GitHub</h3><p>Git sync is not on this server yet. It arrives with the next deploy.</p>';
      return;
    }
    const [cls, label] = gitBadge(st);
    const repo = st.repo || '';
    const when = st.status_time ? dmyTime(st.status_time) : '';
    const needsKey = st.sync_status === 'FAIL' && /reach|deploy key|permission|denied/i.test(st.message || '');
    const steps = repo && (needsKey || !st.sync_status) ? `
      <ol>
        <li>Create the repository if it doesn't exist: <a href="https://github.com/new?name=${encodeURIComponent(repo.split('/')[1] || '')}&visibility=private" target="_blank" rel="noopener">github.com/new</a>, owner <b>${esc(repo.split('/')[0])}</b>, name <b>${esc(repo.split('/')[1])}</b>, <b>Private</b>.</li>
        <li>Open <a href="https://github.com/${esc(repo)}/settings/keys/new" target="_blank" rel="noopener">${esc(repo)} → Settings → Deploy keys → Add</a>. Title <b>LRMagent</b>, paste the key below, tick <b>Allow write access</b>, then <b>Add key</b>.</li>
        <li>Come back and press <b>Sync now</b>.</li>
      </ol>
      <div class="key" id="git-key">${esc(st.deploy_key || 'The key appears within a minute.')}</div>
      <div class="row"><button class="btn" id="git-copy"><i class="fa-regular fa-copy"></i> Copy key</button></div>` : '';
    body.innerHTML = `<h3><i class="fa-brands fa-github"></i>Sync with GitHub <span class="state ${cls}">${esc(label)}</span><button class="icon-btn x" id="git-close" title="Close"><i class="fa-solid fa-xmark"></i></button></h3>
      <p class="muted" style="margin:0">Your knowledge notes go to a private GitHub repository every hour, and changes made there come back. Open that repository as a vault in the Obsidian app (with the Obsidian Git plugin, or a GitHub Desktop clone) to use real Obsidian on your computer or phone.</p>
      ${st.error ? `<div class="banner warn" style="margin:0"><i class="fa-solid fa-triangle-exclamation"></i>${esc(st.error)}</div>` : ''}
      ${st.message && st.sync_status && st.sync_status !== 'OK' ? `<div class="banner ${cls === 'bad' ? 'warn' : 'info'}" style="margin:0">${esc(st.message)}</div>` : ''}
      ${st.sync_status === 'OK' ? `<div class="muted">Last sync ${esc(when)}: ${esc(st.message)}</div>` : ''}
      <div class="row"><input id="git-repo" placeholder="owner/repository, e.g. lrmcorp/lrmagent-vault" value="${esc(repo)}" spellcheck="false" ${st.source === 'github' ? 'disabled title="Set by the LRMAGENT_VAULT_REPO GitHub variable"' : ''}>
        ${st.source === 'github' ? '' : `<button class="btn primary" id="git-save">${repo ? 'Change' : 'Save'}</button>`}</div>
      ${st.source === 'github' ? '<div class="muted">Set by the LRMAGENT_VAULT_REPO variable in GitHub; change it there.</div>' : ''}
      ${steps}
      ${repo ? `<div class="row"><button class="btn primary" id="git-sync"${st.syncing || st.pending_sync ? ' disabled' : ''}><i class="fa-solid fa-rotate"></i> Sync now</button>
        <a class="btn" href="https://github.com/${esc(repo)}" target="_blank" rel="noopener"><i class="fa-brands fa-github"></i> Open on GitHub</a>
        ${st.source === 'github' ? '' : '<button class="btn" id="git-off">Turn off</button>'}</div>` : ''}
      <div class="muted">Synced: <code>knowledge/</code> (and agents' own knowledge folders). A note changed in both places keeps your Obsidian version and saves the agent's as "(agent copy)".${st.stale ? ' <b>The server has not answered for a few minutes.</b>' : ''}</div>
      ${st.log?.length ? `<details><summary class="muted">Sync log</summary><pre>${esc(st.log.join('\n'))}</pre></details>` : ''}`;
    $('#git-close', body)?.addEventListener('click', () => { stop(); close(); gitDot(); });
    const input = $('#git-repo', body);
    input?.addEventListener('focus', () => { typing = true; });
    input?.addEventListener('blur', () => { typing = false; });
    $('#git-copy', body)?.addEventListener('click', () => copy(st.deploy_key || ''));
    $('#git-save', body)?.addEventListener('click', async () => {
      try { draw(await post('git_config', { repo: input.value })); typing = false; toast('Saved. The server picks it up within a minute.'); } catch (e) { fail(e); }
    });
    $('#git-sync', body)?.addEventListener('click', async () => {
      try { draw(await post('git_sync', {})); toast('Syncing within a minute'); } catch (e) { fail(e); }
    });
    $('#git-off', body)?.addEventListener('click', async () => {
      const ok = await ask({ title: 'Turn off Git sync?', text: 'Notes stay where they are, here and on GitHub; they just stop syncing.', ok: 'Turn off' });
      if (ok) { try { draw(await post('git_config', { repo: '' })); } catch (e) { fail(e); } }
    });
  };
  const tick = async () => {
    if (!document.body.contains(overlay)) return;
    if (!typing) { try { const st = await api('git'); draw(st); } catch (e) { $('#git-body', overlay).textContent = e.message; } }
    timer = setTimeout(tick, 3000);
  };
  tick();
  overlay.addEventListener('cancel', () => { stop(); gitDot(); });
  return close;
}

const COMMANDS = [
  { name: 'Open quick switcher', key: 'O', fn: () => quickSwitcher() },
  { name: 'Create new note', key: 'N', fn: () => newNote() },
  { name: 'Create new folder', fn: () => newFolder() },
  { name: 'New note from template', fn: () => newFromTemplate() },
  { name: 'New canvas', fn: () => newBoard('canvas') },
  { name: 'New table view (base)', fn: () => newBoard('base') },
  { name: 'Insert template', fn: () => insertTemplate() },
  { name: 'Find and replace in this note', key: 'H', fn: () => { const t = activeTab(); if (t?.kind === 'note' && t.editable && t.mode !== 'edit') { t.mode = 'edit'; renderView(); } curEd()?.find(); } },
  { name: 'Toggle live preview / source mode', fn: () => toggleSourceMode() },
  { name: 'Attach images or PDFs to this note', fn: () => { const t = activeTab(); if (t?.kind === 'note') uploadInto(dirname(t.path) || 'knowledge'); } },
  { name: "Open today's daily note", fn: () => dailyNote() },
  { name: 'Open graph view', key: 'G', fn: () => openGraph() },
  { name: 'Topologies of Thoughts (full-screen graph, hand gestures)', fn: () => openTopology() },
  { name: 'Set up my vault (numbered folders and a Home note)', fn: () => setupVault() },
  { name: 'Open Home', fn: () => (S.lc.has('knowledge/home.md') ? openPath('knowledge/Home.md') : setupVault()) },
  { name: 'Show the graph beside notes (right sidebar)', fn: () => { setPane('right', true); S.rightTab = 'graph'; saveState(); renderRight(); } },
  { name: 'Open local graph of this note', fn: () => activeTab()?.kind === 'note' && openGraph(activeTab().path) },
  { name: 'Toggle reading / editing view', key: 'E', fn: () => setMode() },
  { name: 'Search in all notes', key: '⇧ F', fn: () => { setPane('left', true); setLeftTab('search'); $('#search-q').focus(); } },
  { name: 'Rename this note', fn: () => activeTab()?.path && renamePath(activeTab().path, { nameOnly: true }) },
  { name: 'Move this note', fn: () => activeTab()?.path && renamePath(activeTab().path) },
  { name: 'Delete this note', fn: () => activeTab()?.path && deletePath(activeTab().path) },
  { name: 'Copy link to this note', fn: () => activeTab()?.path && copy(`[[${linkName(activeTab().path)}]]`) },
  { name: 'Reveal this note in the file explorer', fn: () => activeTab()?.path && reveal(activeTab().path) },
  { name: 'Bookmark this note (or remove it)', fn: () => toggleBookmark() },
  { name: 'Open this note to the right (split view)', fn: () => openSplit() },
  { name: 'Close the split view', fn: () => S.panes.length > 1 && closePane(S.focus === 0 ? 1 : 0) },
  { name: 'Open the calendar', fn: () => { setPane('right', true); S.rightTab = 'calendar'; saveState(); renderRight(); } },
  { name: 'Toggle left sidebar', fn: () => setPane('left') },
  { name: 'Toggle right sidebar', fn: () => setPane('right') },
  { name: 'Close this tab', fn: () => S.active >= 0 && closeTab(S.active) },
  { name: 'Reload the vault from disk', fn: () => loadIndex() },
  { name: 'Sync with GitHub now', fn: async () => { try { await post('git_sync', {}); toast('Syncing within a minute'); } catch (e) { fail(e); } } },
  { name: 'Git sync settings', fn: () => gitPanel() },
];
function palette() {
  picker({
    placeholder: 'Type a command…',
    footer: '<span><kbd>↑↓</kbd> navigate</span><span><kbd>↵</kbd> run</span>',
    items: (q) => COMMANDS.map((c) => ({ ...c, score: fuzzy(q, c.name) })).filter((c) => c.score > 0).sort((a, b) => (q ? b.score - a.score : 0))
      .map((c) => ({ fn: c.fn, html: esc(c.name), sub: c.key ? `${isMac ? '⌘' : 'Ctrl'} ${c.key}` : '' })),
    onPick: (it) => it.fn(),
  });
}

// Panes, top bar, keyboard
function setPane(side, open) {
  const body = $('#body');
  const cls = side === 'left' ? 'no-left' : 'no-right';
  const show = open ?? body.classList.contains(cls);
  body.classList.toggle(cls, !show);
  $(`#toggle-${side}`).setAttribute('aria-pressed', String(show));
  if (small() && show) { // phones: one side pane at a time, over the note
    const other = side === 'left' ? 'right' : 'left';
    body.classList.add(other === 'left' ? 'no-left' : 'no-right');
    $(`#toggle-${other}`).setAttribute('aria-pressed', 'false');
  }
  if (!small()) { ui[`${side}Open`] = show; saveState(); }
  if (activeTab()?.kind === 'graph') setTimeout(() => drawGraph(activeTab()), 50);
}
window.matchMedia('(max-width: 900px)').addEventListener('change', (e) => {
  if (e.matches) { setPane('left', false); setPane('right', false); } else { setPane('left', ui.leftOpen !== false); setPane('right', ui.rightOpen !== false); }
});
$('#toggle-left').onclick = () => setPane('left');
$('#toggle-right').onclick = () => setPane('right');
$('#btn-switcher').onclick = () => quickSwitcher();
$('#btn-palette').onclick = () => palette();
$('#btn-git').onclick = () => gitPanel();
$('#btn-updates').onclick = () => updatesPanel();
updatesDot();
setInterval(updatesDot, 60000);
$('#btn-daily').onclick = () => dailyNote();
$('#btn-graph').onclick = () => openGraph();
$('#btn-topo').onclick = () => openTopology();
$('#btn-new-note').onclick = () => newNote();
$('#btn-new-folder').onclick = () => newFolder();
$('#btn-refresh').onclick = () => loadIndex();
$('#btn-collapse').onclick = () => { S.expanded = new Set(); saveState(); renderTree(); };

document.addEventListener('keydown', (e) => {
  if (document.querySelector('.overlay')) return;
  const k = e.key.toLowerCase();
  if (mod(e) && !e.altKey) {
    const map = { o: () => quickSwitcher(), p: () => palette(), e: () => setMode(), n: () => newNote(), g: () => openGraph(), s: () => { const t = activeTab(); if (t) saveTab(t); } };
    if (e.shiftKey && k === 'f') { e.preventDefault(); setPane('left', true); setLeftTab('search'); $('#search-q').focus(); $('#search-q').select(); return; }
    if (!e.shiftKey && map[k]) { e.preventDefault(); map[k](); return; }
  }
  if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && activeTab()?.kind === 'note') {
    e.preventDefault(); view.querySelector(`[data-act="${e.key === 'ArrowLeft' ? 'back' : 'fwd'}"]`)?.click();
  }
  if (e.key === 'Escape') closeMenus();
});

// Agents & start-up
async function switchAgent(id) {
  await flushAll();
  stopGraph();
  S.agent = id;
  const saved = store.get(`lrm_vault:${id}`, {});
  S.panes.forEach((p) => p.ed?.destroy());
  const panes = (saved.panes || [{ tabs: saved.tabs || [], active: saved.active ?? -1 }]).slice(0, 2);
  S.panes = panes.map((p) => {
    const tabs = (p.tabs || []).filter((t) => t && (t.kind === 'graph' || t.path)).map((t) => Object.assign(newTabObj(t.kind, t.path, t.mode), t.kind === 'graph' ? { center: t.center || null } : {}));
    return { tabs, active: tabs.length ? Math.min(Math.max(p.active ?? 0, 0), tabs.length - 1) : -1, ed: null, sim: null };
  }).filter((p, i) => i === 0 || p.tabs.length);
  S.focus = Math.min(saved.focus || 0, S.panes.length - 1);
  S.expanded = new Set(saved.expanded || SECTIONS);
  S.folder = saved.folder || 'knowledge';
  Object.assign(S.graph, saved.graph || {}, { filter: '' });
  saveState();
  buildPanes();
  loadPrefs();
  await loadIndex();
  // Tabs whose note is gone are dropped; the rest load their text.
  for (const pane of S.panes) {
    pane.tabs = pane.tabs.filter((t) => t.kind === 'graph' || S.lc.has(t.path.toLowerCase()));
    pane.active = Math.min(Math.max(pane.active, pane.tabs.length ? 0 : -1), pane.tabs.length - 1);
  }
  if (S.panes.length > 1 && !S.panes[1].tabs.length) { S.panes.length = 1; S.focus = 0; }
  renderAll(); renderTree();
  for (const pane of S.panes) for (const t of pane.tabs) if (isText(t)) loadTab(t);
}

async function start() {
  if (small()) { $('#body').classList.add('no-left', 'no-right'); $('#toggle-left').setAttribute('aria-pressed', 'false'); $('#toggle-right').setAttribute('aria-pressed', 'false'); } else {
    if (ui.leftOpen === false) setPane('left', false);
    if (ui.rightOpen === false) setPane('right', false);
  }
  // Layout 2: the graph docked on the right by default (files · note · graph).
  S.leftTab = ui.leftTab || 'files'; S.rightTab = ui.layout === 2 ? (ui.rightTab || 'graph') : 'graph';
  ui.layout = 2;
  setLeftTab(S.leftTab);
  let data;
  try { data = await api('agents'); } catch (e) { buildPanes(); paneView().innerHTML = `<div class="empty-state"><h2>Can't open the vault</h2><p>${esc(e.message)}</p></div>`; return; }
  S.agents = data.agents;
  let preferred = ui.agent;
  try { preferred = preferred || localStorage.getItem('cow_active_agent'); } catch { /* ignore */ }
  const id = S.agents.some((a) => a.id === preferred) ? preferred : data.default;
  const select = $('#agent');
  select.innerHTML = S.agents.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}${a.enabled ? '' : ' (off)'}</option>`).join('');
  select.value = id;
  select.hidden = S.agents.length < 2;
  select.onchange = () => switchAgent(select.value);
  await switchAgent(id);
  runSearch();
  gitDot();
  setInterval(gitDot, 60000);
  // Agents write to their memory all the time: pick up their changes while the page is open.
  setInterval(() => { if (document.visibilityState === 'visible' && !allTabs().some((t) => t.saving)) loadIndex({ quiet: true }); }, 15000);
}
start();
