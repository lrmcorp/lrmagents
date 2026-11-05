import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

process.env.DATA_DIR = await fs.mkdtemp(path.join(os.tmpdir(), 'gwm-'));
const { Accounts, useDesignated } = await import('../src/accounts.js');
const { MonitorLog } = await import('../src/monitor.js');
const { Designated } = await import('../src/designated.js');

const T0 = Date.UTC(2026, 10, 5, 2, 0); // 05-11-2026 09:00 Jakarta
const msg = (id, chat, fromMe, text, minutes, extra = {}) => ({ key: { id, remoteJid: chat, fromMe }, message: { conversation: text }, messageTimestamp: (T0 + minutes * 60000) / 1000, pushName: fromMe ? '' : 'Andi', ...extra });

test('monitor only: records customers and staff, reports the numbers, never sends', async () => {
  useDesignated(await new Designated(await fs.mkdtemp(path.join(os.tmpdir(), 'des-'))).load());
  const accounts = new Accounts();
  const a = accounts.get('branch-kemang', true);
  await a.saveSettings({ mode: 'monitor', ownerNumbers: ['6281200000000'] });
  a.status = 'connected';
  const sent = [];
  a.sock = { user: { id: '6289911111111@s.whatsapp.net' }, sendMessage: async (...x) => { sent.push(x); return { key: { id: 'x' } }; } };
  await a.monitor.contacts([{ id: '6281300000003@s.whatsapp.net', name: 'Bu Rina (member)' }]);
  const A = '6281300000001@s.whatsapp.net'; const B = '6281300000002@s.whatsapp.net'; const C = '6281300000003@s.whatsapp.net';
  for (const m of [
    msg('1', A, false, 'Halo, kelas pilates jam 7 masih ada?', 0), msg('2', A, true, 'Masih kak, mau booking?', 4),
    msg('3', A, false, 'Iya booking 1 ya', 6), msg('4', A, true, 'Siap, sudah dibooking', 40),
    msg('5', B, false, 'Kenapa kelas kemarin dibatalkan tanpa info? kecewa', 30),
    msg('6', C, false, 'Mau perpanjang paket', 50), msg('7', C, true, 'Baik bu, saya kirim invoice', 52),
    msg('8', 'status@broadcast', false, 'status', 1), { key: { id: '9', remoteJid: '120363@g.us', fromMe: false }, message: { conversation: 'group' }, messageTimestamp: T0 / 1000 },
  ]) await a.observe(m);
  await a.monitor.writing;
  const r = await a.monitor.report('2026-11-05', '2026-11-05', T0 + 120 * 60000);
  assert.equal(r.stats.customerChats, 3);
  assert.equal(r.stats.newNumbers, 3);
  assert.equal(r.stats.newNotSaved, 2, 'Bu Rina is saved in the phone');
  assert.equal(r.stats.unanswered, 1);
  assert.deepEqual(r.chats.find((c) => c.phone === '+6281300000001').firstReplyMinutes, 4);
  assert.equal(r.chats.find((c) => c.phone === '+6281300000001').longestWaitMinutes, 34);
  assert.equal(r.stats.slowReplies, 1);
  assert.match(r.chats.find((c) => c.phone === '+6281300000002').transcript[0], /Customer: Kenapa kelas/);
  assert.equal(r.stats.complaints, 1);
  assert.equal(r.stats.salesLeads, 2, 'the pilates booking and the renewal (the complaint is not a lead)');
  assert.equal(r.stats.leadsNotClosed, 0);
  assert.equal(r.stats.sold, 2, 'booked, and the invoice sent');
  assert.equal(r.chats.find((c) => c.phone === '+6281300000002').complaint, true);
  assert.ok(r.chats.find((c) => c.phone === '+6281300000001').excerpt.length <= 8);
  // nothing may go out: replies, owner notices, deliveries
  await assert.rejects(a.send(a.sock, A, 'hi'), /monitor-only/);
  await assert.rejects(accounts.deliver('branch-kemang', '6281200000000@s.whatsapp.net', 'note'), /monitor-only/);
  await assert.rejects(accounts.notifyOwner('branch-kemang', 'note'), /no WhatsApp number is linked/);
  assert.equal(sent.length, 0);
  // the next day: the same numbers are no longer new
  await a.observe(msg('10', A, false, 'Besok ada kelas?', 24 * 60));
  await a.monitor.writing;
  const r2 = await a.monitor.report('2026-11-06', '2026-11-06', T0 + 25 * 3600000);
  assert.equal(r2.stats.customerChats, 1);
  assert.equal(r2.stats.newNumbers, 0);
});

test('monitor report needs Monitor only', async () => {
  const accounts = new Accounts();
  const a = accounts.get('lorium-x', true);
  await a.saveSettings({ mode: 'reply' });
  await assert.rejects(accounts.monitorReport('lorium-x', '2026-11-05', '2026-11-05'), /not set to Monitor only/);
});
