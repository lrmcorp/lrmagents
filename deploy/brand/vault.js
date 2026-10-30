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

// ---- paths -------------------------------------------------------------------
const dirname = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
const basename = (p) => p.slice(p.lastIndexOf('/') + 1);
const stem = (p) => basename(p).replace(/\.md$/i, '');
const isNote = (p) => /\.md$/i.test(p);
const isImage = (p) => /\.(png|jpe?g|gif|webp|svg)$/i.test(p);
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
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(t * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

// ---- state -------------------------------------------------------------------
const S = {
  agent: '', agents: [], entries: [], notes: new Map(), lc: new Map(), byStem: new Map(), byName: new Map(),
  aliases: new Map(), dirs: new Set(), out: new Map(), back: new Map(), unresolved: new Map(), tags: new Map(),
  tabs: [], active: -1, expanded: new Set(SECTIONS), folder: 'knowledge', sharedKnowledge: false, truncated: false,
  leftTab: 'files', rightTab: 'links', graph: { local: false, depth: 1, orphans: true, unresolved: false, tags: false, filter: '' },
  sim: null, loaded: false,
};
const ui = store.get('lrm_vault:ui', {});
const activeTab = () => S.tabs[S.active] || null;

// ---- API ---------------------------------------------------------------------
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

// ---- theme: follow the console ------------------------------------------------
function syncTheme() {
  let dark = false;
  try {
    dark = window.parent !== window ? window.parent.document.documentElement.classList.contains('dark')
      : (localStorage.getItem('cow_theme') || 'light') === 'dark';
  } catch { dark = window.matchMedia('(prefers-color-scheme: dark)').matches; }
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

// ---- toasts, menus, modals ------------------------------------------------------
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

// ---- index: links, backlinks, tags --------------------------------------------
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
  for (const [path, e] of S.notes) {
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
    renderTree(); renderTags(); renderRight(); renderStatus();
    if (activeTab()?.kind === 'graph' && !quiet) renderView();
    if (!activeTab() && !quiet) renderView();
    await refreshOpenNotes();
  } catch (e) { if (!quiet) fail(e); }
}
let indexTimer = null;
const loadIndexSoon = () => { clearTimeout(indexTimer); indexTimer = setTimeout(() => loadIndex({ quiet: true }), 1200); };

// Notes the agent changed while they were open (and the user hasn't touched) are reloaded.
async function refreshOpenNotes() {
  for (const tab of S.tabs) {
    if (tab.kind !== 'note' || tab.loading || tab.conflict || tab.content !== tab.saved) continue;
    const entry = S.notes.get(tab.path);
    if (!entry || (!tab.stale && entry.mtime <= tab.mtime + 0.001)) continue;
    tab.stale = false;
    try {
      const data = await api('read', { path: tab.path });
      if (tab.content !== tab.saved) continue;
      Object.assign(tab, { content: data.content, saved: data.content, mtime: data.mtime, editable: data.editable });
      if (tab === activeTab()) {
        const ta = $('#editor');
        if (ta && tab.mode === 'edit') {
          const [a, b] = [ta.selectionStart, ta.selectionEnd];
          ta.value = data.content; ta.setSelectionRange(a, b); autosize(ta);
        } else renderView();
        renderRight(); renderStatus();
      }
    } catch { /* deleted or unreadable: the index refresh shows it */ }
  }
}

// ---- markdown rendering ----------------------------------------------------------
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
    if (/\.pdf$/i.test(target)) return `<a class="internal" href="${esc(fileUrl(target))}" target="_blank" rel="noopener">${esc(basename(target))}</a>`;
    if (isNote(target)) return `<span class="embed" data-embed="${esc(target)}" data-heading="${esc(m.heading)}"></span>`;
  }
  const label = m.alias || [m.name, m.heading].filter(Boolean).join(' › ');
  return `<a href="#" class="internal${target ? '' : ' unresolved'}" data-href="${esc(target || m.name)}"${target ? '' : ' data-create="1"'} data-heading="${esc(m.heading)}">${esc(label)}</a>`;
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

