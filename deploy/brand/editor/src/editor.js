// The Vault's editor: CodeMirror 6 with an Obsidian-style live preview.
// Markdown syntax is hidden on every line except the one being edited; headings,
// emphasis, links, [[wikilinks]], tags, highlights, checkboxes, images, rules,
// Mermaid diagrams and math are shown as they will read. Built into
// ../vendor/lrm-editor.js (npm run build); the page passes callbacks for what
// depends on the vault (link suggestions, opening links, file URLs, rendering).

import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { markdown, markdownKeymap, markdownLanguage } from '@codemirror/lang-markdown';
import { HighlightStyle, syntaxHighlighting, syntaxTree } from '@codemirror/language';
import { highlightSelectionMatches, openSearchPanel, search, searchKeymap } from '@codemirror/search';
import { Compartment, EditorSelection, EditorState, RangeSetBuilder, StateField } from '@codemirror/state';
import { Decoration, EditorView, ViewPlugin, WidgetType, drawSelection, keymap, placeholder } from '@codemirror/view';
import { tags as t } from '@lezer/highlight';

const WIKI = /(!?)\[\[([^\]\n]+?)\]\]/g;
const TAG = /(^|[\s(])#([\p{L}_][\p{L}\p{N}_/-]*)/gu;
const MARK = /==([^=\n](?:[^\n]*?[^=\n])?)==/g;
const INLINE_MATH = /(^|[^\\$])\$([^\s$](?:[^$\n]*?[^\s$])?)\$(?!\d)/g;

// Widgets

class Checkbox extends WidgetType {
  constructor(checked, pos) { super(); this.checked = checked; this.pos = pos; }
  eq(o) { return o.checked === this.checked && o.pos === this.pos; }
  toDOM(view) {
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'cm-lrm-task';
    box.checked = this.checked;
    box.addEventListener('mousedown', (e) => {
      e.preventDefault();
      view.dispatch({ changes: { from: this.pos, to: this.pos + 1, insert: this.checked ? ' ' : 'x' } });
    });
    return box;
  }
  ignoreEvent() { return false; }
}

class Bullet extends WidgetType {
  eq() { return true; }
  toDOM() { const s = document.createElement('span'); s.className = 'cm-lrm-bullet'; s.textContent = '•'; return s; }
}

class Rule extends WidgetType {
  eq() { return true; }
  toDOM() { const s = document.createElement('span'); s.className = 'cm-lrm-hr'; return s; }
}

class Img extends WidgetType {
  constructor(src, alt, width) { super(); this.src = src; this.alt = alt; this.width = width; }
  eq(o) { return o.src === this.src && o.width === this.width; }
  toDOM() {
    const img = document.createElement('img');
    img.className = 'cm-lrm-img';
    img.src = this.src;
    img.alt = this.alt || '';
    if (this.width) img.style.width = `${this.width}px`;
    return img;
  }
}

class Rendered extends WidgetType {
  // Mermaid diagrams and math, drawn by the page's renderer.
  constructor(kind, source, render, block) { super(); this.kind = kind; this.source = source; this.render = render; this.block = block; }
  eq(o) { return o.kind === this.kind && o.source === this.source; }
  toDOM() {
    const el = document.createElement(this.block ? 'div' : 'span');
    el.className = `cm-lrm-rendered cm-lrm-${this.kind}`;
    try { this.render?.(this.kind, this.source, el); } catch (e) { el.textContent = this.source; }
    return el;
  }
  ignoreEvent() { return false; }
}

// Inline live preview (view plugin)

function activeLines(state) {
  const lines = new Set();
  for (const r of state.selection.ranges) {
    const a = state.doc.lineAt(r.from).number; const b = state.doc.lineAt(r.to).number;
    for (let n = a; n <= b; n++) lines.add(n);
  }
  return lines;
}

const hide = Decoration.replace({});
const HEADING = { ATXHeading1: 1, ATXHeading2: 2, ATXHeading3: 3, ATXHeading4: 4, ATXHeading5: 5, ATXHeading6: 6 };

function buildInline(view, opts) {
  const { state } = view;
  const active = activeLines(state);
  const ranges = [];
  const add = (from, to, deco) => { if (from < to || deco.point) ranges.push(deco.range(from, to)); };
  const lineOf = (pos) => state.doc.lineAt(pos).number;
  const inCode = [];

  for (const { from, to } of view.visibleRanges) {
    syntaxTree(state).iterate({
      from, to,
      enter: (node) => {
        const name = node.name;
        const line = state.doc.lineAt(node.from);
        const isActive = active.has(line.number);
        if (HEADING[name]) {
          ranges.push(Decoration.line({ class: `cm-lrm-h cm-lrm-h${HEADING[name]}` }).range(line.from));
        }
        if (name === 'FencedCode' || name === 'CodeBlock') {
          inCode.push([node.from, node.to]);
          for (let n = lineOf(node.from); n <= lineOf(node.to); n++) {
            ranges.push(Decoration.line({ class: 'cm-lrm-codeblock' }).range(state.doc.line(n).from));
          }
          return false;
        }
        if (name === 'Blockquote') {
          for (let n = lineOf(node.from); n <= lineOf(node.to); n++) {
            const text = state.doc.line(n).text;
            const callout = n === lineOf(node.from) && /^\s*>\s*\[!\w+\]/.test(text);
            ranges.push(Decoration.line({ class: `cm-lrm-quote${callout ? ' cm-lrm-callout-title' : ''}` }).range(state.doc.line(n).from));
          }
        }
        if (name === 'InlineCode') add(node.from, node.to, Decoration.mark({ class: 'cm-lrm-code' }));
        if (name === 'StrongEmphasis') add(node.from, node.to, Decoration.mark({ class: 'cm-lrm-strong' }));
        if (name === 'Emphasis') add(node.from, node.to, Decoration.mark({ class: 'cm-lrm-em' }));
        if (name === 'Strikethrough') add(node.from, node.to, Decoration.mark({ class: 'cm-lrm-strike' }));
        if (!opts.live || isActive) return;
        // Syntax hidden away from the cursor.
        if (name === 'HeaderMark') add(node.from, Math.min(node.to + 1, line.to), hide);
        else if (name === 'EmphasisMark' || name === 'CodeMark' || name === 'StrikethroughMark') {
          if (node.node.parent?.name !== 'FencedCode') add(node.from, node.to, hide);
        } else if (name === 'QuoteMark') add(node.from, Math.min(node.to + 1, line.to), hide);
        else if (name === 'HorizontalRule') add(node.from, node.to, Decoration.replace({ widget: new Rule() }));
        else if (name === 'ListMark' && /^[-*+]$/.test(state.sliceDoc(node.from, node.to))) {
          const next = node.node.nextSibling;
          if (!(next && next.name === 'Task')) add(node.from, node.to, Decoration.replace({ widget: new Bullet() }));
          else add(node.from, Math.min(node.to + 1, line.to), hide);
        } else if (name === 'TaskMarker') {
          const checked = /x/i.test(state.sliceDoc(node.from, node.to));
          add(node.from, node.to, Decoration.replace({ widget: new Checkbox(checked, node.from + 1) }));
          if (checked) add(node.to, line.to, Decoration.mark({ class: 'cm-lrm-done' }));
        } else if (name === 'Image') {
          const text = state.sliceDoc(node.from, node.to);
          const m = /^!\[([^\]]*)\]\(([^)\s]+)/.exec(text);
          if (m) {
            const src = opts.fileUrl?.(m[2]);
            if (src) add(node.from, node.to, Decoration.replace({ widget: new Img(src, m[1]) }));
          }
          return false;
        } else if (name === 'Link') {
          const text = state.sliceDoc(node.from, node.to);
          const m = /^\[([^\]]*)\]\(([^)]*)\)$/.exec(text);
          if (m) {
            add(node.from, node.from + 1, hide);
            add(node.from + 1, node.from + 1 + m[1].length, Decoration.mark({ class: 'cm-lrm-link', attributes: { 'data-href': m[2], 'data-md': '1' } }));
            add(node.from + 1 + m[1].length, node.to, hide);
          }
          return false;
        }
      },
    });
  }

  // Patterns the Markdown grammar does not know: [[wikilinks]], #tags, ==highlights==, $math$.
  const skip = (pos) => inCode.some(([a, b]) => pos >= a && pos <= b);
  for (const { from, to } of view.visibleRanges) {
    const startLine = state.doc.lineAt(from).number; const endLine = state.doc.lineAt(to).number;
    for (let n = startLine; n <= endLine; n++) {
      const line = state.doc.line(n);
      const isActive = active.has(n);
      const text = line.text;
      if (skip(line.from)) continue;
      for (const m of text.matchAll(WIKI)) {
        const a = line.from + m.index; const b = a + m[0].length;
        const inner = m[2]; const [target, alias] = inner.split('|');
        const embed = m[1] === '!';
        const resolved = opts.resolve?.(target.split('#')[0].trim());
        if (embed && !isActive && opts.live && resolved && /\.(png|jpe?g|gif|webp|svg)$/i.test(resolved)) {
          const width = /^\d+$/.test(alias || '') ? Number(alias) : 0;
          add(a, b, Decoration.replace({ widget: new Img(opts.fileUrl(resolved, true), target, width) }));
          continue;
        }
        const cls = `cm-lrm-link cm-lrm-wiki${resolved ? '' : ' cm-lrm-unresolved'}`;
        const attrs = { 'data-href': target.trim() };
        if (!opts.live || isActive) { add(a, b, Decoration.mark({ class: cls, attributes: attrs })); continue; }
        const shown = alias ? inner.indexOf('|') + 1 : 0;
        add(a, a + 2 + m[1].length + shown, hide);
        add(a + 2 + m[1].length + shown, b - 2, Decoration.mark({ class: cls, attributes: attrs }));
        add(b - 2, b, hide);
      }
      for (const m of text.matchAll(TAG)) {
        const a = line.from + m.index + m[1].length;
        if (/^#{1,6}\s/.test(text) && m.index === 0) continue;
        add(a, a + 1 + m[2].length, Decoration.mark({ class: 'cm-lrm-tag', attributes: { 'data-tag': m[2] } }));
      }
      for (const m of text.matchAll(MARK)) {
        const a = line.from + m.index; const b = a + m[0].length;
        if (opts.live && !isActive) { add(a, a + 2, hide); add(b - 2, b, hide); }
        add(a + (opts.live && !isActive ? 2 : 0), b - (opts.live && !isActive ? 2 : 0), Decoration.mark({ class: 'cm-lrm-mark' }));
      }
      if (opts.live && !isActive && opts.render && text.includes('$') && !/^\s*\$\$/.test(text)) {
        for (const m of text.matchAll(INLINE_MATH)) {
          const a = line.from + m.index + m[1].length; const b = a + m[2].length + 2;
          add(a, b, Decoration.replace({ widget: new Rendered('math', m[2], opts.render, false) }));
        }
      }
    }
  }
  ranges.sort((x, y) => x.from - y.from || x.value.startSide - y.value.startSide);
  // Drop overlaps (a replaced range inside another one), which CodeMirror refuses.
  const out = []; let end = -1;
  for (const r of ranges) {
    const replacing = r.value.spec?.widget || r.value === hide;
    if (replacing && r.from < end) continue;
    out.push(r);
    if (replacing) end = Math.max(end, r.to);
  }
  try { return Decoration.set(out, true); } catch { return Decoration.none; }
}

