// The private vault: passwords and other sensitive notes the owner wants passed on
// if something happens to them.
//
// - It is locked in the owner's browser with a passphrase (PBKDF2 + AES-GCM) before it
//   is sent here. This container only ever holds the locked bytes, and it is not shared
//   with the agents' container: no agent can read, change or send it.
// - Changing or deleting it needs a key derived from the passphrase, which the server
//   only keeps as a hash.
// - The owner counts as "around" while they message Lorium on WhatsApp or use the
//   console. After 5 and 6 days of silence Lorium warns them; after 7 days (and at least
//   12 hours after the last warning) Lorium sends the locked vault to the person they
//   chose. That person opens it with the passphrase the owner gave them in advance.

import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

export const DAY_MS = Number(process.env.SAFE_DAY_MS || 86_400_000);
export const WARN_DAYS = [5, 6];
export const RELEASE_DAYS = 7;
const MAX_FAILURES = 10; // wrong passphrases per hour before it locks for the hour
const MAX_LOCKED_BYTES = 2 * 1024 * 1024;
export const MAX_PAUSE_DAYS = 60;
export const REVIEW_EVERY_DAYS = 90;
const TEST_LINK_MS = 24 * 3_600_000;

const b64 = /^[A-Za-z0-9+/=]+$/;
const hex64 = /^[0-9a-f]{64}$/;
const hash = (auth) => crypto.createHash('sha256').update(Buffer.from(auth, 'hex')).digest('hex');

export function normalizePhone(value) {
  const digits = String(value || '').replace(/[\s()+.-]/g, '');
  return /^[1-9]\d{7,14}$/.test(digits) ? digits : '';
}

// When the vault goes out, given when the owner was last around and the warnings sent.
export function plan(state, now) {
  // A pause counts as being around until it ends.
  const seen = Math.max(state.lastSeen, state.pausedUntil || 0);
  if (now < seen) return { warn: [], release: false, deadline: seen + RELEASE_DAYS * DAY_MS };
  const since = now - seen;
  const due = [];
  for (const d of WARN_DAYS) if (since >= d * DAY_MS && !state.warned[d]) due.push(d);
  const lastWarn = state.warned[WARN_DAYS[WARN_DAYS.length - 1]];
  const release = since >= RELEASE_DAYS * DAY_MS && Boolean(lastWarn) && now - lastWarn >= DAY_MS / 2;
  const deadline = Math.max(seen + RELEASE_DAYS * DAY_MS, lastWarn ? lastWarn + DAY_MS / 2 : 0);
  return { warn: due, release, deadline };
}

export class Safe {
  constructor({ dir, notifyOwner, deliver, ownerPhone = () => '', log = console, now = () => Date.now() }) {
    this.file = path.join(dir, 'vault.json');
    this.dir = dir;
    this.notifyOwner = notifyOwner; // (text) => Promise
    this.deliver = deliver; // ({ phone, text, document, fileName }) => Promise
    this.ownerPhone = ownerPhone; // () => the owner's own number, for "send a test to me"
    this.log = log;
    this.now = now;
    this.state = null;
    this.failures = [];
    this.busy = false;
  }

  async load() {
    try {
      this.state = JSON.parse(await fs.readFile(this.file, 'utf8'));
    } catch {
      this.state = null;
    }
    return this;
  }

  async persist() {
    await fs.mkdir(this.dir, { recursive: true, mode: 0o700 });
    const tmp = `${this.file}.tmp`;
    if (this.state) {
      await fs.writeFile(tmp, JSON.stringify(this.state), { mode: 0o600 });
      await fs.rename(tmp, this.file);
    } else {
      await fs.rm(this.file, { force: true });
    }
  }

  // What the owner's page shows. Never the locked bytes.
  view() {
    const s = this.state;
    if (!s) return { exists: false, days: { warn: WARN_DAYS, release: RELEASE_DAYS } };
    const p = plan(s, this.now());
    return {
      exists: true, salt: s.salt, iter: s.iter, updatedAt: s.updatedAt, entries: s.entries || 0,
      recipient: s.recipient, backup: s.backup || null, ownerName: s.ownerName, armed: s.armed,
      pausedUntil: s.pausedUntil && s.pausedUntil > this.now() ? s.pausedUntil : null, testedAt: s.testedAt || null,
      lastSeen: s.lastSeen, lastSeenVia: s.lastSeenVia, warned: s.warned, deadline: s.armed && !s.released ? p.deadline : null,
      released: s.released ? { at: s.released.at, to: s.released.to } : null, error: s.error || '',
      days: { warn: WARN_DAYS, release: RELEASE_DAYS, maxPause: MAX_PAUSE_DAYS },
    };
  }

  locked() {
    const now = this.now();
    this.failures = this.failures.filter((t) => now - t < 3_600_000);
    return this.failures.length >= MAX_FAILURES;
  }