// ---- file tree ---------------------------------------------------------------------
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
    const name = dir ? basename(e.path) : (isNote(e.path) ? stem(e.path) : basename(e.path));
    const ext = !dir && !isNote(e.path) ? `<span class="ext">${esc(e.path.split('.').pop().toUpperCase())}</span>` : '';
    const icon = dir ? (open ? 'fa-regular fa-folder-open' : 'fa-regular fa-folder') : isNote(e.path) ? 'fa-regular fa-file-lines' : isImage(e.path) ? 'fa-regular fa-image' : 'fa-regular fa-file';
    const cls = ['tree-row', dir && S.folder === e.path ? 'selected-folder' : '', !dir && e.path === current ? 'active' : ''].join(' ');
    let html = `<div class="${cls}" role="treeitem" tabindex="-1" data-path="${esc(e.path)}" data-type="${e.type}" draggable="true" style="padding-left:${6 + depth * 14}px"${dir ? ` aria-expanded="${open}"` : ''}>
      ${dir ? `<i class="fa-solid fa-chevron-right chev${open ? ' open' : ''}"></i>` : '<span class="chev"></span>'}<i class="ico ${icon}"></i><span class="name">${esc(name)}</span>${ext}
      <button class="icon-btn more" data-more title="More" tabindex="-1"><i class="fa-solid fa-ellipsis"></i></button></div>`;
    if (dir && open) for (const c of kids.get(e.path) || []) html += row(c, depth + 1);
    return html;
  };
  let html = '';
  if (S.notes.has(ROOT_FILE)) {
    html += `<div class="tree-row${current === ROOT_FILE ? ' active' : ''}" data-path="${ROOT_FILE}" data-type="file" title="Long-term memory the agent reads every conversation">
      <span class="chev"></span><i class="ico fa-solid fa-brain" style="color:var(--brand)"></i><span class="name">MEMORY</span></div>`;
  }
  const labels = { memory: ['Memory', 'fa-solid fa-clock-rotate-left', 'Daily notes and what the agent remembers'], knowledge: ['Knowledge', 'fa-solid fa-book', 'The knowledge wiki'] };
  for (const s of SECTIONS) {
    const open = S.expanded.has(s);
    const [label, icon, title] = labels[s];
    const badge = s === 'knowledge' && S.sharedKnowledge ? '<span class="badge" title="This agent reads the shared knowledge base">SHARED</span>' : '';
    html += `<div class="tree-row section${S.folder === s ? ' selected-folder' : ''}" data-path="${s}" data-type="dir" title="${esc(title)}" aria-expanded="${open}">
      <i class="fa-solid fa-chevron-right chev${open ? ' open' : ''}"></i><i class="ico ${icon}"></i><span class="name">${label}</span>${badge}
      <button class="icon-btn more" data-more title="More" tabindex="-1"><i class="fa-solid fa-ellipsis"></i></button></div>`;
    if (open) {
      const list = kids.get(s) || [];
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
  } else {
    items.push({ icon: 'fa-regular fa-file-lines', label: 'Open in new tab', fn: () => openPath(path, { newTab: true }) });
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
  if (!rowEl || !dragPath || rowEl.dataset.path === dirname(dragPath) || `${rowEl.dataset.path}/`.startsWith(`${dragPath}/`)) return;
  e.preventDefault();
  rowEl.classList.add('drop');
});
$('#tree').addEventListener('dragend', () => { dragPath = null; document.querySelectorAll('.tree-row.drop').forEach((r) => r.classList.remove('drop')); });
$('#tree').addEventListener('drop', (e) => {
  const rowEl = e.target.closest('.tree-row[data-type=dir]');
  if (!rowEl || !dragPath) return;
  e.preventDefault();
  const from = dragPath; dragPath = null;
  rowEl.classList.remove('drop');
  movePath(from, `${rowEl.dataset.path}/${basename(from)}`);
});

// ---- file operations -------------------------------------------------------------------
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
    if (focusTitle) { const t = $('#inline-title'); if (t) { t.focus(); t.select(); } }
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

