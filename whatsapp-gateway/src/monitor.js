// Monitor-only numbers (a branch's WhatsApp): every chat is recorded, nothing is ever sent.
//
// The branch phone stays the one staff use; LRMagent is linked as a WhatsApp Web device, so
// it sees the customers' messages and the staff's replies sent from the phone. Each day is
// a JSON-lines file under DATA_DIR/<agent>/monitor/, kept 90 days. The branch's agent asks
// for a report (src/server.js /whatsapp/internal/monitor) and writes the owner's summary.

import fs from 'node:fs/promises';
import path from 'node:path';

const KEEP_DAYS = 90;
const TZ = process.env.MONITOR_TZ || 'Asia/Jakarta';
// Flags set by code, so a report needs no AI to count: the agent only reads the excerpts.
const COMPLAINT = /(kecewa|komplain|complain|marah|kesal|refund|uang (saya )?kembali|tidak profesional|parah|jelek|buruk|lama banget|lambat|(tidak|ga|gak|nggak|belum) (di)?bal[ae]s|dibatalkan|batalin|salah|rusak|kotor|tipu|bohong|keluhan|kurang puas|tidak puas|not happy|disappointed|bad service|worst|terlambat|telat)/i;
const SALES = /(harga|price|berapa|biaya|paket|package|promo|diskon|discount|trial|daftar|registrasi|member|booking|book\b|jadwal|slot|available|tersedia|kelas|class|sesi|session)/i;
const CLOSED = /(sudah (di)?transfer|udah transfer|bukti transfer|sudah bayar|udah bayar|lunas|paid|invoice|sudah (di)?booking|berhasil (di)?booking|sudah dibooking|confirmed|terkonfirmasi|terdaftar|selamat bergabung|see you|sampai jumpa di kelas)/i;

const dayOf = (ms) => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ms));

export class MonitorLog {
  constructor(dir) {
    this.dir = path.join(dir, 'monitor');
    this.saved = new Map(); // phone -> name saved in the phone's contacts
    this.firstSeen = new Map(); // phone or chat -> first message time
    this.loaded = false;
    this.writing = Promise.resolve();
  }

  async load() {
    if (this.loaded) return this;
    this.loaded = true;
    try { this.saved = new Map(Object.entries(JSON.parse(await fs.readFile(path.join(this.dir, 'contacts.json'), 'utf8')))); } catch { /* none yet */ }
    try { this.firstSeen = new Map(Object.entries(JSON.parse(await fs.readFile(path.join(this.dir, 'first-seen.json'), 'utf8')))); } catch { /* none yet */ }
    return this;
  }

  async persistMaps() {
    await fs.mkdir(this.dir, { recursive: true });
    await fs.writeFile(path.join(this.dir, 'contacts.json'), JSON.stringify(Object.fromEntries(this.saved)));
    await fs.writeFile(path.join(this.dir, 'first-seen.json'), JSON.stringify(Object.fromEntries(this.firstSeen)));
  }

  // Contacts saved in the branch phone (WhatsApp syncs them to linked devices with a name).
  async contacts(list) {
    await this.load();
    let changed = false;
    for (const c of list || []) {
      const phone = String(c.phoneNumber || c.id || '').split('@')[0].split(':')[0].replace(/\D/g, '');
      const name = String(c.name || '').trim();
      if (!phone || !name || this.saved.get(phone) === name) continue;
      this.saved.set(phone, name.slice(0, 80));
      changed = true;
    }
    if (changed) await this.persistMaps().catch(() => {});
  }

  // One message: { ts, chat, phone, name, fromMe, text }.
  async record(entry) {
    await this.load();
    const key = entry.phone || entry.chat;
    if (!entry.fromMe && key && !this.firstSeen.has(key)) this.firstSeen.set(key, entry.ts);
    const line = JSON.stringify(entry) + '\n';
    this.writing = this.writing.then(async () => {
      await fs.mkdir(this.dir, { recursive: true });
      await fs.appendFile(path.join(this.dir, `${dayOf(entry.ts)}.jsonl`), line);
      if (!entry.fromMe && this.firstSeen.get(key) === entry.ts) await this.persistMaps();
    }).catch(() => {});
    return this.writing;
  }

  async prune(now = Date.now()) {
    const oldest = dayOf(now - KEEP_DAYS * 86_400_000);
    for (const f of await fs.readdir(this.dir).catch(() => [])) {
      if (/^\d{4}-\d{2}-\d{2}\.jsonl$/.test(f) && f.slice(0, 10) < oldest) await fs.rm(path.join(this.dir, f), { force: true });
    }
  }

