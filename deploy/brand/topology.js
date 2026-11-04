// Topologies of Thoughts: the Vault's notes as a full-screen field of thoughts.
// Four layouts (centralized, clusters, distributed, chain), a HUD that names what you
// point at, and three ways to steer it: mouse, touch, and your hand through the camera
// (MediaPipe hand tracking, served from this server; video never leaves the browser).
//
//   window.LrmTopology.open(host, { nodes, links, focus, dark, mode, onOpen, onMode, onClose })
//   -> { destroy() }
(() => {
  const MP = '/assets/brand/vendor/mediapipe';
  const MODES = {
    centralized: { name: 'centralized', text: 'one central thought connected to all other nodes', key: '1' },
    clusters: { name: 'clustered', text: 'thoughts gathered by folder, with bridges between groups', key: '2' },
    distributed: { name: 'distributed', text: 'a network of thoughts with no single centre', key: '3' },
    chain: { name: 'chain', text: 'thoughts in the order they were written', key: '4' },
  };
  const ORDER = Object.keys(MODES);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const CSS = `
  .topo { position: fixed; inset: 0; z-index: 9000; overflow: hidden; font-family: Inter, system-ui, sans-serif; user-select: none;
    --bg: #f6f6f3; --fg: 17,17,20; --muted: rgba(17,17,20,.55); --line: rgba(17,17,20,.14); --hud: #111114; --accent: #e5322d; --panel: rgba(255,255,255,.82); }
  .topo.dark { --bg: #050607; --fg: 255,255,255; --muted: rgba(255,255,255,.6); --line: rgba(255,255,255,.16); --hud: #f2f2f2; --panel: rgba(12,13,15,.72); }
  .topo { background: var(--bg); color: var(--hud); }
  .topo video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; transform: scaleX(-1); opacity: 0; transition: opacity .4s; filter: grayscale(1) contrast(1.05); }
  .topo.cam video { opacity: .22; }
  .topo.dark.cam video { opacity: .38; }
  .topo canvas.field { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: grab; }
  .topo canvas.field.drag { cursor: grabbing; }
  .topo .hud-title { position: absolute; top: 28px; right: 36px; text-align: left; width: 260px; pointer-events: none; }
  .topo .hud-title h2 { font-weight: 300; font-size: 26px; line-height: 1.15; margin: 0 0 14px; letter-spacing: .2px; }
  .topo .hud-title canvas { width: 88px; height: 88px; display: block; margin: 0 0 8px 40px; }
  .topo .hud-title .mode { font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); }
  .topo .hud-title .mode b { color: var(--hud); font-weight: 500; }
  .topo .hud-title .mode i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--hud); margin-right: 6px; }
  .topo .bar { position: absolute; left: 50%; bottom: 22px; transform: translateX(-50%); display: flex; gap: 6px; align-items: center; background: var(--panel);
    border: 1px solid var(--line); border-radius: 14px; padding: 6px; backdrop-filter: blur(8px); flex-wrap: wrap; justify-content: center; max-width: calc(100% - 24px); }
  .topo .bar button { border: 0; background: transparent; color: var(--hud); font: 500 12.5px Inter, system-ui, sans-serif; padding: 7px 11px; border-radius: 9px; cursor: pointer; display: inline-flex; gap: 7px; align-items: center; }
  .topo .bar button:hover { background: rgba(var(--fg), .07); }
  .topo .bar button[aria-pressed="true"] { background: rgba(var(--fg), .12); }
  .topo .bar kbd { font: 10px ui-monospace, monospace; opacity: .55; }
  .topo .bar .sep { width: 1px; height: 22px; background: var(--line); margin: 0 2px; }
  .topo .close { position: absolute; top: 22px; left: 22px; width: 38px; height: 38px; border-radius: 50%; border: 1px solid var(--line); background: var(--panel); color: var(--hud); cursor: pointer; font-size: 16px; }
  .topo .help { position: absolute; left: 22px; bottom: 80px; width: 270px; background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px;
    font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); backdrop-filter: blur(8px); }
  .topo .help b { color: var(--hud); font-weight: 500; }
  .topo .help .st { margin-top: 8px; color: var(--hud); }
  .topo .count { position: absolute; top: 30px; left: 76px; font: 12px ui-monospace, monospace; color: var(--muted); pointer-events: none; }
  @media (max-width: 720px) { .topo .hud-title { right: 16px; top: 76px; width: 200px; } .topo .hud-title h2 { font-size: 20px; } .topo .help { display: none; } }
  `;

  function open(host, opts) {
    if (!document.getElementById('topo-css')) {
      const st = document.createElement('style'); st.id = 'topo-css'; st.textContent = CSS; document.head.appendChild(st);
    }
    const d3 = window.d3;
    const el = document.createElement('div');
    el.className = `topo${opts.dark ? ' dark' : ''}`;
    el.innerHTML = `<video playsinline muted></video><canvas class="field"></canvas>
      <button class="close" title="Close (Esc)" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
      <div class="count"></div>
      <div class="hud-title"><h2>Topologies of<br>Thoughts</h2><canvas class="glyph" width="176" height="176"></canvas><div class="mode"></div></div>
      <div class="help" hidden></div>
      <div class="bar">${ORDER.map((m) => `<button data-mode="${m}">${esc(MODES[m].name)} <kbd>${MODES[m].key}</kbd></button>`).join('')}
        <span class="sep"></span><button data-act="cam" title="Steer with your hand through the camera"><i class="fa-solid fa-hand"></i>Hand gestures</button>
        <button data-act="theme" title="Light / dark"><i class="fa-solid fa-circle-half-stroke"></i></button>
        <button data-act="fit" title="Fit to screen (F)"><i class="fa-solid fa-expand"></i></button></div>`;
    host.appendChild(el);
    const canvas = el.querySelector('canvas.field');
    const ctx = canvas.getContext('2d');
    const video = el.querySelector('video');
    const help = el.querySelector('.help');

    // ---- data -------------------------------------------------------------------------
    const nodes = opts.nodes.map((n) => ({ ...n }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const links = opts.links.filter((l) => byId.has(l.source) && byId.has(l.target)).map((l) => ({ source: byId.get(l.source), target: byId.get(l.target) }));
    const near = new Map(nodes.map((n) => [n, new Set()]));
    links.forEach((l) => { near.get(l.source).add(l.target); near.get(l.target).add(l.source); });
    const groups = [...new Set(nodes.map((n) => n.group))].sort();
    let focus = byId.get(opts.focus) || [...nodes].sort((a, b) => b.deg - a.deg)[0] || null;
    let mode = MODES[opts.mode] ? opts.mode : 'centralized';
    el.querySelector('.count').textContent = `${nodes.length} thoughts · ${links.length} links`;

    // ---- view -------------------------------------------------------------------------
    let W = 0; let H = 0; const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const view = { x: 0, y: 0, k: 1 };
    function resize() {
      W = el.clientWidth; H = el.clientHeight;
      canvas.width = W * dpr; canvas.height = H * dpr;
    }
    resize();
    const toWorld = (sx, sy) => ({ x: (sx - W / 2 - view.x) / view.k, y: (sy - H / 2 - view.y) / view.k });
    const toScreen = (n) => ({ x: W / 2 + view.x + n.x * view.k, y: H / 2 + view.y + n.y * view.k });
    const radius = (n) => 1.4 + Math.sqrt(n.deg) * 0.9;

    // ---- layouts ----------------------------------------------------------------------
    const R = Math.max(120, Math.min(420, 22 * Math.sqrt(nodes.length)));
    const sim = d3.forceSimulation(nodes).alphaDecay(0.03).velocityDecay(0.35).stop();
    function depths() {
      const depth = new Map();
      if (!focus) return depth;
      depth.set(focus, 0);
      let frontier = [focus];
      for (let d = 1; frontier.length; d++) {
        const next = [];
        for (const n of frontier) for (const m of near.get(n)) if (!depth.has(m)) { depth.set(m, d); next.push(m); }
        frontier = next;
      }
      return depth;
    }
    function layout(next) {
      mode = next;
      for (const n of nodes) { n.fx = null; n.fy = null; }
      sim.force('link', null).force('charge', null).force('x', null).force('y', null).force('r', null).force('collide', null);
      const collide = d3.forceCollide((n) => radius(n) + 1.5);
      if (mode === 'centralized') {
        const depth = depths();
        const max = Math.max(1, ...depth.values());
        const ring = (n) => (n === focus ? 0 : depth.has(n) ? (depth.get(n) / (max + 1)) * R * 1.15 + R * 0.18 : R * 1.35);
        if (focus) { focus.fx = 0; focus.fy = 0; }
        sim.force('r', d3.forceRadial(ring, 0, 0).strength(0.9))
          .force('link', d3.forceLink(links).distance(30).strength(0.04))
          .force('charge', d3.forceManyBody().strength(-14)).force('collide', collide);
      } else if (mode === 'clusters') {
        const at = new Map(groups.map((g, i) => {
          const a = (i / Math.max(1, groups.length)) * Math.PI * 2;
          return [g, { x: Math.cos(a) * R * 0.9, y: Math.sin(a) * R * 0.9 }];
        }));
        sim.force('x', d3.forceX((n) => at.get(n.group).x).strength(0.22)).force('y', d3.forceY((n) => at.get(n.group).y).strength(0.22))
          .force('link', d3.forceLink(links).distance(26).strength(0.03))
          .force('charge', d3.forceManyBody().strength(-18)).force('collide', collide);
      } else if (mode === 'distributed') {
        sim.force('link', d3.forceLink(links).distance(34).strength(0.5))
          .force('charge', d3.forceManyBody().strength(nodes.length > 600 ? -26 : -60))
          .force('x', d3.forceX(0).strength(0.04)).force('y', d3.forceY(0).strength(0.04)).force('collide', collide);
      } else {
        const sorted = [...nodes].sort((a, b) => (a.mtime || 0) - (b.mtime || 0) || a.label.localeCompare(b.label));
        const step = Math.max(6, Math.min(26, (R * 3.2) / Math.max(1, sorted.length)));
        const perRow = Math.max(12, Math.floor((R * 3.2) / step));
        sorted.forEach((n, i) => {
          const row = Math.floor(i / perRow); const col = i % perRow;
          const x = (row % 2 ? perRow - 1 - col : col) * step - ((perRow - 1) * step) / 2;
          n.tx = x; n.ty = row * 46 - (Math.floor(sorted.length / perRow) * 46) / 2 + Math.sin(col / 3) * 8;
        });
        sim.force('x', d3.forceX((n) => n.tx).strength(0.5)).force('y', d3.forceY((n) => n.ty).strength(0.5)).force('collide', collide);
      }
      sim.alpha(1).restart();
      el.querySelectorAll('[data-mode]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
      el.querySelector('.hud-title .mode').innerHTML = `<i></i>mode: <b>${esc(MODES[mode].name)}</b><br>${esc(MODES[mode].text)}${focus && mode === 'centralized' ? `<br>centre: <b>${esc(focus.label)}</b>` : ''}`;
      drawGlyph();
      opts.onMode?.(mode);
    }
    // nodes start scattered like stars
    nodes.forEach((n, i) => { const a = i * 2.399963; const r = Math.sqrt(i + 1) * 9; n.x = Math.cos(a) * r; n.y = Math.sin(a) * r; });

    // The little drawing under the title: what the current topology looks like.
    function drawGlyph() {
      const g = el.querySelector('canvas.glyph').getContext('2d');
      const c = getComputedStyle(el).getPropertyValue('--hud').trim() || '#111';
      g.clearRect(0, 0, 176, 176); g.strokeStyle = c; g.fillStyle = c; g.lineWidth = 1.6;
      const dot = (x, y, r = 4) => { g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill(); };
      const line = (a, b) => { g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke(); };
      const ring = (n, r, cx = 88, cy = 88, off = 0) => Array.from({ length: n }, (_, i) => [cx + Math.cos(off + (i / n) * Math.PI * 2) * r, cy + Math.sin(off + (i / n) * Math.PI * 2) * r]);
      if (mode === 'centralized') { const pts = ring(14, 66); pts.forEach((p) => { line([88, 88], p); dot(...p, 3.5); }); dot(88, 88, 6); }
      if (mode === 'clusters') {
        [[52, 60], [124, 56], [90, 128]].forEach(([cx, cy]) => { const pts = ring(6, 20, cx, cy); pts.forEach((p) => { line([cx, cy], p); dot(...p, 3); }); dot(cx, cy, 4.5); });
        line([52, 60], [124, 56]); line([124, 56], [90, 128]);
      }
      if (mode === 'distributed') {
        const pts = [[30, 40], [78, 26], [132, 44], [150, 96], [118, 140], [64, 148], [26, 104], [70, 82], [110, 92]];
        [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [6, 0], [7, 0], [7, 5], [7, 8], [8, 2], [8, 4], [1, 7], [3, 8]].forEach(([a, b]) => line(pts[a], pts[b]));
        pts.forEach((p) => dot(...p, 3.5));
      }
      if (mode === 'chain') { const pts = Array.from({ length: 9 }, (_, i) => [16 + i * 18, 88 + Math.sin(i * 0.9) * 26]); pts.slice(1).forEach((p, i) => line(pts[i], p)); pts.forEach((p) => dot(...p, 3.5)); }
    }

    // ---- drawing ----------------------------------------------------------------------
    let hover = null; let cursor = null; let pinch = 0; let hold = 0; let raf = 0;
    function frame() {
      raf = requestAnimationFrame(frame);
      if (sim.alpha() > sim.alphaMin()) sim.tick();
      const fg = getComputedStyle(el).getPropertyValue('--fg').trim();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.translate(W / 2 + view.x, H / 2 + view.y);
      ctx.scale(view.k, view.k);
      const lit = hover ? near.get(hover) : null;
      ctx.lineWidth = 0.7 / view.k;
      ctx.strokeStyle = `rgba(${fg},${lit ? 0.06 : 0.16})`;
      ctx.beginPath();
      for (const l of links) { ctx.moveTo(l.source.x, l.source.y); ctx.lineTo(l.target.x, l.target.y); }
      ctx.stroke();
      if (lit) {
        ctx.strokeStyle = `rgba(${fg},0.75)`; ctx.lineWidth = 1.1 / view.k; ctx.beginPath();
        for (const m of lit) { ctx.moveTo(hover.x, hover.y); ctx.lineTo(m.x, m.y); }
        ctx.stroke();
      }
      // star-like points: about the same size on screen at any zoom
      const scale = Math.min(1.3, Math.max(0.55, view.k)) / view.k;
      for (const n of nodes) {
        const r = radius(n) * scale;
        const on = !lit || n === hover || lit.has(n);
        ctx.fillStyle = `rgba(${fg},${on ? (n.deg > 4 ? 1 : 0.82) : 0.18})`;
        if (n.deg > 8 && on) { ctx.shadowColor = `rgba(${fg},.7)`; ctx.shadowBlur = 6; } else ctx.shadowBlur = 0;
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();
        if (n === focus && mode === 'centralized') { ctx.shadowBlur = 0; ctx.strokeStyle = `rgba(${fg},.9)`; ctx.lineWidth = 1.2 / view.k; ctx.beginPath(); ctx.arc(n.x, n.y, r + 5 / view.k, 0, Math.PI * 2); ctx.stroke(); }
      }
      ctx.shadowBlur = 0;
      // labels of the big thoughts when zoomed in
      ctx.font = `${11 / view.k}px ui-monospace, SFMono-Regular, Menlo, monospace`;
      ctx.fillStyle = `rgba(${fg},.7)`; ctx.textAlign = 'left';
      if (view.k > 1.6 || nodes.length < 60) for (const n of nodes) if (!lit || lit.has(n) || n === hover) ctx.fillText(n.label.slice(0, 40), n.x + radius(n) + 4 / view.k, n.y + 3 / view.k);
      ctx.restore();
      if (hover) drawBracket(hover, fg);
      if (cursor) drawCursor(fg);
    }
    function drawBracket(n, fg) {
      const p = toScreen(n); const s = 18; const len = 9;
      ctx.strokeStyle = `rgba(${fg},.95)`; ctx.lineWidth = 1.4;
      const c = (x, y, dx, dy) => { ctx.beginPath(); ctx.moveTo(x + dx * len, y); ctx.lineTo(x, y); ctx.lineTo(x, y + dy * len); ctx.stroke(); };
      c(p.x - s, p.y - s, 1, 1); c(p.x + s, p.y - s, -1, 1); c(p.x - s, p.y + s, 1, -1); c(p.x + s, p.y + s, -1, -1);
      const label = n.label.length > 48 ? `${n.label.slice(0, 47)}…` : n.label;
      ctx.font = '13px ui-monospace, SFMono-Regular, Menlo, monospace';
      const tw = ctx.measureText(label).width;
      const x = p.x + s + 12 + tw > W - 10 ? p.x - s - 12 - tw : p.x + s + 12;
      ctx.fillStyle = getComputedStyle(el).getPropertyValue('--panel'); ctx.fillRect(x - 6, p.y - 12, tw + 12, 22);
      ctx.fillStyle = `rgba(${fg},1)`; ctx.textAlign = 'left'; ctx.fillText(label, x, p.y + 4);
      ctx.fillStyle = `rgba(${fg},.55)`; ctx.font = '10.5px ui-monospace, monospace';
      ctx.fillText(`${n.group} · ${n.deg} link${n.deg === 1 ? '' : 's'}`, x, p.y + 20);
    }
    function drawCursor(fg) {
      const accent = getComputedStyle(el).getPropertyValue('--accent').trim();
      ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(cursor.x, cursor.y, pinch ? 7 : 5, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = `rgba(${fg},.6)`; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(cursor.x, cursor.y, 14, 0, Math.PI * 2); ctx.stroke();
      if (hold > 0) { ctx.strokeStyle = accent; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(cursor.x, cursor.y, 14, -Math.PI / 2, -Math.PI / 2 + hold * Math.PI * 2); ctx.stroke(); }
    }
    function nodeAt(sx, sy, reach = 14) {
      const w = toWorld(sx, sy); let best = null; let bd = (reach / view.k) ** 2;
      for (const n of nodes) { const d = (n.x - w.x) ** 2 + (n.y - w.y) ** 2; const rr = Math.max(bd, (radius(n) + 2) ** 2); if (d <= rr && d <= bd + radius(n) ** 2) { bd = d; best = n; } }
      return best;
    }
    function fit() {
      if (!nodes.length) return;
      const xs = nodes.map((n) => n.x); const ys = nodes.map((n) => n.y);
      const w = Math.max(...xs) - Math.min(...xs) + 80; const h = Math.max(...ys) - Math.min(...ys) + 80;
      view.k = Math.max(0.15, Math.min(4, Math.min(W / w, (H - 120) / h)));
      view.x = -((Math.max(...xs) + Math.min(...xs)) / 2) * view.k; view.y = -((Math.max(...ys) + Math.min(...ys)) / 2) * view.k;
    }
    function zoomAt(sx, sy, factor) {
      const before = toWorld(sx, sy);
      view.k = Math.max(0.12, Math.min(8, view.k * factor));
      view.x = sx - W / 2 - before.x * view.k; view.y = sy - H / 2 - before.y * view.k;
    }
    function setFocus(n) { if (!n) return; focus = n; if (mode !== 'centralized') layout('centralized'); else layout(mode); }

    // ---- mouse & touch ---------------------------------------------------------------
    const pointers = new Map(); let dragFrom = null; let moved = false; let lastPinch = 0;
    canvas.addEventListener('pointerdown', (e) => {
      canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      dragFrom = { x: e.offsetX, y: e.offsetY, vx: view.x, vy: view.y }; moved = false; canvas.classList.add('drag');
      if (pointers.size === 2) { const [a, b] = [...pointers.values()]; lastPinch = Math.hypot(a.x - b.x, a.y - b.y); }
    });
    canvas.addEventListener('pointermove', (e) => {
      if (pointers.has(e.pointerId)) pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (lastPinch) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, d / lastPinch);
        lastPinch = d; moved = true; return;
      }
      if (dragFrom && pointers.size === 1) {
        const dx = e.offsetX - dragFrom.x; const dy = e.offsetY - dragFrom.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
        view.x = dragFrom.vx + dx; view.y = dragFrom.vy + dy;
      } else if (!camOn) hover = nodeAt(e.offsetX, e.offsetY);
    });
    const up = (e) => {
      pointers.delete(e.pointerId); canvas.classList.remove('drag');
      if (dragFrom && !moved && pointers.size === 0) { const n = nodeAt(e.offsetX, e.offsetY); if (n) { if (n === focus && mode === 'centralized') opts.onOpen?.(n.id); else setFocus(n); } }
      if (!pointers.size) { dragFrom = null; lastPinch = 0; }
    };
    canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);
    canvas.addEventListener('dblclick', (e) => { const n = nodeAt(e.offsetX, e.offsetY); if (n) opts.onOpen?.(n.id); });
    canvas.addEventListener('wheel', (e) => { e.preventDefault(); zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * 0.0015)); }, { passive: false });
    const onKey = (e) => {
      if (e.key === 'Escape') { destroy(); return; }
      const m = ORDER.find((k) => MODES[k].key === e.key); if (m) layout(m);
      if (e.key === 'f' || e.key === 'F') fit();
      if (e.key === 'Enter' && (hover || focus)) opts.onOpen?.((hover || focus).id);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', resize);
    el.querySelector('.close').onclick = () => destroy();
    el.querySelectorAll('[data-mode]').forEach((b) => { b.onclick = () => layout(b.dataset.mode); });
    el.querySelector('[data-act="fit"]').onclick = fit;
    el.querySelector('[data-act="theme"]').onclick = () => { el.classList.toggle('dark'); drawGlyph(); };
    el.querySelector('[data-act="cam"]').onclick = () => (camOn ? stopCam() : startCam());

    // ---- hand gestures ---------------------------------------------------------------
    // Index fingertip = pointer. Pinch (thumb + index) on a thought = make it the centre;
    // keep pinching for a second = open it. Pinch on empty space and move = pan.
    // Two hands pinching = zoom. Hold up 1-4 fingers = switch layout. Fist = fit to screen.
    let camOn = false; let stream = null; let landmarker = null; let camLoop = 0; let lastVideoTime = -1;
    const smooth = { x: 0, y: 0, ready: false }; let pinchStart = null; let gestureAt = 0; let lastCount = -1; let countSince = 0; let twoHand = 0;
    const HELP = `<b>Hand gestures</b><br>☝️ point: move the red dot<br>🤏 pinch a thought: make it the centre<br>🤏 hold the pinch 1 s: open the note<br>🤏 pinch empty space + move: pan<br>🤏🤏 two hands pinch, apart/together: zoom<br>1–4 fingers up 1 s: switch layout<br>✊ fist 1 s: fit to screen`;
    function status(text) { help.hidden = false; help.innerHTML = `${HELP}<div class="st">${esc(text)}</div>`; }
    async function loadScript(src) {
      if (window.Vision) return;
      await new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = () => rej(new Error('could not load hand tracking')); document.head.appendChild(s); });
    }
    async function startCam() {
      const btn = el.querySelector('[data-act="cam"]');
      btn.setAttribute('aria-pressed', 'true'); status('Starting the camera…');
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error('this browser has no camera access here');
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 960 }, height: { ideal: 540 }, facingMode: 'user' }, audio: false });
        video.srcObject = stream; await video.play();
        status('Loading hand tracking (first time takes a few seconds)…');
        await loadScript(`${MP}/vision_bundle.js`);
        const files = await window.Vision.FilesetResolver.forVisionTasks(`${MP}/wasm`);
        const make = (delegate) => window.Vision.HandLandmarker.createFromOptions(files, {
          baseOptions: { modelAssetPath: `${MP}/hand_landmarker.task`, delegate }, runningMode: 'VIDEO', numHands: 2,
          minHandDetectionConfidence: 0.6, minHandPresenceConfidence: 0.6, minTrackingConfidence: 0.5 });
        try { landmarker = await make('GPU'); } catch { landmarker = await make('CPU'); }
        camOn = true; el.classList.add('cam'); status('Ready. Show your hand to the camera.');
        track();
      } catch (e) {
        stopCam();
        const why = e?.name === 'NotAllowedError' ? 'Camera permission was refused. Allow the camera for this site in the browser, then try again.'
          : e?.name === 'NotFoundError' ? 'No camera found.' : (e?.message || String(e));
        status(`Hand gestures are off: ${why}`);
        btn.setAttribute('aria-pressed', 'false');
      }
    }
    function stopCam() {
      camOn = false; cancelAnimationFrame(camLoop); el.classList.remove('cam'); cursor = null; hold = 0; pinch = 0;
      stream?.getTracks().forEach((t) => t.stop()); stream = null; video.srcObject = null;
      landmarker?.close?.(); landmarker = null;
      el.querySelector('[data-act="cam"]').setAttribute('aria-pressed', 'false');
    }
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const isPinch = (h) => dist(h[4], h[8]) / Math.max(0.01, dist(h[0], h[9])) < 0.32;
    function fingersUp(h) {
      let n = 0;
      for (const [tip, pip] of [[8, 6], [12, 10], [16, 14], [20, 18]]) if (dist(h[tip], h[0]) > dist(h[pip], h[0]) * 1.12) n++;
      return n;
    }
    function screenOf(p) {
      // mirror (the video is shown mirrored) and use the middle 80% of the frame as the whole screen
      const x = ((1 - p.x) - 0.1) / 0.8; const y = (p.y - 0.1) / 0.8;
      return { x: Math.max(0, Math.min(1, x)) * W, y: Math.max(0, Math.min(1, y)) * H };
    }
    function track() {
      camLoop = requestAnimationFrame(track);
      if (!landmarker || video.readyState < 2 || video.currentTime === lastVideoTime) return;
      lastVideoTime = video.currentTime;
      let result;
      try { result = landmarker.detectForVideo(video, performance.now()); } catch { return; }
      const hands = result?.landmarks || [];
      const now = performance.now();
      if (!hands.length) { cursor = null; hold = 0; pinch = 0; pinchStart = null; twoHand = 0; smooth.ready = false; return; }
      // two hands pinching: zoom by the distance between them
      if (hands.length === 2 && isPinch(hands[0]) && isPinch(hands[1])) {
        const a = screenOf(hands[0][8]); const b = screenOf(hands[1][8]); const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (twoHand) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, Math.max(0.9, Math.min(1.1, d / twoHand)));
        twoHand = d; cursor = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; pinchStart = null; hold = 0; return;
      }
      twoHand = 0;
      const h = hands[0];
      const target = screenOf(h[8]);
      if (!smooth.ready) { smooth.x = target.x; smooth.y = target.y; smooth.ready = true; }
      smooth.x += (target.x - smooth.x) * 0.35; smooth.y += (target.y - smooth.y) * 0.35;
      cursor = { x: smooth.x, y: smooth.y };
      const pinching = isPinch(h);
      pinch = pinching ? 1 : 0;
      if (pinching) {
        if (!pinchStart) {
          const n = nodeAt(cursor.x, cursor.y, 26);
          pinchStart = { at: now, node: n, x: cursor.x, y: cursor.y, vx: view.x, vy: view.y };
          if (n) setFocus(n);
        } else if (pinchStart.node) {
          hold = Math.min(1, (now - pinchStart.at) / 1000);
          if (hold >= 1) { const id = pinchStart.node.id; pinchStart = { ...pinchStart, node: null }; hold = 0; opts.onOpen?.(id); }
        } else {
          view.x = pinchStart.vx + (cursor.x - pinchStart.x); view.y = pinchStart.vy + (cursor.y - pinchStart.y);
        }
        return;
      }
      pinchStart = null; hold = 0;
      hover = nodeAt(cursor.x, cursor.y, 26);
      // fingers held up: 0 (fist) = fit, 1-4 = layouts
      const count = fingersUp(h);
      if (count !== lastCount) { lastCount = count; countSince = now; return; }
      if (now - countSince > 1000 && now - gestureAt > 1500) {
        if (count === 0) { fit(); gestureAt = now; } else if (count >= 1 && count <= 4 && ORDER[count - 1] !== mode && count !== 1) { layout(ORDER[count - 1]); gestureAt = now; }
        else if (count === 1 && mode !== 'centralized' && now - countSince > 1600) { layout('centralized'); gestureAt = now; }
      }
    }

    // ---- life -----------------------------------------------------------------------
    function destroy() {
      stopCam(); cancelAnimationFrame(raf); sim.stop();
      window.removeEventListener('keydown', onKey); window.removeEventListener('resize', resize);
      el.remove(); opts.onClose?.();
    }
    layout(mode);
    for (let i = 0; i < 120; i++) sim.tick();
    fit();
    frame();
    return { destroy };
  }

  window.LrmTopology = { open };
})();
