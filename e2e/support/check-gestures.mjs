// Checks Sora's hand-gesture decisions (window.SoraGesture in personal.html) without a camera:
// synthetic MediaPipe landmarks in, actions out. Run: node e2e/support/check-gestures.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../../deploy/brand/personal.html', import.meta.url), 'utf8');
const src = html.match(/<script id="sora-gesture">([\s\S]*?)<\/script>/)?.[1];
assert.ok(src, 'the sora-gesture script block is in personal.html');
const sandbox = { window: {} };
vm.runInNewContext(src, sandbox);
const G = sandbox.window.SoraGesture;

// A right hand in camera coordinates. up: which of index/middle/ring/pinky are raised;
// pinch: thumb tip on the index tip; (dx, dy) moves the whole hand.
function hand({ up = [1, 0, 0, 0], pinch = false, gap = null, dx = 0, dy = 0 } = {}) {
  const p = Array.from({ length: 21 }, () => ({ x: 0, y: 0, z: 0 }));
  const at = (i, x, y) => { p[i] = { x: x + dx, y: y + dy, z: 0 }; };
  at(0, 0.5, 0.8);
  [-0.06, 0, 0.05, 0.1].forEach((off, f) => {
    const b = 5 + f * 4; const x = 0.5 + off;
    at(b, x, 0.62); at(b + 1, x, 0.52); at(b + 2, x, 0.47); at(b + 3, x, up[f] ? 0.42 : 0.62);
  });
  at(1, 0.42, 0.74); at(2, 0.38, 0.68); at(3, 0.36, 0.64);
  const tip = p[8];
  if (gap != null) at(4, tip.x - dx + gap, tip.y - dy); // thumb `gap` away from the index tip (hand scale is 0.2)
  else if (pinch) at(4, tip.x - dx + 0.01, tip.y - dy);
  else at(4, 0.34, 0.6);
  return p;
}
const types = (r) => Array.from(r.actions, (a) => a.type); // a plain array (the actions come from the vm realm)
const run = (frames, ctx) => {
  let state = G.initial(); const all = [];
  for (const [hands, t] of frames) { const r = G.step(state, hands, t, ctx); state = r.state; all.push(r); }
  return all;
};
const results = [];
const check = (name, fn) => { fn(); results.push(name); };

check('no hands, nothing happens', () => {
  assert.deepEqual(types(G.step(G.initial(), [], 0)), []);
});

check('pointing moves the cursor, mirrored like the preview', () => {
  const r = G.step(G.initial(), [hand({ dx: 0.1 })], 0);
  assert.deepEqual(types(r), ['cursor']);
  const c = r.actions[0];
  assert.ok(Math.abs(c.x - ((1 - 0.54) - 0.1) / 0.8) < 1e-9, `x ${c.x}`); // index tip at 0.44 + 0.1
  assert.equal(c.pinch, false);
});

check('a pinch presses once, moving it pans, letting go releases', () => {
  const [a, b, c, d] = run([[[hand()], 0], [[hand({ pinch: true })], 33], [[hand({ pinch: true, dx: -0.1 })], 66], [[hand()], 99]]);
  assert.deepEqual(types(a), ['cursor']);
  assert.deepEqual(types(b), ['cursor', 'press']);
  assert.deepEqual(types(c), ['cursor', 'pan']);
  assert.ok(c.actions[1].dx > 0, 'hand moves left in the camera = right on the mirrored screen');
  assert.deepEqual(types(d), ['cursor', 'release']);
});

check('between the thresholds a pinch neither starts nor ends (no flicker)', () => {
  const mid = hand({ gap: 0.07 }); // ratio 0.35: above "on" (0.30), below "off" (0.42)
  assert.ok(!types(G.step(G.initial(), [mid], 0)).includes('press'));
  const [, held] = run([[[hand({ pinch: true })], 0], [[mid], 33]]);
  assert.deepEqual(types(held), ['cursor', 'pan']);
});

check('two hands pinching apart zoom in, together zoom out, with no extra press', () => {
  const L = (dx) => hand({ pinch: true, dx }); const R = (dx) => hand({ pinch: true, dx });
  const frames = run([[[L(-0.1), R(0.1)], 0], [[L(-0.15), R(0.15)], 33], [[L(-0.12), R(0.12)], 66], [[L(-0.12), hand()], 99], [[L(-0.12)], 132]]);
  assert.ok(!frames.some((r) => types(r).includes('press')), 'no press during or after a two-hand zoom');
  const z1 = frames[1].actions.find((a) => a.type === 'zoom'); const z2 = frames[2].actions.find((a) => a.type === 'zoom');
  assert.ok(z1.factor > 1 && z1.factor <= 1.12, `apart ${z1.factor}`);
  assert.ok(z2.factor < 1, `together ${z2.factor}`);
  assert.ok(!types(frames[3]).includes('pan'), 'the hand left pinching does not jump on the first frame');
});

check('an open palm held 1 s closes the viewer, once', () => {
  const palm = hand({ up: [1, 1, 1, 1] });
  const ctx = { viewerOpen: true };
  const f = run([[[palm], 0], [[palm], 500], [[palm], 999], [[palm], 1000], [[palm], 1500]], ctx);
  assert.ok(!f.slice(0, 3).some((r) => types(r).includes('close')));
  assert.ok(Math.abs(f[1].actions.find((a) => a.type === 'palm').progress - 0.5) < 1e-9);
  assert.ok(types(f[3]).includes('close'));
  assert.ok(!types(f[4]).includes('close'), 'held on, it does not close a second viewer');
});

check('an open palm does nothing without a viewer, and a point is not a palm', () => {
  assert.ok(!run([[[hand({ up: [1, 1, 1, 1] })], 0], [[hand({ up: [1, 1, 1, 1] })], 1200]]).some((r) => types(r).includes('close')));
  assert.equal(G.fingersUp(hand()), 1);
  assert.equal(G.fingersUp(hand({ up: [1, 1, 1, 1] })), 4);
});

check('a dropped frame keeps the pinch; a lost hand ends it', () => {
  const [, , gap, again] = run([[[hand()], 0], [[hand({ pinch: true })], 33], [[], 100], [[hand({ pinch: true })], 130]]);
  assert.deepEqual(types(gap), []);
  assert.ok(!types(again).includes('press'), 'the same pinch continues after a dropped frame');
  const [, , lost] = run([[[hand()], 0], [[hand({ pinch: true })], 33], [[], 400]]);
  assert.deepEqual(types(lost), ['lost']);
});

console.log(results.map((n) => `ok   ${n}`).join('\n'));
console.log(`ALL PASSED (${results.length})`);
