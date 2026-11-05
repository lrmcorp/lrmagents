import { expect } from 'e2e';
import { test } from '@e2e-dev/web';
import { healthy, signIn } from '../helpers';

// Two throwaway agents: one Level 1, one Level 2 under it, in a new group. Names are unique per
// run because the desktop and phone runs share one console.
test('Team page: groups, levels and who reports to whom', async ({ app, browser }) => {
  await signIn(app, browser);
  const tag = Math.random().toString(36).slice(2, 7);
  const head = `e2e-head-${tag}`;
  const branch = `e2e-branch-${tag}`;
  const group = `E2E Group ${tag}`;
  const agents = async (body: object) => browser.evaluate(async (b: object) => {
    const r = await fetch('/api/agents', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    return r.status;
  }, body);
  expect(await agents({ action: 'create', id: head, name: `Head ${tag}` })).toBe(200);
  expect(await agents({ action: 'create', id: branch, name: `Branch ${tag}` })).toBe(200);
  try {
    await app.open('/assets/brand/team.html');
    await expect(browser.locator(`[data-agent="${head}"]`)).toBeVisible();

    await browser.locator('#new-group').fill(group);
    await browser.locator('#add-group button').click();
    await expect(browser.locator('#chart')).toContainText(group);

    const pick = (id: string, k: string, label: string) => browser.evaluate(([id, k, label]: string[]) => {
      const sel = document.querySelector(`[data-agent="${id}"] [data-k="${k}"]`) as HTMLSelectElement;
      sel.value = [...sel.options].find((o) => o.textContent!.trim() === label)!.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }, [id, k, label]);
    await pick(head, 'group', group);
    await pick(head, 'level', 'Level 1');
    await pick(branch, 'group', group);
    await pick(branch, 'level', 'Level 2');
    // The only Level 1 of its group is chosen for it.
    const boss = await browser.evaluate((id: string) => (document.querySelector(`[data-agent="${id}"] [data-k="reports_to"]`) as HTMLSelectElement).value, branch);
    expect(boss).toBe(head);
    await expect(browser.locator('#savebar')).toBeVisible();

    await browser.locator('#save').click();
    await expect(browser.locator('.toast')).toContainText('Saved');
    await expect(browser.locator('.reports').filter({ hasText: `Branch ${tag}` }).first()).toBeVisible();
    await healthy(browser);
  } finally {
    await agents({ action: 'delete', id: branch });
    await agents({ action: 'delete', id: head });
  }
});
