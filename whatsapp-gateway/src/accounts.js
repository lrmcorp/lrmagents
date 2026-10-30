// One WhatsApp Web (Baileys) session per agent. Session keys and settings live
// under DATA_DIR/<agentId>/, so links survive restarts and redeploys.

import fs from 'node:fs/promises';
import path from 'node:path';
import pino from 'pino';
import QRCode from 'qrcode';
import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from 'baileys';
import { ask } from './cowagent.js';
import { PairingBook, chunks, decide, normalizeSettings, phoneOf, toWhatsApp } from './policy.js';

const DATA_DIR = process.env.DATA_DIR || '/data';
const UNSUPPORTED_REPLY = 'I can only read text messages for now. Please type your question.';
const ERROR_REPLY = 'Sorry, something went wrong on my side. Please try again in a moment.';
const log = pino({ level: process.env.LOG_LEVEL || 'info' });
const quiet = pino({ level: 'silent' });
const SAFE_ID = /^[A-Za-z0-9_-]{1,64}$/;
// A QR login that nobody scans is stopped after this long (OpenClaw uses 3 min).
const QR_LOGIN_TTL_MS = 3 * 60 * 1000;
const MAX_RECONNECTS = 12;

let waVersion;
async function version() {
  if (!waVersion) {
    try {
      waVersion = (await fetchLatestBaileysVersion()).version;
    } catch {
      waVersion = undefined; // Baileys falls back to its bundled version.
    }
  }
  return waVersion;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class Account {
  constructor(agentId) {
    this.agentId = agentId;
    this.dir = path.join(DATA_DIR, agentId);
    this.authDir = path.join(this.dir, 'auth');
    this.status = 'unlinked'; // unlinked | starting | qr | connected | reconnecting | logged_out
    this.qr = '';
    this.me = null;
    this.error = '';
    this.sock = null;
    this.settings = normalizeSettings();
    this.pairing = new PairingBook();
    this.sentIds = new Set(); // ids we sent, so self-chat replies don't loop
    this.retry = 0;
    this.stopped = false;
    this.queues = new Map(); // chat jid -> promise chain, one answer at a time per chat
    this.seen = new Set(); // inbound ids already handled; WhatsApp can redeliver
    this.qrTimer = null;
  }

  async loadSettings() {
    try {
      this.settings = normalizeSettings(JSON.parse(await fs.readFile(path.join(this.dir, 'settings.json'), 'utf8')));
    } catch {
      this.settings = normalizeSettings();
    }
  }

  async saveSettings(next) {
    this.settings = normalizeSettings({ ...this.settings, ...next });
    await fs.mkdir(this.dir, { recursive: true });
    await fs.writeFile(path.join(this.dir, 'settings.json'), JSON.stringify(this.settings, null, 2));
    return this.settings;
  }

  async hasSession() {
    try {
      await fs.access(path.join(this.authDir, 'creds.json'));
      return true;
    } catch {
      return false;
    }
  }

  view() {
    return {
      agentId: this.agentId,
      status: this.status,
      phone: this.me ? phoneOf(this.me.id) : '',
      name: this.me?.name || '',
      error: this.error,
      settings: this.settings,
      pending: this.pairing.list(),
    };
  }

  async start() {
    this.stopped = false;
    this.status = this.retry ? 'reconnecting' : 'starting';
    this.error = '';
    await fs.mkdir(this.authDir, { recursive: true });
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);
    const sock = makeWASocket({
      version: await version(),
      auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, quiet) },
      logger: quiet,
      browser: Browsers.ubuntu('LRM Agents'),
      markOnlineOnConnect: false, // keeps the phone's own notifications working
      syncFullHistory: false,
      shouldSyncHistoryMessage: () => false,
      generateHighQualityLinkPreview: false,
    });
    this.sock = sock;
    sock.ev.on('creds.update', saveCreds);
    sock.ev.on('connection.update', (update) => this.onConnection(sock, update));
    sock.ev.on('messages.upsert', ({ messages, type }) => {
      if (type !== 'notify') return;
      for (const msg of messages) this.enqueue(sock, msg);
    });
  }

  async onConnection(sock, { connection, lastDisconnect, qr }) {
    if (sock !== this.sock) return;
    if (qr) {
      this.qr = await QRCode.toDataURL(qr, { margin: 1, width: 280 });
      this.status = 'qr';
      if (!this.qrTimer) {
        this.qrTimer = setTimeout(() => {
          this.qrTimer = null;
          if (this.status !== 'qr') return;
          this.stop();
          this.status = 'unlinked';
          this.qr = '';
          this.error = 'The QR code expired before it was scanned. Click Link to try again.';
        }, QR_LOGIN_TTL_MS);
      }
    }
    if (connection === 'open') {
      clearTimeout(this.qrTimer);
      this.qrTimer = null;
      this.status = 'connected';
      this.qr = '';
      this.retry = 0;
      this.me = sock.user;
      log.info({ agent: this.agentId }, 'whatsapp connected');
    }
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      this.qr = '';
      if (this.stopped) return;
      if (code === DisconnectReason.loggedOut) {
        log.warn({ agent: this.agentId }, 'whatsapp logged out from the phone');
        await this.wipe();
        this.status = 'logged_out';
        return;
      }
      // A QR that rotated out unscanned closes the socket too; the timer above owns that case.
      if (this.status === 'unlinked') return;
      // Back off 2s growing x1.8 to 30s with jitter (OpenClaw's curve), then give up
      // so a dead session doesn't hammer WhatsApp forever.
      this.retry += 1;
      this.error = lastDisconnect?.error?.message || '';
      if (this.retry > MAX_RECONNECTS) {
        this.status = 'error';
        this.error = `Could not reconnect (${this.error || code}). Click Link to retry.`;
        return;
      }
      this.status = 'reconnecting';
      const base = Math.min(30_000, 2000 * 1.8 ** (this.retry - 1));
      const wait = Math.round(base * (0.75 + Math.random() * 0.5));
      log.info({ agent: this.agentId, code, wait }, 'whatsapp reconnecting');
      await sleep(wait);
      if (!this.stopped && sock === this.sock) this.start().catch((e) => this.fail(e));
    }
  }

  fail(error) {
    this.status = 'reconnecting';
    this.error = error.message;
    log.error({ agent: this.agentId, err: error.message }, 'whatsapp start failed');
  }

  enqueue(sock, msg) {
    const chat = msg.key?.remoteJid || '';
    const previous = this.queues.get(chat) || Promise.resolve();
    const next = previous.then(() => this.handle(sock, msg)).catch((e) => log.error({ err: e.message }, 'handle failed'));
    this.queues.set(chat, next);
    next.finally(() => {
      if (this.queues.get(chat) === next) this.queues.delete(chat);
    });
  }

  async handle(sock, msg) {
    if (!msg.message || this.sentIds.has(msg.key.id) || this.seen.has(msg.key.id)) return;
    this.seen.add(msg.key.id);
    if (this.seen.size > 5000) this.seen.delete(this.seen.values().next().value);
    const myJids = [sock.user?.id, sock.user?.lid].filter(Boolean);
    const myPhone = phoneOf(sock.user?.id);
    const chatPhone = phoneOf(msg.key.remoteJid) || phoneOf(msg.key.remoteJidAlt);
    const selfChat = Boolean(msg.key.fromMe && myPhone && chatPhone === myPhone);
    const verdict = decide({ key: msg.key, message: msg.message, settings: this.settings, myJids, selfChat });
    const chat = msg.key.remoteJid;

    if (verdict.action === 'ignore') return;
    if (verdict.action === 'pairing') {
      const { code, fresh } = this.pairing.request(verdict.phone);
      if (fresh && code) {
        await this.send(sock, chat, `Hi! This assistant is private. Ask its owner to approve pairing code *${code}* in the LRM Agents console (WhatsApp page). The code expires in 1 hour.`);
      }
      return;
    }
    await sock.readMessages([msg.key]).catch(() => {});
    if (verdict.action === 'unsupported') {
      await this.send(sock, chat, UNSUPPORTED_REPLY, msg);
      return;
    }

    await sock.sendPresenceUpdate('composing', chat).catch(() => {});
    const typing = setInterval(() => sock.sendPresenceUpdate('composing', chat).catch(() => {}), 10_000);
    try {
      const reply = await ask({ agentId: this.agentId, conversationId: `whatsapp:${this.agentId}:${chat}`, text: verdict.text });
      clearInterval(typing);
      await this.send(sock, chat, reply, msg);
    } catch (error) {
      clearInterval(typing);
      log.error({ agent: this.agentId, err: error.message }, 'agent call failed');
      await this.send(sock, chat, ERROR_REPLY, msg);
    } finally {
      await sock.sendPresenceUpdate('paused', chat).catch(() => {});
    }
  }

  async send(sock, chat, text, quoted) {
    const parts = chunks(toWhatsApp(text));
    for (let i = 0; i < parts.length; i += 1) {
      // A short human-like pause between parts; bursts of instant messages are
      // the pattern WhatsApp flags most readily.
      if (i) await sleep(800 + Math.random() * 700);
      const sent = await sock.sendMessage(chat, { text: parts[i] }, i === 0 && quoted ? { quoted } : undefined);
      if (sent?.key?.id) {
        this.sentIds.add(sent.key.id);
        if (this.sentIds.size > 2000) this.sentIds.delete(this.sentIds.values().next().value);
      }
    }
  }

  async stop() {
    this.stopped = true;
    clearTimeout(this.qrTimer);
    this.qrTimer = null;
    const sock = this.sock;
    this.sock = null;
    try {
      sock?.end(undefined);
    } catch {
      // already closed
    }
  }

  async wipe() {
    await fs.rm(this.authDir, { recursive: true, force: true });
    this.me = null;
  }

  async unlink() {
    const sock = this.sock;
    this.stopped = true;
    try {
      await sock?.logout();
    } catch {
      // the phone may already have removed the device
    }
    await this.stop();
    await this.wipe();
    this.status = 'unlinked';
    this.qr = '';
  }
}

