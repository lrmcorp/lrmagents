import { expect } from 'e2e';

// No JavaScript errors, and nothing wider than the screen (no sideways scrolling on a phone).
export async function healthy(browser: any) {
  const errors = await browser.evaluate(() => (window as any).__lrmErrors || []);
  expect(errors).toEqual([]);
  const overflow = await browser.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

export async function signIn(app: any, browser: any) {
  await app.open('/');
  const password = browser.locator('input[type=password]:visible').first();
  if (await browser.locator('input[type=password]:visible').count()) {
    await password.fill(process.env.E2E_PASSWORD ?? 'testpassword123');
    await password.press('Enter');
  }
  await expect(browser.locator('.lrm-shell, #sidebar, nav').first()).toBeVisible();
}