  // Proof of the passphrase: the key the page derives from it.
  verify(auth) {
    if (!this.state) throw Object.assign(new Error('There is no private vault yet.'), { status: 404 });
    if (this.locked()) throw Object.assign(new Error('Too many wrong passphrases. Try again in an hour.'), { status: 429 });
    const ok = typeof auth === 'string' && hex64.test(auth)
      && crypto.timingSafeEqual(Buffer.from(hash(auth), 'hex'), Buffer.from(this.state.verifier, 'hex'));
    if (!ok) {
      this.failures.push(this.now());
      throw Object.assign(new Error('Wrong passphrase.'), { status: 403 });
    }
  }

  open(auth) {
    this.verify(auth);
    return { iv: this.state.iv, ct: this.state.ct };
  }

  // Create, or (with the current passphrase) replace the content and settings.
  async save(body, site = '') {
    const creating = !this.state;
    if (!creating) this.verify(body.auth);
    const next = creating ? {} : { ...this.state };
    const fresh = creating || body.newAuth;
    if (fresh) {
      const auth = creating ? body.auth : body.newAuth;
      if (typeof auth !== 'string' || !hex64.test(auth)) throw Object.assign(new Error('missing key'), { status: 400 });
      if (typeof body.salt !== 'string' || !b64.test(body.salt) || body.salt.length < 16) throw Object.assign(new Error('missing salt'), { status: 400 });
      const iter = Number(body.iter);
      if (!Number.isInteger(iter) || iter < 300_000) throw Object.assign(new Error('too few iterations'), { status: 400 });
      Object.assign(next, { verifier: hash(auth), salt: body.salt, iter });
    }
    if (typeof body.iv !== 'string' || !b64.test(body.iv) || typeof body.ct !== 'string' || !b64.test(body.ct)) {
      throw Object.assign(new Error('missing locked content'), { status: 400 });
    }
    if (body.ct.length > MAX_LOCKED_BYTES) throw Object.assign(new Error('The private vault is limited to about 1.5 MB.'), { status: 413 });
    const phone = normalizePhone(body.recipient?.phone);
    const name = String(body.recipient?.name || '').trim().slice(0, 80);
    if (body.armed && (!phone || !name)) throw Object.assign(new Error('Add the name and WhatsApp number (with country code) of the person to send it to.'), { status: 400 });
    const bPhone = normalizePhone(body.backup?.phone);
    const bName = String(body.backup?.name || '').trim().slice(0, 80);
    if ((bPhone || String(body.backup?.phone || '').trim()) && (!bPhone || !bName)) throw Object.assign(new Error('Give the backup person a name and a full number with country code, or leave both empty.'), { status: 400 });
    if (bPhone && bPhone === phone) throw Object.assign(new Error('The backup person must have a different number.'), { status: 400 });
    const now = this.now();
    let pausedUntil = null;
    if (body.pausedUntil) {
      pausedUntil = Number(new Date(body.pausedUntil));
      if (!Number.isFinite(pausedUntil) || pausedUntil <= now) pausedUntil = null;
      else if (pausedUntil > now + MAX_PAUSE_DAYS * DAY_MS) throw Object.assign(new Error(`A pause can last up to ${MAX_PAUSE_DAYS} days.`), { status: 400 });
    }
    Object.assign(next, {
      v: 1, iv: body.iv, ct: body.ct, entries: Math.max(0, Math.min(10_000, Number(body.entries) || 0)),
      recipient: { name, phone }, backup: bPhone ? { name: bName, phone: bPhone } : null, pausedUntil,
      ownerName: String(body.ownerName || '').trim().slice(0, 80),
      armed: Boolean(body.armed), site: site || next.site || '', updatedAt: now,
      // Saving it means the owner is here: the clock restarts, and a vault that was
      // already sent is taken back (its link stops working).
      lastSeen: now, lastSeenVia: 'private vault', warned: {}, released: null, token: null, sentTo: [], error: '',
    });
    this.state = next;
    await this.persist();
    return this.view();
  }

  async remove(auth) {
    this.verify(auth);
    this.state = null;
    await this.persist();
  }

  // The owner did something: messaged Lorium, or used the console.
  async seen(via) {
    if (!this.state) return;
    const warned = Object.keys(this.state.warned || {}).length > 0;
    this.state.lastSeen = this.now();
    this.state.lastSeenVia = via;
    this.state.warned = {};
    await this.persist();
    if (warned && this.state.armed && !this.state.released) {
      await this.notifyOwner('✅ *Private vault*\n- You are back: the 7 days start again.\n- Nothing was sent.').catch(() => {});
    }
  }

  // The locked vault as a page anyone can open with the passphrase (the link sent to
  // the person) and as a single file.
  released(token) {
    const s = this.state;
    if (!s || typeof token !== 'string') return null;
    const same = (t) => typeof t === 'string' && t.length === token.length && crypto.timingSafeEqual(Buffer.from(token), Buffer.from(t));
    const data = { salt: s.salt, iter: s.iter, iv: s.iv, ct: s.ct, ownerName: s.ownerName, recipient: s.recipient.name };
    if (s.released && same(s.token)) return { ...data, at: s.released.at };
    if (s.test && s.test.until > this.now() && same(s.test.token)) return { ...data, at: s.test.at, test: true };
    return null;
  }