export class Accounts {
  constructor() {
    this.byAgent = new Map();
  }

  get(agentId, create = false) {
    if (!SAFE_ID.test(agentId)) throw new Error('invalid agent id');
    let account = this.byAgent.get(agentId);
    if (!account && create) {
      account = new Account(agentId);
      this.byAgent.set(agentId, account);
    }
    return account;
  }

  // Reconnect every agent that was linked before the restart.
  async restore() {
    await fs.mkdir(DATA_DIR, { recursive: true });
    for (const entry of await fs.readdir(DATA_DIR, { withFileTypes: true })) {
      if (!entry.isDirectory() || !SAFE_ID.test(entry.name)) continue;
      const account = this.get(entry.name, true);
      await account.loadSettings();
      if (await account.hasSession()) account.start().catch((e) => account.fail(e));
    }
  }

  async view(agentId) {
    const account = this.get(agentId);
    if (account) return account.view();
    const fresh = new Account(agentId);
    await fresh.loadSettings();
    return fresh.view();
  }

  async link(agentId) {
    const account = this.get(agentId, true);
    await account.loadSettings();
    if (['connected', 'qr', 'starting', 'reconnecting'].includes(account.status) && account.sock) return account.view();
    await account.stop();
    account.retry = 0;
    await account.start();
    return account.view();
  }

  qr(agentId) {
    return this.get(agentId)?.qr || '';
  }

  async unlink(agentId) {
    const account = this.get(agentId);
    if (account) await account.unlink();
  }

  async saveSettings(agentId, settings) {
    const account = this.get(agentId, true);
    await account.loadSettings();
    return account.saveSettings(settings);
  }

  async approve(agentId, code) {
    const account = this.get(agentId);
    const phone = account?.pairing.approve(code);
    if (!phone) return '';
    await account.saveSettings({ allowFrom: [...account.settings.allowFrom, phone] });
    if (account.sock && account.status === 'connected') {
      await account.send(account.sock, `${phone}@s.whatsapp.net`, 'You are approved. Send your message again and I will answer.').catch(() => {});
    }
    return phone;
  }

  async shutdown() {
    await Promise.all([...this.byAgent.values()].map((a) => a.stop()));
  }
}
