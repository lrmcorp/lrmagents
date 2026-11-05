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
