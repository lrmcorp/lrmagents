import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

// Keep any stray write away from /data.
process.env.DATA_DIR = await fs.mkdtemp(path.join(os.tmpdir(), 'wa-accounts-'));
process.env.LOG_LEVEL = 'silent';
const { Accounts, useDesignated } = await import('../src/accounts.js');

const GROUP = '120363000000000001@g.us';
const OTHER = '120363000000000002@g.us';
const ME = '6280000000001:7@s.whatsapp.net';

useDesignated({
  group: (jid) => (jid === GROUP ? { jid, agents: [] } : null),
  groupAllows: (jid) => jid === GROUP,
  has: () => false,
  allows: () => false,
  listenOnly: () => false,
  mayReply: () => false,
});

function fakeSock({ participants = [], metaFails = false } = {}) {
  const sock = { user: { id: ME }, sent: [], metaCalls: 0 };
  sock.sendMessage = async (chat, content) => {
    sock.sent.push({ chat, content });
    return { key: { id: `m${sock.sent.length}${Math.random()}` } };
  };
  sock.groupMetadata = async () => {
    sock.metaCalls += 1;
    if (metaFails) throw new Error('rate-overlimit');
    return { participants };
  };
  return sock;
}

function setup(agentId, sock, settings = {}) {
  const accounts = new Accounts();
  const account = accounts.get(agentId, true);
  account.status = 'connected';
  account.sock = sock;
  Object.assign(account.settings, settings);
  return { accounts, account };
}

const MEMBERS = [{ id: '6281111111111@s.whatsapp.net' }, { id: '55501@lid', phoneNumber: '64225802322@s.whatsapp.net' }];

test('deliver: tags phone and LID members, LID by its own token', async () => {
  const sock = fakeSock({ participants: MEMBERS });
  const { accounts } = setup('a1', sock);
  const r = await accounts.deliver('a1', GROUP, 'Report', '', ['+6281111111111', '64225802322', '6289999999999']);
  assert.deepEqual(r.mentioned, ['6281111111111', '64225802322']);
  assert.deepEqual(r.skipped, [{ number: '6289999999999', reason: 'not in this group' }]);
  assert.equal(sock.sent[0].content.text, 'Report\n\ncc @6281111111111 @55501');
  assert.deepEqual(sock.sent[0].content.mentions, ['6281111111111@s.whatsapp.net', '55501@lid']);
});

test('deliver: metadata failure still sends, every number skipped as unavailable', async () => {
  const sock = fakeSock({ metaFails: true });
  const { accounts } = setup('a2', sock);
  const r = await accounts.deliver('a2', GROUP, 'Report', '', ['6281111111111', '6282222222222']);
  assert.equal(sock.sent.length, 1);
  assert.equal(sock.sent[0].content.text, 'Report');
  assert.equal(sock.sent[0].content.mentions, undefined);
  assert.deepEqual(r.skipped, [
    { number: '6281111111111', reason: 'group member list unavailable' },
    { number: '6282222222222', reason: 'group member list unavailable' },
  ]);
});

test('deliver: same text with a different tag list is not deduped; metadata is cached', async () => {
  const sock = fakeSock({ participants: MEMBERS });
  const { accounts } = setup('a3', sock);
  await accounts.deliver('a3', GROUP, 'Daily', '', ['6281111111111']);
  assert.deepEqual(await accounts.deliver('a3', GROUP, 'Daily', '', ['6281111111111']), { deduped: true });
  const third = await accounts.deliver('a3', GROUP, 'Daily', '', ['64225802322']);
  assert.deepEqual(third.mentioned, ['64225802322']);
  assert.equal(sock.sent.length, 2);
  assert.equal(sock.metaCalls, 1);
});

test('deliver: an @token near the top of a long text is tagged on that chunk', async () => {
  const sock = fakeSock({ participants: MEMBERS });
  const { accounts } = setup('a4', sock);
  const long = `@6281111111111 please read.\n\n${'Line of the report.\n'.repeat(260)}`;
  await accounts.deliver('a4', GROUP, long, '', ['6281111111111', '64225802322']);
  assert.ok(sock.sent.length >= 2);
  assert.deepEqual(sock.sent[0].content.mentions, ['6281111111111@s.whatsapp.net']);
  const last = sock.sent[sock.sent.length - 1].content;
  assert.match(last.text, /cc @55501$/);
  assert.deepEqual(last.mentions, ['55501@lid']);
});

test('deliver: groups the owner did not tick are refused; plain sends unchanged', async () => {
  const sock = fakeSock({ participants: MEMBERS });
  const { accounts } = setup('a5', sock);
  await assert.rejects(accounts.deliver('a5', OTHER, 'x'), /groups the owner ticked/);
  await accounts.deliver('a5', GROUP, 'No tags');
  assert.deepEqual(sock.sent[0].content, { text: 'No tags' });
  assert.equal(sock.metaCalls, 0);
});

test('send: uses the account socket that is live now', async () => {
  const old = fakeSock();
  const now = fakeSock();
  const { account } = setup('a6', now);
  await account.send(old, '6281111111111@s.whatsapp.net', 'late reply');
  assert.equal(old.sent.length, 0);
  assert.equal(now.sent[0].content.text, 'late reply');
});

test('notifyOwner without an agent id falls back to a linked number', async () => {
  const sock = fakeSock();
  const { accounts } = setup('a7', sock);
  await accounts.notifyOwner('', 'hello owner');
  assert.equal(sock.sent[0].chat, '6280000000001@s.whatsapp.net');
  assert.equal(sock.sent[0].content.text, 'hello owner');
});

test('groupMembers: ticked groups only, monitor-only numbers skipped', async () => {
  const sock = fakeSock({ participants: MEMBERS });
  const { accounts, account } = setup('a8', sock);
  assert.equal(await accounts.groupMembers(OTHER), null);
  assert.equal(sock.metaCalls, 0);
  const members = await accounts.groupMembers(GROUP);
  assert.deepEqual(members.map((m) => m.number).sort(), ['6281111111111', '64225802322']);
  account.settings.mode = 'monitor';
  accounts.groupMetaCache.clear();
  assert.equal(await accounts.groupMembers(GROUP), null);
  assert.equal(sock.metaCalls, 1);
});

test('start: concurrent calls share one open', async () => {
  const { account } = setup('a9', fakeSock());
  let opens = 0;
  account.open = async () => { opens += 1; await new Promise((r) => setTimeout(r, 20)); };
  await Promise.all([account.start(), account.start()]);
  assert.equal(opens, 1);
  await account.start();
  assert.equal(opens, 2);
});

test('link: a double click opens one socket', async () => {
  const accounts = new Accounts();
  const account = accounts.get('a10', true);
  let opens = 0;
  account.open = async () => { opens += 1; await new Promise((r) => setTimeout(r, 20)); account.sock = fakeSock(); account.status = 'qr'; };
  await Promise.all([accounts.link('a10'), accounts.link('a10')]);
  assert.equal(opens, 1);
  await accounts.link('a10');
  assert.equal(opens, 1);
});