function inlinePreview(opts) {
  return ViewPlugin.fromClass(class {
    constructor(view) { this.decorations = buildInline(view, opts.get()); }
    update(u) {
      if (u.docChanged || u.viewportChanged || u.selectionSet || u.transactions.some((tr) => tr.reconfigured)) {
        this.decorations = buildInline(u.view, opts.get());
      }
    }
  }, { decorations: (v) => v.decorations });
}

// Block live preview: Mermaid diagrams and $$math$$ (state field: block widgets)

function buildBlocks(state, opts) {
  const o = opts.get();
  if (!o.live || !o.render) return Decoration.none;
  const builder = new RangeSetBuilder();
  const active = activeLines(state);
  const touches = (a, b) => { for (let n = state.doc.lineAt(a).number; n <= state.doc.lineAt(b).number; n++) if (active.has(n)) return true; return false; };
  const found = [];
  syntaxTree(state).iterate({
    enter: (node) => {
      if (node.name !== 'FencedCode') return;
      const info = node.node.getChild('CodeInfo');
      if (!info || state.sliceDoc(info.from, info.to).trim().toLowerCase() !== 'mermaid') return false;
      const text = node.node.getChild('CodeText');
      if (!touches(node.from, node.to)) found.push([node.from, node.to, 'mermaid', text ? state.sliceDoc(text.from, text.to) : '']);
      return false;
    },
  });
  // $$ … $$ blocks, on their own lines.
  for (let n = 1; n <= state.doc.lines; n++) {
    const line = state.doc.line(n);
    if (!/^\s*\$\$/.test(line.text)) continue;
    const oneLine = /^\s*\$\$(.+)\$\$\s*$/.exec(line.text);
    if (oneLine) { if (!touches(line.from, line.to)) found.push([line.from, line.to, 'mathblock', oneLine[1]]); continue; }
    let m = n + 1;
    while (m <= state.doc.lines && !/\$\$\s*$/.test(state.doc.line(m).text)) m++;
    if (m > state.doc.lines) break;
    const end = state.doc.line(m);
    const tex = state.sliceDoc(line.from, end.to).replace(/^\s*\$\$/, '').replace(/\$\$\s*$/, '');
    if (!touches(line.from, end.to)) found.push([line.from, end.to, 'mathblock', tex]);
    n = m;
  }
  found.sort((a, b) => a[0] - b[0]);
  let last = -1;
  for (const [from, to, kind, src] of found) {
    if (from < last) continue;
    builder.add(from, to, Decoration.replace({ widget: new Rendered(kind, src, o.render, true), block: true }));
    last = to;
  }
  return builder.finish();
}

