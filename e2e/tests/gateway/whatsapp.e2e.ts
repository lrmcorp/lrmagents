import { expect } from 'e2e';
import { test } from '@e2e-dev/web';
import { healthy } from '../helpers';

test('WhatsApp page: add and remove a designated contact', async ({ app, browser }) => {
  await app.open('/whatsapp/');
  await expect(browser.locator('#agents [data-agent]').first()).toBeVisible();
  await browser.locator('#t-groups').click();
  await expect(browser.locator('#groups')).toBeVisible();
  await browser.locator('#t-contacts').click();
  await expect(browser.locator('#designated')).toBeVisible();
  await browser.locator('#d-add [name=name]').fill('Budi');
  await browser.locator('#d-add [name=phone]').fill('+62 811 0000 0001');
  await browser.locator('#d-add button').click();
  await expect(browser.locator('#d-list')).toContainText('Budi');
  await browser.evaluate(() => { window.confirm = () => true; });
  await browser.locator('[data-dremove="0"]').click();
  await expect(browser.locator('#d-list')).toContainText('None yet');
  await healthy(browser);
});

test('WhatsApp page: an agent\'s settings open and close', async ({ app, browser }) => {
  await app.open('/whatsapp/');
  await browser.locator('[data-agent="default"] [data-settings]').click();
  await expect(browser.locator('#set-dlg')).toBeVisible();
  await browser.locator('#set-form [value=monitor]').click();
  await expect(browser.locator('#set-form [name=dmPolicy]')).toBeHidden();
  await browser.locator('#set-cancel').click();
  await expect(browser.locator('#set-dlg')).toBeHidden();
  await healthy(browser);
});

test('Private vault: create, save, lock and unlock', async ({ app, browser }) => {
  await app.open('/whatsapp/safe/');
  await expect(browser.locator('#create:not([hidden]), #unlock:not([hidden])').first()).toBeVisible();
  if (await browser.locator('#create:not([hidden])').count()) {
    await browser.locator('#c-pass').fill('correct horse battery staple');
    await browser.locator('#c-pass2').fill('correct horse battery staple');
    await browser.locator('#c-go').click();
  } else {
    await browser.locator('#u-pass').fill('correct horse battery staple');
    await browser.locator('#u-go').click();
  }
  await expect(browser.locator('#editor')).toBeVisible({ timeout: 30000 });
  await browser.locator('#notes').fill('Box key in the blue drawer.');
  await browser.locator('#save').click();
  await expect(browser.locator('#saved-note')).toContainText('Saved');
  await browser.locator('#lock').click();
  await browser.locator('#u-pass').fill('correct horse battery staple');
  await browser.locator('#u-go').click();
  await expect(browser.locator('#notes')).toHaveValue('Box key in the blue drawer.', { timeout: 30000 });
  await healthy(browser);
});