  // What the person would receive, sent to the owner's own number (the link works for 24 hours).
  async test(auth, openerPage) {
    this.verify(auth);
    const s = this.state;
    const phone = normalizePhone(this.ownerPhone());
    if (!phone) throw Object.assign(new Error('Set "Your own numbers" on Lorium\'s card on the WhatsApp page first: the test goes there.'), { status: 400 });
    const token = crypto.randomBytes(24).toString('base64url');
    s.test = { token, at: this.now(), until: this.now() + TEST_LINK_MS };
    await this.persist();
    const { text, document } = this.message(token, s.recipient?.name || 'them', openerPage, true);
    await this.deliver({ phone, text, document, fileName: 'Private vault (test).html' });
    s.testedAt = this.now();
    await this.persist();
    return this.view();
  }

  message(token, toName, openerPage, test = false) {
    const s = this.state;
    const link = s.site ? `${s.site.replace(/\/$/, '')}/whatsapp/safe/open/${token}` : '';
    const who = s.ownerName || 'The owner of this number';
    const data = { salt: s.salt, iter: s.iter, iv: s.iv, ct: s.ct, ownerName: s.ownerName, recipient: toName, at: this.now(), test };
    const text = [
      ...(test ? ['🧪 *TEST of your private vault*: this is exactly what your person would receive. The link works for 24 hours.', ''] : []),
      `Hello ${toName},`,
      '',
      `${who} asked LRMagent to send you this message if they did not respond for ${RELEASE_DAYS} days.`,
      '',
      'It contains their private vault, locked with a passphrase they gave you in advance.',
      link ? `Open it here: ${link}` : 'Open the attached file in a web browser.',
      'Or open the attached file. Then type the passphrase.',
    ].join('\n');
    return { text, document: Buffer.from(openerPage(data)) };
  }

  // Runs every few minutes.
  async tick(openerPage) {
    const s = this.state;
    if (!s || this.busy) return;
    const now = this.now();
    // Every few months: is what is in it still right?
    if (now - (s.updatedAt || now) >= REVIEW_EVERY_DAYS * DAY_MS && now - (s.reviewNudgeAt || 0) >= REVIEW_EVERY_DAYS * DAY_MS) {
      s.reviewNudgeAt = now;
      await this.persist().catch(() => {});
      const when = new Date(s.updatedAt).toLocaleDateString('en-GB').replace(/\//g, '-');
      await this.notifyOwner(`🔒 *Private vault: time for a check*\n- Last saved ${when}.\n- Are the passwords and notes still right? Knowledge → Private vault.`).catch(() => {});
    }
    if (!s.armed || s.released) return;
    this.busy = true;
    try {
      const p = plan(s, now);
      for (const d of p.warn) {
        const left = Math.max(1, Math.round((plan({ ...s, warned: { ...s.warned, [d]: now } }, now).deadline - now) / 3_600_000));
        await this.notifyOwner(`⚠️ *Private vault*\n- No message from you for ${d} days.\n- In about ${left} hours Lorium sends your private vault to ${s.recipient.name}.\n- Reply anything here to stop it.`);
        s.warned[d] = now;
        await this.persist();
      }
      if (p.release) await this.release(openerPage);
    } catch (e) {
      s.error = String(e.message || e).slice(0, 200);
      await this.persist().catch(() => {});
      this.log.error?.({ err: s.error }, 'private vault step failed');
    } finally {
      this.busy = false;
    }
  }

  async release(openerPage) {
    const s = this.state;
    const token = s.token || crypto.randomBytes(24).toString('base64url');
    s.token = token;
    s.sentTo = s.sentTo || [];
    await this.persist();
    // The person, and the backup person; each only once, and one failing does not stop the other.
    const people = [s.recipient, s.backup].filter((p) => p?.phone && !s.sentTo.includes(p.phone));
    const errors = [];
    for (const person of people) {
      try {
        const { text, document } = this.message(token, person.name, openerPage);
        await this.deliver({ phone: person.phone, text, document, fileName: 'Private vault.html' });
        s.sentTo.push(person.phone);
        await this.persist();
      } catch (e) {
        errors.push(`${person.name}: ${e.message || e}`);
      }
    }
    if (!s.sentTo.length) throw new Error(errors.join('; ') || 'not sent');
    s.released = { at: this.now(), to: s.sentTo.join(', ') };
    s.error = errors.join('; ').slice(0, 200);
    await this.persist();
    const names = [s.recipient, s.backup].filter((p) => p && s.sentTo.includes(p.phone)).map((p) => `${p.name} (+${p.phone})`).join(' and ');
    await this.notifyOwner(`🔓 *Private vault*\n- Sent to ${names} after ${RELEASE_DAYS} days without a message from you.${errors.length ? `\n- Not delivered: ${errors.join('; ')}` : ''}\n- To take it back, open Private vault in the console and save it again: the link stops working.`).catch(() => {});
  }
}
