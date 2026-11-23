import { expect } from 'e2e';
import { test } from '@e2e-dev/web';
import { healthy, signIn } from '../helpers';

test('signs in and shows the LRMagent menu', async ({ app, browser }) => {
  await signIn(app, browser);
  await expect(browser.locator('body')).toContainText('Chats');
  await expect(browser.locator('body')).toContainText('Private vault');
  await healthy(browser);
});

test('Personal page: Sora with Talk, voice and language', async ({ app, browser }) => {
  await signIn(app, browser);
  await app.open('/assets/brand/personal.html');
  await expect(browser.locator('#talk')).toBeVisible();
  await expect(browser.locator('#voice')).toBeVisible();
  await expect(browser.locator('#lang')).toBeVisible();
  await healthy(browser);
});

test('Personal page: a HUD panel opens full screen and closes', async ({ app, browser }) => {
  await signIn(app, browser);
  await app.open('/assets/brand/personal.html');
  await expect(browser.locator('#gestures')).toBeVisible();
  // A chat reply with panels, as the voice API sends it (no model needed): the typed message goes
  // through the page's own fetch, answered here.
  await browser.evaluate(() => {
    const hud = [
      { type: 'list', title: 'Errands', items: ['Pharmacy', 'Post office', 'Groceries', 'Bank', 'Tailor', 'Florist', 'Garage', 'Library', 'Dry cleaner', 'Key cutter'] },
      { type: 'link', title: 'Opening hours', url: 'https://example.com/hours', body: 'The pharmacy page.' },
      { type: 'image', title: 'Logo', url: `${location.origin}/assets/brand/app-icon-512.png` },
      { type: 'link', title: 'Unsafe', url: 'javascript:alert(1)' },
    ];
    const real = window.fetch.bind(window);
    window.fetch = (url: any, opts?: any) => (String(url).includes('op=chat')
      ? Promise.resolve(new Response(JSON.stringify({ text: '', hud }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      : real(url, opts));
  });
  await browser.locator('#keyboard').click();
  await browser.locator('#typed-in').fill('errands');
  await browser.locator('#typed-in').press('Enter');
  await expect(browser.locator('.card')).toHaveCount(4);

  // Tap a list panel: every item shows, not only the first eight; Esc closes and focus returns.
  await browser.locator('.card .open').nth(0).click();
  await expect(browser.locator('#viewer')).toBeVisible();
  expect(await browser.evaluate(() => document.querySelector('#viewer-title')?.textContent)).toBe('Errands'); // shown in capitals by CSS
  await expect(browser.locator('#vcontent li')).toHaveCount(10);
  await expect(browser.locator('#viewer-link')).toBeHidden();
  await browser.locator('#viewer-in').click();
  await browser.locator('#viewer-close').press('Escape');
  await expect(browser.locator('#viewer')).toBeHidden();

  // A link panel: Open in browser goes to the https page in a new tab; the close button closes.
  await browser.locator('.card .open').nth(1).click();
  await expect(browser.locator('#viewer-link')).toBeVisible();
  await expect(browser.locator('#viewer-link')).toHaveAttribute('href', 'https://example.com/hours');
  await expect(browser.locator('#viewer-link')).toHaveAttribute('target', '_blank');
  await expect(browser.locator('#viewer-link')).toHaveAttribute('rel', 'noopener noreferrer');
  await browser.locator('#viewer-close').click();
  await expect(browser.locator('#viewer')).toBeHidden();

  // A picture opens whole; a javascript: link gets no button.
  await browser.locator('.card .open').nth(2).click();
  await expect(browser.locator('#vcontent img')).toBeVisible();
  await browser.locator('#viewer-close').click();
  await browser.locator('.card .open').nth(3).click();
  await expect(browser.locator('#viewer-link')).toBeHidden();
  await browser.locator('#viewer-close').click();
  await expect(browser.locator('#viewer')).toBeHidden();

  // The gesture logic is loaded (its decisions are checked in e2e/support/check-gestures.mjs).
  const kinds = await browser.evaluate(() => (window as any).SoraGesture.step((window as any).SoraGesture.initial(), [], 1, {}).actions.length);
  expect(kinds).toBe(0);
  await healthy(browser);
});

test('Browser page: Keep signed in panel opens', async ({ app, browser }) => {
  await signIn(app, browser);
  await app.open('/assets/brand/browser.html');
  await browser.locator('#keep-open').click();
  await expect(browser.locator('.keep .box')).toContainText('Keep signed in');
  await healthy(browser);
});

test('Vault page opens', async ({ app, browser }) => {
  await signIn(app, browser);
  await app.open('/assets/brand/vault.html');
  const text = await browser.evaluate(() => document.body.innerText.trim().length);
  expect(text).toBeGreaterThan(0);
  await healthy(browser);
});

test('Tasks: the new task form opens', async ({ app, browser }) => {
  await signIn(app, browser);
  await browser.evaluate(() => (window as any).navigateTo('tasks'));
  await browser.evaluate(() => (window as any).openTaskCreateModal());
  await expect(browser.locator('#task-edit-name')).toBeVisible();
  await healthy(browser);
});
