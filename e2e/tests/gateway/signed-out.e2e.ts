import { expect } from 'e2e';
import { test } from '@e2e-dev/web';

// support/mock-console.mjs answers "signed out" for requests carrying this cookie.
test('Signed out: the WhatsApp APIs answer 401', async ({ app, browser }) => {
  await app.open('/whatsapp/');
  await browser.setCookies([{ name: 'e2e_signed_out', value: '1', url: process.env.GATEWAY_URL ?? 'http://127.0.0.1:8181' }]);
  try {
    const codes = await browser.evaluate(async () => {
      const out: Record<string, number> = {};
      for (const p of ['/whatsapp/api/agents', '/whatsapp/api/groups', '/whatsapp/api/designated', '/whatsapp/api/groups/members?jid=120363000000000001%40g.us']) {
        out[p] = (await fetch(p, { credentials: 'same-origin' })).status;
      }
      return out;
    });
    for (const [p, code] of Object.entries(codes)) expect(`${p} ${code}`).toBe(`${p} 401`);
  } finally {
    await browser.clearCookies({ name: 'e2e_signed_out' });
  }
});
