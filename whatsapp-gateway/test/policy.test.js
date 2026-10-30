import assert from 'node:assert/strict';
import test from 'node:test';
import { PairingBook, chunks, decide, normalizeSettings, senderPhone, toWhatsApp } from '../src/policy.js';

const ME = ['6280000000001:7@s.whatsapp.net', '99999@lid'];
const dm = (from, text, extra = {}) => ({
  key: { remoteJid: `${from}@s.whatsapp.net`, id: 'x', fromMe: false, ...extra },
  message: { conversation: text },
});
const settings = (raw) => normalizeSettings(raw);

test('settings fall back to safe defaults', () => {
  assert.deepEqual(normalizeSettings({ dmPolicy: 'weird', allowFrom: ['+62 812-0000-0000', 'x'] }), {
    dmPolicy: 'pairing', allowFrom: ['6281200000000'], groupPolicy: 'off',
  });
});

test('allowlisted DM is answered, stranger gets pairing', () => {
  const s = settings({ allowFrom: ['6281111111111'] });
  assert.deepEqual(decide({ ...dm('6281111111111', 'hi'), settings: s, myJids: ME }), { action: 'answer', text: 'hi', media: '' });
  assert.deepEqual(decide({ ...dm('6282222222222', 'hi'), settings: s, myJids: ME }), { action: 'pairing', phone: '6282222222222' });
  assert.equal(decide({ ...dm('6282222222222', 'hi'), settings: settings({ dmPolicy: 'allowlist' }), myJids: ME }).action, 'ignore');
});

test('LID-addressed DM uses the alternate phone jid', () => {
  const msg = { key: { remoteJid: '12345@lid', remoteJidAlt: '6281111111111@s.whatsapp.net', id: 'x' }, message: { conversation: 'yo' } };
  assert.equal(senderPhone(msg.key), '6281111111111');
  assert.equal(decide({ ...msg, settings: settings({ allowFrom: ['6281111111111'] }), myJids: ME }).action, 'answer');
});

test('self chat is the owner; other fromMe messages are ignored', () => {
  const own = { key: { remoteJid: '6280000000001@s.whatsapp.net', fromMe: true, id: 'x' }, message: { conversation: 'note this' } };
  assert.deepEqual(decide({ ...own, settings: settings({}), myJids: ME, selfChat: true }), { action: 'answer', text: 'note this', media: '' });
  assert.equal(decide({ ...dm('6281111111111', 'sent by me', { fromMe: true }), settings: settings({ dmPolicy: 'open' }), myJids: ME }).action, 'ignore');
});

test('groups need the mention policy, an @mention and an allowed sender', () => {
  const group = (text, mentioned, participant = '6281111111111@s.whatsapp.net') => ({
    key: { remoteJid: '1203@g.us', participant, id: 'g' },
    message: { extendedTextMessage: { text, contextInfo: { mentionedJid: mentioned ? ['6280000000001@s.whatsapp.net'] : [] } } },
  });
  const s = settings({ groupPolicy: 'mention', allowFrom: ['6281111111111'] });
  assert.equal(decide({ ...group('@6280000000001 hello', true), settings: settings({ allowFrom: ['6281111111111'] }), myJids: ME }).action, 'ignore');
  assert.equal(decide({ ...group('hello', false), settings: s, myJids: ME }).action, 'ignore');
  assert.deepEqual(decide({ ...group('@6280000000001 hello', true), settings: s, myJids: ME }), { action: 'answer', text: 'hello', media: '' });
  assert.equal(decide({ ...group('@6280000000001 hi', true, '6283333333333@s.whatsapp.net'), settings: s, myJids: ME }).action, 'ignore');
});

test('photos and voice notes are answered; other captionless media is unsupported', () => {
  assert.equal(decide({ key: { remoteJid: 'status@broadcast', id: 'x' }, message: { conversation: 'x' }, settings: settings({ dmPolicy: 'open' }), myJids: ME }).action, 'ignore');
  const s = settings({ allowFrom: ['6281111111111'] });
  const msg = (message) => ({ key: { remoteJid: '6281111111111@s.whatsapp.net', id: 'x' }, message, settings: s, myJids: ME });
  assert.deepEqual(decide(msg({ imageMessage: {} })), { action: 'answer', text: '', media: 'image' });
  assert.deepEqual(decide(msg({ imageMessage: { caption: 'what is this?' } })), { action: 'answer', text: 'what is this?', media: 'image' });
  assert.deepEqual(decide(msg({ audioMessage: { ptt: true } })), { action: 'answer', text: '', media: 'audio' });
  assert.deepEqual(decide(msg({ viewOnceMessageV2: { message: { imageMessage: {} } } })), { action: 'answer', text: '', media: 'image' });
  assert.deepEqual(decide(msg({ videoMessage: {} })), { action: 'unsupported' });
  assert.deepEqual(decide(msg({ videoMessage: { caption: 'look' } })), { action: 'answer', text: 'look', media: '' });
});

