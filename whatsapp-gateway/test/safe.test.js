import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { DAY_MS, Safe, normalizePhone, plan } from '../src/safe.js';

const auth = (s) => crypto.createHash('sha256').update(s).digest('hex');
const base = { salt: 'c2FsdHNhbHRzYWx0c2FsdA==', iter: 600000, iv: 'aXZpdml2aXZpdml2', ct: 'bG9ja2VkYnl0ZXM=' };

async function make() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'safe-'));
  let now = Date.UTC(2026, 0, 1);
  const sent = []; const owner = [];
  const safe = await new Safe({ dir, now: () => now, notifyOwner: async (t) => owner.push(t), deliver: async (m) => sent.push(m), log: { error() {} } }).load();
  return { safe, sent, owner, dir, tick: (ms) => { now += ms; }, at: () => now };
}

test('normalizePhone: country code required, spaces and + removed', () => {
  assert.equal(normalizePhone('+62 812-3456-7890'), '6281234567890');
  assert.equal(normalizePhone('0812'), '');
});

test('plan: warnings on days 5 and 6, release on day 7 and never sooner than 12 h after the last warning', () => {
  const s = { lastSeen: 0, warned: {} };
  assert.deepEqual(plan(s, 4.9 * DAY_MS).warn, []);
  assert.deepEqual(plan(s, 5 * DAY_MS).warn, [5]);
  assert.deepEqual(plan(s, 9 * DAY_MS).warn, [5, 6]); // server was down: both warnings first
  assert.equal(plan(s, 9 * DAY_MS).release, false);
  assert.equal(plan({ lastSeen: 0, warned: { 5: 9 * DAY_MS, 6: 9 * DAY_MS } }, 9.4 * DAY_MS).release, false);
  assert.equal(plan({ lastSeen: 0, warned: { 5: 9 * DAY_MS, 6: 9 * DAY_MS } }, 9.5 * DAY_MS).release, true);
});

test('only the passphrase key opens, changes or deletes it; the file holds only a hash of it', async () => {
  const { safe, dir } = await make();
  await safe.save({ ...base, auth: auth('right') });
  const file = await fs.readFile(path.join(dir, 'vault.json'), 'utf8');
  assert.ok(!file.includes(auth('right')));
  assert.deepEqual(safe.open(auth('right')), { iv: base.iv, ct: base.ct });
  assert.throws(() => safe.open(auth('wrong')), /Wrong passphrase/);
  await assert.rejects(safe.save({ ...base, auth: auth('wrong'), ct: 'b3ZlcndyaXR0ZW4=' }), /Wrong passphrase/);
  await assert.rejects(safe.remove(auth('wrong')), /Wrong passphrase/);
  assert.equal(safe.open(auth('right')).ct, base.ct);
  for (let i = 0; i < 10; i++) try { safe.open(auth(`guess${i}`)); } catch { /* counted */ }
  assert.throws(() => safe.open(auth('right')), /Too many/);
});

test('sending needs a person; 7 silent days -> two warnings -> sent once; being around resets it', async () => {
  const { safe, sent, owner, tick } = await make();
  await assert.rejects(safe.save({ ...base, auth: auth('p'), armed: true }), /name and WhatsApp number/);
  await safe.save({ ...base, auth: auth('p'), armed: true, recipient: { name: 'Sarah', phone: '+62 811 000 0000' }, ownerName: 'Ryan' }, 'https://agent.example');
  const page = (d) => `<html>${JSON.stringify(d)}</html>`;
  tick(5 * DAY_MS); await safe.tick(page);
  assert.equal(owner.length, 1); assert.match(owner[0], /5 days/);
  await safe.seen('WhatsApp'); // the owner replied
  assert.match(owner[1], /start again/);
  tick(6.9 * DAY_MS); await safe.tick(page);
  assert.equal(sent.length, 0);
  tick(0.2 * DAY_MS); await safe.tick(page); // day 7.1 since the reply: 6-day warning only now? both were due
  assert.equal(sent.length, 0, 'not sent before 12 h after the last warning');
  tick(0.5 * DAY_MS); await safe.tick(page);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].phone, '62811000000' + '0');
  assert.match(sent[0].text, /Hello Sarah/);
  assert.match(sent[0].text, /https:\/\/agent\.example\/whatsapp\/safe\/open\//);
  assert.match(sent[0].document.toString(), /"ct":"bG9ja2VkYnl0ZXM="/);
  const token = sent[0].text.match(/open\/([A-Za-z0-9_-]+)/)[1];
  assert.equal(safe.released(token).ct, base.ct);
  assert.equal(safe.released('x'.repeat(token.length)), null);
  tick(DAY_MS); await safe.tick(page);
  assert.equal(sent.length, 1, 'sent once');
  // Saving again takes it back: the link stops working.
  await safe.save({ ...base, auth: auth('p'), armed: true, recipient: { name: 'Sarah', phone: '62811000000' + '0' } });
  assert.equal(safe.released(token), null);
});

test('switched off: nothing is sent', async () => {
  const { safe, sent, owner, tick } = await make();
  await safe.save({ ...base, auth: auth('p'), armed: false, recipient: { name: 'S', phone: '628110000000' } });
  tick(30 * DAY_MS); await safe.tick(() => '');
  assert.equal(sent.length + owner.length, 0);
});
