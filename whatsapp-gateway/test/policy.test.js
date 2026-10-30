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
  assert.deepEqual(decide({ ...dm('6281111111111', 'hi'), settings: s, myJids: ME }), { action: 'answer', text: 'hi' });
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
  assert.deepEqual(decide({ ...own, settings: settings({}), myJids: ME, selfChat: true }), { action: 'answer', text: 'note this' });
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
  assert.deepEqual(decide({ ...group('@6280000000001 hello', true), settings: s, myJids: ME }), { action: 'answer', text: 'hello' });
  assert.equal(decide({ ...group('@6280000000001 hi', true, '6283333333333@s.whatsapp.net'), settings: s, myJids: ME }).action, 'ignore');
});

test('broadcasts are ignored and captionless media is unsupported', () => {
  assert.equal(decide({ key: { remoteJid: 'status@broadcast', id: 'x' }, message: { conversation: 'x' }, settings: settings({ dmPolicy: 'open' }), myJids: ME }).action, 'ignore');
  const img = { key: { remoteJid: '6281111111111@s.whatsapp.net', id: 'x' }, message: { imageMessage: {} } };
  assert.deepEqual(decide({ ...img, settings: settings({ allowFrom: ['6281111111111'] }), myJids: ME }), { action: 'unsupported' });
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
