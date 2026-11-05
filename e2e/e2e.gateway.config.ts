// The WhatsApp gateway's own pages (WhatsApp, Private vault). GATEWAY_URL: a running gateway.
import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';

const app = { url: process.env.GATEWAY_URL ?? 'http://127.0.0.1:8181' };
const initScripts = [{ path: 'tests/init.js' }];
// CDP_URL: attach to a Chrome started with --remote-debugging-port (e.g. where Playwright can't
// download its own browser); otherwise e2e launches its own.
const connect = process.env.CDP_URL ? { cdpEndpoint: async () => (await (await fetch(`${process.env.CDP_URL}/json/version`)).json()).webSocketDebuggerUrl } : undefined;

export default {
  tests: 'tests/gateway/**/*.e2e.ts',
  // One gateway, one private vault: the desktop and phone runs must not create it at the same time.
  workers: 1,
  targets: [
    { name: 'desktop', engine: web({ viewport: { width: 1280, height: 800 }, initScripts, ...(connect ? { connect } : {}) }), app },
    { name: 'phone', engine: web({ viewport: { width: 390, height: 844 }, initScripts, ...(connect ? { connect } : {}) }), app },
  ],
} satisfies E2EConfig;