test('pairing codes: stable per phone, capped, approvable, expiring', () => {
  let now = 0;
  const book = new PairingBook(() => now);
  const a = book.request('1');
  assert.match(a.code, /^[A-HJ-NP-Z2-9]{8}$/);
  assert.deepEqual(book.request('1'), { code: a.code, fresh: false });
  book.request('2'); book.request('3');
  assert.equal(book.request('4').code, '');
  assert.equal(book.approve(a.code.toLowerCase()), '1');
  assert.equal(book.approve(a.code), '');
  now = 2 * 60 * 60 * 1000;
  assert.equal(book.list().length, 0);
});

test('markdown becomes WhatsApp formatting, code untouched', () => {
  assert.equal(toWhatsApp('# Title\n**bold** and ~~gone~~ [site](https://x.io)\n- item\n`**raw**`'),
    '*Title*\n*bold* and ~gone~ site (https://x.io)\n• item\n`**raw**`');
});

test('chunks keep text and respect size', () => {
  const text = Array.from({ length: 400 }, (_, i) => `line ${i}`).join('\n');
  const parts = chunks(text, 200);
  assert.ok(parts.every((p) => p.length <= 200));
  assert.equal(parts.join('\n'), text);
});

test('rate limit: per sender, warn once, recover after the window', async () => {
  const { RateLimiter } = await import('../src/policy.js');
  let now = 0;
  const rl = new RateLimiter({ perSender: 3, perAccount: 5, windowMs: 1000 }, () => now);
  assert.deepEqual([rl.check('a'), rl.check('a'), rl.check('a'), rl.check('a'), rl.check('a')], ['ok', 'ok', 'ok', 'warn', 'drop']);
  assert.deepEqual([rl.check('b'), rl.check('b'), rl.check('c')], ['ok', 'ok', 'warn']); // account cap of 5 reached
  now = 1500;
  assert.equal(rl.check('a'), 'ok');
});

test('group history: bounded, injected once, expires', async () => {
  const { GroupHistory } = await import('../src/policy.js');
  let now = 0;
  const h = new GroupHistory({ limit: 3, maxChars: 1000, ttlMs: 100 }, () => now);
  for (const n of [1, 2, 3, 4]) h.add('g', `P${n}`, `msg ${n}`);
  assert.equal(h.take('g'), '[Recent messages in this group, oldest first:\n- P2: msg 2\n- P3: msg 3\n- P4: msg 4]');
  assert.equal(h.take('g'), '');
  h.add('g', 'Old', 'stale'); now = 200; h.add('g', 'New', 'fresh');
  assert.equal(h.take('g'), '[Recent messages in this group, oldest first:\n- New: fresh]');
});

test('self chat: own number or own LID only, never the alt sender number', async () => {
  const { isSelfChat } = await import('../src/policy.js');
  const me = ['6280000000001:7@s.whatsapp.net', '99999:7@lid'];
  assert.equal(isSelfChat({ fromMe: true, remoteJid: '6280000000001@s.whatsapp.net' }, me), true);
  assert.equal(isSelfChat({ fromMe: true, remoteJid: '99999@lid' }, me), true);
  // Owner writing to a customer in a LID chat; Baileys puts the owner's number in remoteJidAlt.
  assert.equal(isSelfChat({ fromMe: true, remoteJid: '55555@lid', remoteJidAlt: '6280000000001@s.whatsapp.net' }, me), false);
  assert.equal(isSelfChat({ fromMe: false, remoteJid: '6280000000001@s.whatsapp.net' }, me), false);
  assert.equal(isSelfChat({ fromMe: true, remoteJid: '99999@s.whatsapp.net' }, me), false); // same digits, other namespace
});

test('local file links become attachments; web links and outside paths stay text', async () => {
  const { extractLocalFiles } = await import('../src/policy.js');
  const roots = ['/home/agent/cow/'];
  const r = extractLocalFiles('Here is the chart:\n\n![Sales Q3](/home/agent/cow/images/q3.png)\n\n[report.pdf](/home/agent/cow/out/report%20v2.pdf)\nSee [site](https://x.io) and ![x](/etc/passwd) and [y](/home/agent/cow/../.cow/config.json)', roots);
  assert.equal(r.text, 'Here is the chart:\n\nSee [site](https://x.io) and ![x](/etc/passwd) and [y](/home/agent/cow/../.cow/config.json)');
  assert.deepEqual(r.files, [
    { path: '/home/agent/cow/images/q3.png', name: 'q3.png', caption: 'Sales Q3', image: true },
    { path: '/home/agent/cow/out/report v2.pdf', name: 'report v2.pdf', caption: 'report.pdf', image: false },
  ]);
  assert.deepEqual(extractLocalFiles('plain', roots), { text: 'plain', files: [] });
});