  async read(from, to) {
    const out = [];
    for (const f of (await fs.readdir(this.dir).catch(() => [])).sort()) {
      const day = f.slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}\.jsonl$/.test(f) || day < from || day > to) continue;
      for (const line of (await fs.readFile(path.join(this.dir, f), 'utf8')).split('\n')) {
        if (!line) continue;
        try { out.push(JSON.parse(line)); } catch { /* skip a torn line */ }
      }
    }
    return out.sort((a, b) => a.ts - b.ts);
  }

  // The day's (or days') numbers, and each conversation for the agent to read.
  async report(from, to, now = Date.now()) {
    await this.load();
    const rows = await this.read(from, to);
    const byChat = new Map();
    for (const m of rows) {
      const c = byChat.get(m.chat) || { chat: m.chat, phone: m.phone || '', name: '', messages: [] };
      if (!m.fromMe && m.name) c.name = m.name;
      if (!c.phone && m.phone) c.phone = m.phone;
      c.messages.push(m);
      byChat.set(m.chat, c);
    }
    const chats = [];
    const replyMinutes = [];
    for (const c of byChat.values()) {
      const inbound = c.messages.filter((m) => !m.fromMe);
      if (!inbound.length) continue; // staff-only chats are not customer chats
      const key = c.phone || c.chat;
      const first = Number(this.firstSeen.get(key) || inbound[0].ts);
      const isNew = dayOf(first) >= from && dayOf(first) <= to;
      const savedName = c.phone ? this.saved.get(c.phone) || '' : '';
      // How long customers waited: each run of their messages until the next staff reply.
      const waits = [];
      let waitingSince = null;
      for (const m of c.messages) {
        if (!m.fromMe && waitingSince === null) waitingSince = m.ts;
        if (m.fromMe && waitingSince !== null) { waits.push(Math.round((m.ts - waitingSince) / 60000)); waitingSince = null; }
      }
      replyMinutes.push(...waits);
      const last = c.messages[c.messages.length - 1];
      const customerText = inbound.map((m) => m.text).join('\n');
      const flags = {
        complaint: COMPLAINT.test(customerText),
        salesLead: SALES.test(customerText) && !COMPLAINT.test(customerText),
        sold: CLOSED.test(c.messages.map((m) => m.text).join('\n')),
        slow: waits.some((w) => w > 15),
      };
      const pick = (m) => (!m.fromMe && (COMPLAINT.test(m.text) || SALES.test(m.text))) || CLOSED.test(m.text);
      const keyLines = [c.messages[0], ...c.messages.filter(pick), ...c.messages.slice(-2)]
        .filter((m, i, all) => all.indexOf(m) === i).slice(0, 8);
      chats.push({
        ...flags,
        excerpt: keyLines.map((m) => `${new Date(m.ts).toLocaleTimeString('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' })} ${m.fromMe ? 'Staff' : 'Customer'}: ${String(m.text).slice(0, 160)}`),
        contact: savedName || c.name || (c.phone ? `+${c.phone}` : 'unknown'), phone: c.phone ? `+${c.phone}` : '', saved: Boolean(savedName),
        new: isNew, customerMessages: inbound.length, staffMessages: c.messages.length - inbound.length,
        firstReplyMinutes: waits.length ? waits[0] : null, longestWaitMinutes: waits.length ? Math.max(...waits) : null,
        unanswered: !last.fromMe ? Math.round((now - last.ts) / 60000) : 0,
        transcript: c.messages.slice(-40).map((m) => `${new Date(m.ts).toLocaleTimeString('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' })} ${m.fromMe ? 'Staff' : 'Customer'}: ${String(m.text).slice(0, 300)}`),
      });
    }
    chats.sort((a, b) => (b.new - a.new) || b.customerMessages - a.customerMessages);
    const sorted = [...replyMinutes].sort((a, b) => a - b);
    return {
      from, to, timezone: TZ,
      stats: {
        customerChats: chats.length,
        newNumbers: chats.filter((c) => c.new).length,
        newNotSaved: chats.filter((c) => c.new && !c.saved).length,
        unanswered: chats.filter((c) => c.unanswered > 0).length,
        medianReplyMinutes: sorted.length ? sorted[Math.floor(sorted.length / 2)] : null,
        slowReplies: replyMinutes.filter((m) => m > 15).length,
        replies: replyMinutes.length,
        complaints: chats.filter((c) => c.complaint).length,
        salesLeads: chats.filter((c) => c.salesLead).length,
        sold: chats.filter((c) => c.sold).length,
        leadsNotClosed: chats.filter((c) => c.salesLead && !c.sold).length,
      },
      chats,
    };
  }
}

export { dayOf };