function dailyNote() {
  const path = `memory/${today()}.md`;
  if (S.notes.has(path)) return openPath(path);
  S.expanded.add('memory');
  return createNote(path, `# ${today()}\n\n`, { mode: 'edit' });
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
    for (const tab of S.tabs) {
      if (tab.kind !== 'note' && tab.kind !== 'asset') continue;
      if (tab.path === path) tab.path = newPath;
      else if (tab.path.startsWith(`${path}/`)) tab.path = newPath + tab.path.slice(path.length);
    }
    if (S.expanded.has(path)) S.expanded.add(newPath);
    S.expanded.add(dirname(newPath));
    await loadIndex({ quiet: true });
    // Notes that the link update rewrote are reloaded from disk.
    for (const tab of S.tabs) if (tab.kind === 'note') tab.stale = true;
    await refreshOpenNotes();
    const tab = activeTab();
    if (tab?.path === newPath && isNote(newPath) && stem(newPath) !== oldStem && tab.content.startsWith(`# ${oldStem}\n`)) {
      // Keep a "# Untitled" heading in step with the new name.
      tab.content = `# ${stem(newPath)}\n${tab.content.slice(oldStem.length + 3)}`;
      await saveTab(tab);
    }
    renderTabs(); renderView();
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
    for (let i = S.tabs.length - 1; i >= 0; i--) {
      const p = S.tabs[i].path;
      if (p && (p === path || p.startsWith(`${path}/`))) closeTab(i, { skipSave: true });
    }
    await loadIndex({ quiet: true });
    toast('Moved to trash');
  } catch (e) { fail(e); }
}

// ---- tabs --------------------------------------------------------------------------------
function saveState() {
  store.set(`lrm_vault:${S.agent}`, {
    tabs: S.tabs.map((t) => ({ kind: t.kind, path: t.path, mode: t.mode })),
    active: S.active, expanded: [...S.expanded], folder: S.folder, graph: S.graph,
  });
  store.set('lrm_vault:ui', { ...ui, agent: S.agent, leftTab: S.leftTab, rightTab: S.rightTab });
}

function newTabObj(kind, path, mode) {
  return { kind, path, mode: mode || 'read', content: '', saved: '', mtime: 0, editable: true, loading: kind === 'note', error: '', conflict: false, history: [], fwd: [], scroll: 0 };
}

async function openPath(path, { newTab = false, mode = null, heading = '', line = 0, fromHistory = false } = {}) {
  if (!path) return;
  const kind = isNote(path) ? 'note' : 'asset';
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
  if (kind === 'note') await loadTab(tab);
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
  if (tab === activeTab()) { renderView(); renderRight(); renderStatus(); }
}

function activate(i) {
  if (i === S.active) return;
  const cur = activeTab();
  if (cur) { cur.scroll = $('.scroller')?.scrollTop || 0; flush(cur); }
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
  saveState(); renderTabs(); renderTree(); renderView();
}

function renderTabs() {
  $('#tabbar').innerHTML = S.tabs.map((t, i) => {
    const name = t.kind === 'graph' ? (t.center ? `Graph: ${stem(t.center)}` : 'Graph view') : (t.path === ROOT_FILE ? 'MEMORY' : (isNote(t.path) ? stem(t.path) : basename(t.path)));
    const icon = t.kind === 'graph' ? 'fa-diagram-project' : t.kind === 'asset' ? 'fa-image' : 'fa-file-lines';
    const dirty = t.kind === 'note' && t.content !== t.saved;
    return `<div class="tab${i === S.active ? ' active' : ''}" data-i="${i}" title="${esc(t.path || name)}">
      <i class="fa-regular ${icon}" style="font-size:11px;opacity:.7"></i><span class="t-name">${esc(name)}</span>${dirty ? '<span class="dirty" title="Saving…"></span>' : ''}
      <button class="icon-btn close" data-close="${i}" title="Close"><i class="fa-solid fa-xmark"></i></button></div>`;
  }).join('') + '<button class="icon-btn" id="tab-new" title="New tab (quick switcher)" style="margin:0 0 4px 2px"><i class="fa-solid fa-plus"></i></button>';
}
$('#tabbar').addEventListener('click', (e) => {
  if (e.target.closest('#tab-new')) return quickSwitcher(true);
  const close = e.target.closest('[data-close]');
  if (close) { closeTab(Number(close.dataset.close)); return; }
  const tab = e.target.closest('.tab');
  if (tab) activate(Number(tab.dataset.i));
});
$('#tabbar').addEventListener('auxclick', (e) => { const tab = e.target.closest('.tab'); if (tab && e.button === 1) closeTab(Number(tab.dataset.i)); });

