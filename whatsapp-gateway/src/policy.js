// Pure decisions for the gateway: who may talk to an agent, what text to send,
// and how to split replies. No I/O here, so it is unit tested directly.

import crypto from 'node:crypto';

// WhatsApp caps a text at 65k characters, but long walls read badly on a phone.
export const MAX_CHUNK = 3500;
const PAIRING_TTL_MS = 60 * 60 * 1000;
// Unanswered pairing requests per account; more are ignored until one expires.
const MAX_PENDING = 3;
const PAIRING_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // no 0/O/1/I

export const DEFAULT_SETTINGS = Object.freeze({
  // 'pairing': unknown senders get a code the owner approves in the console.
  // 'allowlist': only listed numbers. 'open': anyone. 'disabled': nobody.
  dmPolicy: 'pairing',
  allowFrom: [],
  // 'mention': answer in groups only when the linked number is @mentioned or
  // replied to, and only from allowed senders. 'off': ignore groups.
  groupPolicy: 'off',
});

export function digits(value) {
  return String(value ?? '').replace(/\D/g, '');
}

export function normalizeSettings(raw = {}) {
  const pick = (value, options, fallback) => (options.includes(value) ? value : fallback);
  return {
    dmPolicy: pick(raw.dmPolicy, ['pairing', 'allowlist', 'open', 'disabled'], DEFAULT_SETTINGS.dmPolicy),
    allowFrom: [...new Set((raw.allowFrom ?? []).map(digits).filter((n) => n.length >= 6))],
    groupPolicy: pick(raw.groupPolicy, ['mention', 'off'], DEFAULT_SETTINGS.groupPolicy),
  };
}

// "6281234567890:12@s.whatsapp.net" -> "6281234567890"; LIDs and groups -> ''.
export function phoneOf(jid) {
  if (!jid || !/@s\.whatsapp\.net$/.test(jid)) return '';
  return digits(jid.split('@')[0].split(':')[0]);
}

// The sender's phone number, using the alternate address Baileys 7 gives when
// WhatsApp addresses the chat by LID instead of phone number.
export function senderPhone(key) {
  const isGroup = String(key?.remoteJid ?? '').endsWith('@g.us');
  const candidates = isGroup
    ? [key.participant, key.participantAlt]
    : [key?.remoteJid, key?.remoteJidAlt, key?.participantAlt];
  for (const jid of candidates) {
    const phone = phoneOf(jid);
    if (phone) return phone;
  }
  return '';
}

export function messageText(message) {
  const m = message ?? {};
  const inner = m.ephemeralMessage?.message ?? m.viewOnceMessage?.message ?? m.viewOnceMessageV2?.message;
  if (inner) return messageText(inner);
  return (
    m.conversation ??
    m.extendedTextMessage?.text ??
    m.imageMessage?.caption ??
    m.videoMessage?.caption ??
    m.documentMessage?.caption ??
    ''
  ).trim();
}

export function contextInfo(message) {
  const m = message ?? {};
  for (const part of Object.values(m)) {
    if (part && typeof part === 'object' && part.contextInfo) return part.contextInfo;
  }
  return undefined;
}

export function hasMedia(message) {
  const m = message ?? {};
  return Boolean(m.imageMessage || m.videoMessage || m.audioMessage || m.documentMessage || m.stickerMessage);
}

// Was the linked account @mentioned, or is this a reply to one of its messages?
export function addressesMe(message, myJids) {
  const ctx = contextInfo(message);
  if (!ctx) return false;
  const mine = new Set(myJids.filter(Boolean).map((j) => j.split('@')[0].split(':')[0]));
  const bare = (jid) => String(jid ?? '').split('@')[0].split(':')[0];
  if ((ctx.mentionedJid ?? []).some((jid) => mine.has(bare(jid)))) return true;
  return Boolean(ctx.participant && mine.has(bare(ctx.participant)));
}

export function removeMentions(text, myJids) {
  let out = text;
  for (const jid of myJids.filter(Boolean)) out = out.replaceAll(`@${jid.split('@')[0].split(':')[0]}`, '');
  return out.trim();
}