function blockPreview(opts) {
  return StateField.define({
    create: (state) => buildBlocks(state, opts),
    update: (deco, tr) => (tr.docChanged || tr.selection || tr.reconfigured ? buildBlocks(tr.state, opts) : deco.map(tr.changes)),
    provide: (f) => EditorView.decorations.from(f),
  });
}

// Look

const highlight = HighlightStyle.define([
  { tag: t.heading, fontWeight: '700' },
  { tag: t.strong, fontWeight: '700' },
  { tag: t.emphasis, fontStyle: 'italic' },
  { tag: t.strikethrough, textDecoration: 'line-through' },
  { tag: t.link, color: 'var(--link)' },
  { tag: t.url, color: 'var(--text-3)' },
  { tag: t.monospace, fontFamily: 'var(--mono)' },
  { tag: t.quote, color: 'var(--text-2)' },
  { tag: [t.processingInstruction, t.meta, t.contentSeparator], color: 'var(--text-3)' },
]);

const theme = EditorView.theme({
  '&': { fontSize: '15px', color: 'var(--text)', background: 'transparent' },
  '&.cm-focused': { outline: 'none' },
  '.cm-content': { fontFamily: "Inter, system-ui, sans-serif", lineHeight: '1.7', padding: '0 0 30vh', caretColor: 'var(--text)' },
  '.cm-scroller': { overflow: 'visible' },
  '.cm-line': { padding: '0' },
  '.cm-cursor': { borderLeftColor: 'var(--text)' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { background: 'var(--active) !important' },
  '.cm-placeholder': { color: 'var(--text-3)' },
  '.cm-panels': { background: 'var(--surface-2)', color: 'var(--text)', borderBottom: '1px solid var(--line)' },
  '.cm-panels.cm-panels-top': { position: 'sticky', top: '0' },
  '.cm-search': { fontFamily: 'Inter, system-ui, sans-serif', fontSize: '13px', padding: '6px 8px' },
  '.cm-search input, .cm-search button': { fontFamily: 'inherit', fontSize: '13px', borderRadius: '6px', border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text)' },
  '.cm-search label': { fontSize: '12px' },
  '.cm-tooltip': { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: '10px', boxShadow: 'var(--shadow)', overflow: 'hidden' },
  '.cm-tooltip-autocomplete > ul > li': { padding: '4px 10px', fontFamily: 'Inter, system-ui, sans-serif' },
  '.cm-tooltip-autocomplete > ul > li[aria-selected]': { background: 'var(--active)', color: 'var(--text)' },
  '.cm-completionDetail': { color: 'var(--text-3)', fontStyle: 'normal', marginLeft: '8px', fontSize: '12px' },
  '.cm-lrm-h': { fontWeight: '750', lineHeight: '1.35' },
  '.cm-lrm-h1': { fontSize: '1.75em', paddingTop: '.4em' }, '.cm-lrm-h2': { fontSize: '1.45em', paddingTop: '.35em' },
  '.cm-lrm-h3': { fontSize: '1.2em', paddingTop: '.25em' }, '.cm-lrm-h4': { fontSize: '1.05em' },
  '.cm-lrm-strong': { fontWeight: '700' }, '.cm-lrm-em': { fontStyle: 'italic' }, '.cm-lrm-strike': { textDecoration: 'line-through' },
  '.cm-lrm-code': { fontFamily: 'var(--mono)', fontSize: '13px', background: 'var(--code-bg)', borderRadius: '4px', padding: '1px 3px' },
  '.cm-lrm-codeblock': { fontFamily: 'var(--mono)', fontSize: '13px', background: 'var(--code-bg)', padding: '0 12px !important' },
  '.cm-lrm-quote': { borderLeft: '3px solid var(--brand)', paddingLeft: '14px !important', color: 'var(--text-2)' },
  '.cm-lrm-callout-title': { fontWeight: '700' },
  '.cm-lrm-link': { color: 'var(--link)', cursor: 'pointer', fontWeight: '550' },
  '.cm-lrm-unresolved': { color: 'var(--unresolved)' },
  '.cm-lrm-tag': { color: 'var(--brand)', background: 'var(--brand-soft)', borderRadius: '999px', padding: '1px 7px', fontSize: '.88em', fontWeight: '600', cursor: 'pointer' },
  '.cm-lrm-mark': { background: 'var(--mark)', borderRadius: '3px' },
  '.cm-lrm-task': { margin: '0 6px 0 0', verticalAlign: 'middle', accentColor: 'var(--brand)', cursor: 'pointer' },
  '.cm-lrm-done': { color: 'var(--text-3)', textDecoration: 'line-through' },
  '.cm-lrm-bullet': { color: 'var(--text-3)', fontWeight: '700', padding: '0 4px' },
  '.cm-lrm-hr': { display: 'inline-block', width: '100%', borderTop: '1px solid var(--line)', verticalAlign: 'middle' },
  '.cm-lrm-img': { maxWidth: '100%', borderRadius: '8px', display: 'block', margin: '4px 0' },
  '.cm-lrm-rendered': { cursor: 'text' },
  '.cm-lrm-mermaid, .cm-lrm-mathblock': { display: 'block', textAlign: 'center', padding: '8px 0' },
});

// The editor

export function create(parent, cfg) {
  let current = { live: cfg.livePreview !== false, resolve: cfg.resolve, fileUrl: cfg.fileUrl, render: cfg.render };
  const opts = { get: () => current };
  const preview = new Compartment();
  const previewExt = () => [inlinePreview(opts), blockPreview(opts)];

  const linkSource = (ctx) => {
    const m = ctx.matchBefore(/!?\[\[[^\]\n|#]*$/);
    if (!m) return null;
    const start = m.from + m.text.indexOf('[[') + 2;
    const query = ctx.state.sliceDoc(start, ctx.pos);
    const items = cfg.suggest?.(query) || [];
    // Insert the name and step past the closing ]] (typed by closeBrackets or already there).
    const apply = (insert) => (view, _c, from, to) => {
      const closing = view.state.sliceDoc(to, to + 2) === ']]';
      const text = insert + (closing ? '' : ']]');
      view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + insert.length + 2 } });
    };
    return {
      from: start, filter: false,
      options: items.map((it) => ({ label: it.label, detail: it.detail, apply: apply(it.insert) })),
    };
  };
  const tagSource = (ctx) => {
    const m = ctx.matchBefore(/(^|\s)#[\p{L}\p{N}_/-]*$/u);
    if (!m || !cfg.tags) return null;
    const hash = m.from + m.text.indexOf('#');
    const query = ctx.state.sliceDoc(hash + 1, ctx.pos).toLowerCase();
    const options = cfg.tags().filter((tg) => tg.toLowerCase().startsWith(query)).slice(0, 30).map((tg) => ({ label: `#${tg}`, apply: `#${tg}` }));
    return options.length ? { from: hash, options, filter: false } : null;
  };

  const fileHandler = async (files, view, pos) => {
    if (!files?.length || !cfg.onFiles) return false;
    const text = await cfg.onFiles([...files]);
    if (text) view.dispatch({ changes: { from: pos, insert: text }, selection: { anchor: pos + text.length } });
    return true;
  };

  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: cfg.doc || '',
      extensions: [
        history(),
        drawSelection(),
        EditorView.lineWrapping,
        EditorState.allowMultipleSelections.of(true),
        closeBrackets(),
        markdown({ base: markdownLanguage, addKeymap: true }),
        syntaxHighlighting(highlight),
        search({ top: true }),
        highlightSelectionMatches(),
        autocompletion({ override: [linkSource, tagSource], activateOnTyping: true, icons: false }),
        placeholder(cfg.placeholder || ''),
        preview.of(previewExt()),
        theme,
        EditorState.readOnly.of(Boolean(cfg.readOnly)),
        keymap.of([
          { key: 'Mod-s', run: () => { cfg.onSave?.(); return true; } },
          { key: 'Mod-h', run: openSearchPanel },
          indentWithTab,
          ...closeBracketsKeymap, ...markdownKeymap, ...completionKeymap, ...searchKeymap, ...historyKeymap, ...defaultKeymap,
        ]),
        EditorView.updateListener.of((u) => { if (u.docChanged) cfg.onChange?.(u.state.doc.toString()); }),
        EditorView.domEventHandlers({
          mousedown: (e, v) => {
            const link = e.target.closest?.('.cm-lrm-link');
            if (link && (current.live || e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              cfg.openLink?.(link.dataset.href, { md: link.dataset.md === '1', newTab: e.ctrlKey || e.metaKey });
              return true;
            }
            const tag = e.target.closest?.('.cm-lrm-tag');
            if (tag && (e.ctrlKey || e.metaKey)) { e.preventDefault(); cfg.openTag?.(tag.dataset.tag); return true; }
            return false;
          },
          paste: (e, v) => {
            const files = e.clipboardData?.files;
            if (files?.length && cfg.onFiles) { e.preventDefault(); fileHandler(files, v, v.state.selection.main.head); return true; }
            return false;
          },
          drop: (e, v) => {
            const files = e.dataTransfer?.files;
            if (files?.length && cfg.onFiles) {
              e.preventDefault();
              const pos = v.posAtCoords({ x: e.clientX, y: e.clientY }) ?? v.state.selection.main.head;
              fileHandler(files, v, pos);
              return true;
            }
            return false;
          },
        }),
      ],
    }),
  });

  return {
    view,
    getDoc: () => view.state.doc.toString(),
    setDoc(text) {
      // Keep the cursor where it was when the agent changes the note underneath.
      const sel = view.state.selection.main;
      const len = text.length;
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text },
        selection: EditorSelection.single(Math.min(sel.anchor, len), Math.min(sel.head, len)) });
    },
    insert(text) {
      const { from, to } = view.state.selection.main;
      view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
      view.focus();
    },
    focus: () => view.focus(),
    gotoLine(n) {
      const line = view.state.doc.line(Math.max(1, Math.min(n, view.state.doc.lines)));
      view.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: 'center' }) });
      view.focus();
    },
    setLivePreview(on) { current = { ...current, live: on }; view.dispatch({ effects: preview.reconfigure(previewExt()) }); },
    refresh() { view.dispatch({ effects: preview.reconfigure(previewExt()) }); },
    find() { openSearchPanel(view); },
    destroy: () => view.destroy(),
  };
}