// ---- saving --------------------------------------------------------------------------------
let saveTimer = null;
function scheduleSave(tab) { clearTimeout(saveTimer); saveTimer = setTimeout(() => saveTab(tab), 900); }
async function flush(tab) { if (tab?.kind === 'note' && tab.content !== tab.saved && !tab.conflict) { clearTimeout(saveTimer); await saveTab(tab); } }
async function flushAll() { clearTimeout(saveTimer); for (const t of S.tabs) await flush(t); }

async function saveTab(tab, { force = false } = {}) {
  if (tab.kind !== 'note' || !tab.editable || (tab.conflict && !force)) return;
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
  if (S.tabs.some((t) => t.kind === 'note' && t.content !== t.saved)) { flushAll(); e.preventDefault(); e.returnValue = ''; }
});
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushAll(); });

// ---- the note view -------------------------------------------------------------------------
function setMode(mode) {
  const tab = activeTab();
  if (tab?.kind !== 'note') return;
  if (mode === 'edit' && !tab.editable) { toast('This note is maintained by the agent and is read-only'); return; }
  tab.mode = mode || (tab.mode === 'edit' ? 'read' : 'edit');
  if (tab.mode === 'read') flush(tab);
  saveState(); renderView();
}

function crumbs(path) {
  const parts = path.split('/');
  return parts.map((p, i) => `<span data-folder="${esc(parts.slice(0, i + 1).join('/'))}">${esc(i === parts.length - 1 && isNote(p) ? stem(p) : p)}</span>`).join(' / ');
}

function renderView() {
  const view = $('#view');
  const tab = activeTab();
  if (!tab || tab.kind !== 'graph') stopGraph();
  if (!tab) { view.innerHTML = emptyState(); renderRight(); renderStatus(); return; }
  if (tab.kind === 'graph') { renderGraph(view, tab); renderRight(); renderStatus(); return; }
  const back = tab.history.length; const fwd = tab.fwd.length;
  const head = `<div class="note-head">
    <button class="icon-btn" data-act="back" title="Back (Alt ←)" ${back ? '' : 'disabled style="opacity:.35"'}><i class="fa-solid fa-arrow-left"></i></button>
    <button class="icon-btn" data-act="fwd" title="Forward (Alt →)" ${fwd ? '' : 'disabled style="opacity:.35"'}><i class="fa-solid fa-arrow-right"></i></button>
    <div class="crumbs">${crumbs(tab.path)}</div>
    ${tab.kind === 'note' ? `${tab.editable ? '' : '<!--'}<button class="icon-btn" data-act="mode" title="${tab.mode === 'edit' ? 'Reading view' : 'Edit'} (Ctrl/⌘ E)"><i class="fa-solid ${tab.mode === 'edit' ? 'fa-book-open' : 'fa-pen'}"></i></button>${tab.editable ? '' : '-->'}
    <button class="icon-btn" data-act="graph" title="Local graph"><i class="fa-solid fa-diagram-project"></i></button>` : ''}
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
  else if (tab.mode === 'edit') body = `<textarea class="editor" id="editor" spellcheck="true" placeholder="Start writing…  [[ to link a note, # for tags"></textarea>`;
  else {
    const { props, html } = renderMarkdown(tab.content, tab.path, { readonly: !tab.editable });
    body = `${propsHtml(props)}<div class="md" id="reading">${html || `<p class="muted">This note is empty.${tab.editable ? ' Press Ctrl/⌘ E to write.' : ''}</p>`}</div>`;
  }
  view.innerHTML = `${head}<div class="scroller"><div class="doc"><div id="banner"></div>
    <input class="inline-title" id="inline-title" value="${esc(tab.path === ROOT_FILE ? 'MEMORY' : stem(tab.path))}" ${readonlyTitle ? 'readonly' : ''} spellcheck="false" aria-label="Note name">
    ${body}</div></div>`;
  renderBanner();
  const ta = $('#editor');
  if (ta) { ta.value = tab.content; autosize(ta); }
  const reading = $('#reading');
  if (reading) { highlightCode(reading); fillEmbeds(reading, 0); }
  $('.scroller').scrollTop = tab.scroll || 0;
  tab.scroll = 0;
  renderRight(); renderStatus();
}

function renderBanner() {
  const tab = activeTab(); const el = $('#banner');
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
  return `<div class="empty-state"><i class="fa-solid fa-gem" style="font-size:34px;color:var(--brand)"></i><h2>Your agent's vault</h2>
    <p>Everything this agent remembers: its long-term memory, daily notes and knowledge wiki. Notes link with <code>[[wikilinks]]</code> like Obsidian.</p>
    <div class="actions">
      <button class="link-btn" data-act="new">Create a new note <kbd>${isMac ? '⌘' : 'Ctrl'} N</kbd></button>
      <button class="link-btn" data-act="switcher">Go to a note <kbd>${isMac ? '⌘' : 'Ctrl'} O</kbd></button>
      <button class="link-btn" data-act="daily">Open today's daily note</button>
      <button class="link-btn" data-act="open-graph">Open graph view <kbd>${isMac ? '⌘' : 'Ctrl'} G</kbd></button>
    </div>
    ${recent.length ? `<div style="margin-top:22px;text-align:left"><div class="side-head">Recently changed</div>${recent.map((n) => `<a class="side-item" data-open="${esc(n.path)}">${esc(n.path === ROOT_FILE ? 'MEMORY' : stem(n.path))} <small>· ${esc(dirname(n.path) || 'vault')} · ${ago(n.mtime)}</small></a>`).join('')}</div>` : ''}</div>`;
}

function autosize(ta) { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight + 4}px`; }