// Decide what to do with one inbound message.
//   { action: 'ignore', reason }
//   { action: 'answer', text }
//   { action: 'pairing', phone }   -> send or reuse a pairing code
//   { action: 'unsupported' }      -> media without a caption
export function decide({ key, message, settings, myJids, selfChat }) {
  const remote = String(key?.remoteJid ?? '');
  if (!remote || remote === 'status@broadcast' || remote.endsWith('@newsletter') || remote.endsWith('@broadcast')) {
    return { action: 'ignore', reason: 'broadcast' };
  }
  const isGroup = remote.endsWith('@g.us');
  const text = messageText(message);
  const media = hasMedia(message);

  // "Message yourself" on the linked phone is the owner talking to the agent.
  if (selfChat) {
    if (!text) return media ? { action: 'unsupported' } : { action: 'ignore', reason: 'empty' };
    return { action: 'answer', text };
  }
  if (key?.fromMe) return { action: 'ignore', reason: 'from-me' };

  const phone = senderPhone(key);
  const allowed = phone && settings.allowFrom.includes(phone);

  if (isGroup) {
    if (settings.groupPolicy !== 'mention') return { action: 'ignore', reason: 'groups-off' };
    if (!addressesMe(message, myJids)) return { action: 'ignore', reason: 'not-addressed' };
    if (!allowed && settings.dmPolicy !== 'open') return { action: 'ignore', reason: 'group-sender-not-allowed' };
    const cleaned = removeMentions(text, myJids);
    if (!cleaned) return media ? { action: 'unsupported' } : { action: 'ignore', reason: 'empty' };
    return { action: 'answer', text: cleaned };
  }

  if (settings.dmPolicy === 'disabled') return { action: 'ignore', reason: 'dm-disabled' };
  if (!allowed && settings.dmPolicy !== 'open') {
    if (settings.dmPolicy === 'pairing' && phone) return { action: 'pairing', phone };
    return { action: 'ignore', reason: 'not-allowed' };
  }
  if (!text) return media ? { action: 'unsupported' } : { action: 'ignore', reason: 'empty' };
  return { action: 'answer', text };
}

// WhatsApp formatting differs from Markdown: *bold*, _italic_, ~strike~, and no
// headings or link syntax. Code blocks and inline code are left alone.
export function toWhatsApp(markdown) {
  return String(markdown ?? '')
    .split(/(```[\s\S]*?```|`[^`\n]+`)/)
    .map((part, i) => {
      if (i % 2) return part;
      return part
        .replace(/^#{1,6}\s+(.+)$/gm, '*$1*')
        .replace(/\*\*(.+?)\*\*/g, '*$1*')
        .replace(/__(.+?)__/g, '_$1_')
        .replace(/~~(.+?)~~/g, '~$1~')
        .replace(/!?\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '$1 ($2)')
        .replace(/^(\s*)[-*]\s+/gm, '$1• ');
    })
    .join('');
}

export function chunks(text, size = MAX_CHUNK) {
  const out = [];
  let rest = String(text ?? '').trim();
  while (rest.length > size) {
    let cut = rest.lastIndexOf('\n\n', size);
    if (cut < size / 2) cut = rest.lastIndexOf('\n', size);
    if (cut < size / 2) cut = rest.lastIndexOf(' ', size);
    if (cut < size / 2) cut = size;
    out.push(rest.slice(0, cut).trimEnd());
    rest = rest.slice(cut).trimStart();
  }
  if (rest) out.push(rest);
  return out;
}

// Pending pairing requests: phone -> { code, expires }. One live code per phone,
// so a sender who keeps messaging does not mint new codes.
export class PairingBook {
  constructor(now = () => Date.now()) {
    this.now = now;
    this.pending = new Map();
  }

  request(phone) {
    this.prune();
    const existing = this.pending.get(phone);
    if (existing) return { code: existing.code, fresh: false };
    if (this.pending.size >= MAX_PENDING) return { code: '', fresh: false };
    let code = '';
    for (let i = 0; i < 8; i += 1) code += PAIRING_ALPHABET[crypto.randomInt(PAIRING_ALPHABET.length)];
    this.pending.set(phone, { code, expires: this.now() + PAIRING_TTL_MS });
    return { code, fresh: true };
  }

  approve(code) {
    this.prune();
    const wanted = String(code ?? '').trim().toUpperCase();
    for (const [phone, entry] of this.pending) {
      if (entry.code === wanted) {
        this.pending.delete(phone);
        return phone;
      }
    }
    return '';
  }

  list() {
    this.prune();
    return [...this.pending].map(([phone, { code, expires }]) => ({ phone, code, expires }));
  }

  prune() {
    const now = this.now();
    for (const [phone, entry] of this.pending) if (entry.expires <= now) this.pending.delete(phone);
  }
}
