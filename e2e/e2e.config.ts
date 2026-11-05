// LRMagent console, end to end (no AI needed: plain locators and assertions).
// APP_URL: a running console (e.g. the lrm-agents image on 9899); E2E_PASSWORD: its password.
import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';

const app = { url: process.env.APP_URL ?? 'http://127.0.0.1:9899' };
const initScripts = [{ path: 'tests/init.js' }];
// CDP_URL: attach to a Chrome started with --remote-debugging-port (e.g. where Playwright can't
// download its own browser); otherwise e2e launches its own.
const connect = process.env.CDP_URL ? { cdpEndpoint: async () => (await (await fetch(`${process.env.CDP_URL}/json/version`)).json()).webSocketDebuggerUrl } : undefined;

export default {
  tests: 'tests/console/**/*.e2e.ts',
  targets: [
    { name: 'desktop', engine: web({ viewport: { width: 1280, height: 800 }, initScripts, ...(connect ? { connect } : {}) }), app },
    { name: 'phone', engine: web({ viewport: { width: 390, height: 844 }, initScripts, ...(connect ? { connect } : {}) }), app },
  ],
} satisfies E2EConfig;