function scrollToHeading(heading) {
  const tab = activeTab();
  if (!tab) return;
  if (tab.mode === 'edit') {
    const lines = tab.content.split('\n');
    const at = lines.findIndex((l) => /^#{1,6}\s/.test(l) && slug(l.replace(/^#+\s*/, '')) === slug(heading));
    if (at >= 0) caretToLine(at + 1);
    return;
  }
  const el = document.getElementById(`h-${slug(heading)}`);
  if (el) { el.scrollIntoView({ block: 'start', behavior: 'smooth' }); el.animate([{ background: 'var(--mark)' }, { background: 'transparent' }], { duration: 1500 }); }
}

function caretToLine(line) {
  const ta = $('#editor');
  if (!ta) return;
  const lines = ta.value.split('\n');
  const pos = lines.slice(0, Math.max(0, line - 1)).reduce((n, l) => n + l.length + 1, 0);
  ta.focus({ preventScroll: true });
  ta.setSelectionRange(pos, pos);
  const y = caretXY(ta).y;
  const scroller = $('.scroller');
  scroller.scrollTop += y - scroller.getBoundingClientRect().top - scroller.clientHeight / 3;
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

const view = $('#view');
view.addEventListener('click', async (e) => {
  const tab = activeTab();
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act) {
    if (act === 'mode') return setMode();
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

// ---- the editor ----------------------------------------------------------------------------
view.addEventListener('input', (e) => {
  if (e.target.id !== 'editor') return;
  const tab = activeTab();
  tab.content = e.target.value;
  autosize(e.target);
  scheduleSave(tab);
  if (!$('.tab.active .dirty')) renderTabs();
  renderStatusSoon(); renderRightSoon();
  updateSuggest(e.target);
});
view.addEventListener('keydown', (e) => {
  if (e.target.id !== 'editor') return;
  const ta = e.target;
  if (suggest && handleSuggestKey(e, ta)) return;
  if (e.key === 'Tab') {
    e.preventDefault();
    const { selectionStart: s, value } = ta;
    const lineStart = value.lastIndexOf('\n', s - 1) + 1;
    if (e.shiftKey) {
      if (value[lineStart] === '\t') { ta.setSelectionRange(lineStart, lineStart + 1); insertText(ta, ''); ta.setSelectionRange(s - 1, s - 1); }
    } else if (/^\s*([-*+]|\d+[.)])\s/.test(value.slice(lineStart))) {
      const off = s - lineStart; ta.setSelectionRange(lineStart, lineStart); insertText(ta, '\t'); ta.setSelectionRange(lineStart + off + 1, lineStart + off + 1);
    } else insertText(ta, '\t');
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey && !mod(e) && ta.selectionStart === ta.selectionEnd) {
    const s = ta.selectionStart; const value = ta.value;
    const lineStart = value.lastIndexOf('\n', s - 1) + 1;
    const line = value.slice(lineStart, s);
    const m = /^(\s*(?:>\s*)*)([-*+]|(\d+)([.)]))(\s+)(\[[ xX]\]\s+)?/.exec(line);
    if (!m) return;
    e.preventDefault();
    if (line.trim() === m[0].trim()) { ta.setSelectionRange(lineStart, s); insertText(ta, ''); return; } // empty item ends the list
    const bullet = m[3] ? `${Number(m[3]) + 1}${m[4]}` : m[2];
    insertText(ta, `\n${m[1]}${bullet}${m[5]}${m[6] ? '[ ] ' : ''}`);
  }
});
function insertText(ta, text) {
  ta.focus();
  if (!document.execCommand(text ? 'insertText' : 'delete', false, text)) {
    ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, 'end');
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

// Caret position on screen, for the [[ link suggester.
function caretXY(ta) {
  const div = document.createElement('div');
  const cs = getComputedStyle(ta);
  for (const p of ['fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'paddingTop', 'paddingLeft', 'paddingRight', 'tabSize']) div.style[p] = cs[p];
  Object.assign(div.style, { position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap', overflowWrap: 'break-word', width: `${ta.clientWidth}px`, top: '0', left: '-9999px' });
  div.textContent = ta.value.slice(0, ta.selectionStart);
  const span = document.createElement('span'); span.textContent = '​'; div.appendChild(span);
  document.body.appendChild(div);
  const r = ta.getBoundingClientRect();
  const xy = { x: r.left + span.offsetLeft, y: r.top + span.offsetTop + parseFloat(cs.lineHeight || '20') };
  div.remove();
  return xy;
}

let suggest = null;
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
function updateSuggest(ta) {
  const before = ta.value.slice(Math.max(0, ta.selectionStart - 120), ta.selectionStart);
  const m = /!?\[\[([^\]\n|#]*)$/.exec(before);
  if (!m) { closeSuggest(); return; }
  const items = rankNotes(m[1], 12);
  if (!items.length && !m[1]) { closeSuggest(); return; }
  if (!suggest) { suggest = { el: document.createElement('div'), sel: 0 }; suggest.el.className = 'suggest'; document.body.appendChild(suggest.el); }
  suggest.items = items; suggest.query = m[1]; suggest.sel = Math.min(suggest.sel, Math.max(0, items.length - 1));
  suggest.el.innerHTML = items.length ? items.map((it, i) => `<div class="opt${i === suggest.sel ? ' sel' : ''}" data-i="${i}">${esc(it.alias || it.name)}<small>${esc(it.alias ? `${it.name} · ` : '')}${esc(dirname(it.path) || 'vault')}</small></div>`).join('')
    : `<div class="opt" style="cursor:default">No note named “${esc(m[1])}” yet<small>Finish with ]] to link it; clicking the link creates it</small></div>`;
  const { x, y } = caretXY(ta);
  suggest.el.style.left = `${Math.max(4, Math.min(x, innerWidth - 310))}px`;
  suggest.el.style.top = `${y + 260 > innerHeight ? Math.max(4, y - 290) : y + 4}px`;
  suggest.el.onmousedown = (e) => { const o = e.target.closest('[data-i]'); if (o) { e.preventDefault(); pickSuggest(ta, Number(o.dataset.i)); } };
}
function closeSuggest() { suggest?.el.remove(); suggest = null; }
function pickSuggest(ta, i) {
  const it = suggest?.items[i];
  if (!it) return closeSuggest();
  const q = suggest.query;
  const after = ta.value.slice(ta.selectionStart, ta.selectionStart + 2);
  const link = linkName(it.path) + (it.alias ? `|${it.alias}` : '');
  ta.setSelectionRange(ta.selectionStart - q.length, ta.selectionStart);
  insertText(ta, link + (after === ']]' ? '' : ']]'));
  if (after === ']]') ta.setSelectionRange(ta.selectionStart + 2, ta.selectionStart + 2);
  closeSuggest();
}
function handleSuggestKey(e, ta) {
  if (!suggest.items.length) { if (e.key === 'Escape') { closeSuggest(); return true; } return false; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    suggest.sel = (suggest.sel + (e.key === 'ArrowDown' ? 1 : -1) + suggest.items.length) % suggest.items.length;
    suggest.el.querySelectorAll('.opt').forEach((o, i) => o.classList.toggle('sel', i === suggest.sel));
    suggest.el.querySelector('.sel')?.scrollIntoView({ block: 'nearest' });
    return true;
  }
  if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickSuggest(ta, suggest.sel); return true; }
  if (e.key === 'Escape') { e.preventDefault(); closeSuggest(); return true; }
  return false;
}
view.addEventListener('focusout', (e) => { if (e.target.id === 'editor') setTimeout(closeSuggest, 150); });
view.addEventListener('scroll', closeSuggest, true);

// ---- right pane: backlinks, outgoing, outline, info -------------------------------------------
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
        ${out.length + missing.length ? '' : '<div class="hint">This note links nowhere.</div>'}</div>`;
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
        <div class="muted">${tab.path === ROOT_FILE ? 'Long-term memory: the agent reads it in every conversation.' : sectionOf(tab.path) === 'memory' ? 'Agent memory: found by memory search.' : 'Knowledge wiki: found by memory search.'}</div></div></div>`;
  }
}
$('#right-body').addEventListener('click', (e) => {
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

// ---- status bar -----------------------------------------------------------------------------
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

// ---- search & tags (left pane) -----------------------------------------------------------------
function setLeftTab(name) {
  S.leftTab = name;
  for (const n of ['files', 'search', 'tags']) $(`#left-${n}`).hidden = n !== name;
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
  if (!q) { out.innerHTML = '<div class="hint">Search every note\'s text. Use <code>tag:#name</code> for tags.</div>'; return; }
  const tagQ = /^tag:#?(\S+)$/i.exec(q);
  if (tagQ) {
    const want = tagQ[1].toLowerCase();
    const hits = [...S.notes.values()].filter((e) => (e.tags || []).some((t) => t.toLowerCase() === want || t.toLowerCase().startsWith(`${want}/`)));
    out.innerHTML = `<div class="hint">${hits.length} note${hits.length === 1 ? '' : 's'} tagged #${esc(tagQ[1])}</div>` + hits.sort((a, b) => b.mtime - a.mtime).map((e) =>
      `<div class="result" data-open="${esc(e.path)}"><div class="r-title">${esc(stem(e.path))}</div><div class="r-path">${esc(e.path)}</div></div>`).join('');
    return;
  }
  out.innerHTML = '<div class="hint">Searching…</div>';
  try {
    const data = await api('search', { q });
    if (seq !== searchSeq) return;
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const re = new RegExp(`(${terms.join('|')})`, 'gi');
    const mark = (s) => esc(s).replace(re, '<mark>$1</mark>');
    out.innerHTML = `<div class="hint">${data.total} result${data.total === 1 ? '' : 's'}${data.total > 50 ? ' (showing 50)' : ''}</div>` + data.results.map((r) =>
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

// ---- graph view -----------------------------------------------------------------------------------
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
function stopGraph() { if (S.sim) { S.sim.stop(); S.sim = null; } }

function graphData(tab) {
  const g = S.graph;
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

function drawGraph(tab) {
  stopGraph();
  const wrap = $('#graph');
  if (!wrap || !window.d3) return;
  const d3 = window.d3;
  const { nodes, links } = graphData(tab);
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  const groups = [...new Set(nodes.filter((n) => n.kind === 'note').map((n) => n.group))].sort();
  const colorOf = (n) => {
    if (n.kind === 'ghost') return v('--text-3');
    if (n.kind === 'tag') return '#8b5cf6';
    if (n.group === 'MEMORY') return v('--brand');
    return PALETTE[groups.indexOf(n.group) % PALETTE.length];
  };
  $('#g-legend').innerHTML = groups.map((gr) => `<span><i style="background:${colorOf({ kind: 'note', group: gr })}"></i>${esc(gr === 'MEMORY' ? 'MEMORY.md' : gr)}</span>`).join('');
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
    if (d.kind === 'note') openPath(d.id, { newTab: true });
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
  S.sim = sim;
}

// ---- quick switcher & command palette -----------------------------------------------------------------
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

const COMMANDS = [
  { name: 'Open quick switcher', key: 'O', fn: () => quickSwitcher() },
  { name: 'Create new note', key: 'N', fn: () => newNote() },
  { name: 'Create new folder', fn: () => newFolder() },
  { name: "Open today's daily note", fn: () => dailyNote() },
  { name: 'Open graph view', key: 'G', fn: () => openGraph() },
  { name: 'Open local graph of this note', fn: () => activeTab()?.kind === 'note' && openGraph(activeTab().path) },
  { name: 'Toggle reading / editing view', key: 'E', fn: () => setMode() },
  { name: 'Search in all notes', key: '⇧ F', fn: () => { setPane('left', true); setLeftTab('search'); $('#search-q').focus(); } },
  { name: 'Rename this note', fn: () => activeTab()?.path && renamePath(activeTab().path, { nameOnly: true }) },
  { name: 'Move this note', fn: () => activeTab()?.path && renamePath(activeTab().path) },
  { name: 'Delete this note', fn: () => activeTab()?.path && deletePath(activeTab().path) },
  { name: 'Copy link to this note', fn: () => activeTab()?.path && copy(`[[${linkName(activeTab().path)}]]`) },
  { name: 'Reveal this note in the file explorer', fn: () => activeTab()?.path && reveal(activeTab().path) },
  { name: 'Toggle left sidebar', fn: () => setPane('left') },
  { name: 'Toggle right sidebar', fn: () => setPane('right') },
  { name: 'Close this tab', fn: () => S.active >= 0 && closeTab(S.active) },
  { name: 'Reload the vault from disk', fn: () => loadIndex() },
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

// ---- panes, top bar, keyboard -----------------------------------------------------------------------
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
$('#btn-daily').onclick = () => dailyNote();
$('#btn-graph').onclick = () => openGraph();
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

// ---- agents & start-up ------------------------------------------------------------------------------
async function switchAgent(id) {
  await flushAll();
  stopGraph();
  S.agent = id;
  const saved = store.get(`lrm_vault:${id}`, {});
  S.tabs = (saved.tabs || []).filter((t) => t && (t.kind === 'graph' || t.path)).map((t) => Object.assign(newTabObj(t.kind, t.path, t.mode), t.kind === 'graph' ? { center: null } : {}));
  S.active = Math.min(saved.active ?? -1, S.tabs.length - 1);
  if (S.active < 0 && S.tabs.length) S.active = 0;
  S.expanded = new Set(saved.expanded || SECTIONS);
  S.folder = saved.folder || 'knowledge';
  Object.assign(S.graph, saved.graph || {}, { filter: '' });
  saveState();
  renderTabs();
  await loadIndex();
  // Tabs whose note is gone are dropped; the rest load their text.
  S.tabs = S.tabs.filter((t) => t.kind === 'graph' || S.lc.has(t.path.toLowerCase()));
  S.active = Math.min(Math.max(S.active, S.tabs.length ? 0 : -1), S.tabs.length - 1);
  renderTabs(); renderTree(); renderView();
  for (const t of S.tabs) if (t.kind === 'note') loadTab(t);
}

async function start() {
  if (small()) { $('#body').classList.add('no-left', 'no-right'); $('#toggle-left').setAttribute('aria-pressed', 'false'); $('#toggle-right').setAttribute('aria-pressed', 'false'); } else {
    if (ui.leftOpen === false) setPane('left', false);
    if (ui.rightOpen === false) setPane('right', false);
  }
  S.leftTab = ui.leftTab || 'files'; S.rightTab = ui.rightTab || 'links';
  setLeftTab(S.leftTab);
  let data;
  try { data = await api('agents'); } catch (e) { $('#view').innerHTML = `<div class="empty-state"><h2>Can't open the vault</h2><p>${esc(e.message)}</p></div>`; return; }
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
  // Agents write to their memory all the time: pick up their changes while the page is open.
  setInterval(() => { if (document.visibilityState === 'visible' && !S.tabs.some((t) => t.saving)) loadIndex({ quiet: true }); }, 15000);
}
start();
