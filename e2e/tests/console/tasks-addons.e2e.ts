import { expect } from 'e2e';
import { test } from '@e2e-dev/web';
import { healthy, signIn } from '../helpers';

// The Tasks form's WhatsApp add-ons ("Also send to", "Mention in the group") against mocked
// scheduler and gateway responses, so no WhatsApp account or real task is needed.
const INST = 'lrm-wa:default';
const PRE = 'openai:conversation:whatsapp:default:';
const GROUP_A = '120363000000000001@g.us';
const GROUP_B = '120363000000000002@g.us';
const num = (i: number) => `+628120000${String(i).padStart(4, '0')}`;

const recipient = (jid: string, name: string) => ({
  channel_type: 'openai_api', instance_id: INST, receiver: PRE + jid, name,
  is_group: jid.endsWith('@g.us'), session_id: PRE + jid, hint: 'WhatsApp group',
});
const task = (id: string, name: string, jid: string, action: object = {}) => ({
  id, name, agent_id: 'default', enabled: true, schedule: { type: 'cron', expression: '0 9 * * *' },
  action: { type: 'send_message', channel_type: 'openai_api', instance_id: INST, receiver: PRE + jid, content: 'Send the daily sales report', ...action },
});
const members = (count: number) => ({
  members: Array.from({ length: count }, (_, i) => ({ number: num(i + 1).slice(1), name: `Member ${i + 1}`, admin: false })),
});

type Members = (jid: string) => Promise<{ status: number; json: object }>;

// Registers the mocks before the page opens; returns the bodies the form saves.
async function mockTasks(browser: any, tasks: object[], loadMembers: Members) {
  const saved: any[] = [];
  await browser.route(/\/api\/scheduler\?/, (route: any) => route.fulfill({ json: { status: 'success', tasks } }));
  await browser.route(/\/api\/scheduler\/instances/, (route: any) => route.fulfill({
    json: { status: 'success', instances: [{ instance_id: INST, name: 'WhatsApp (Lorium)', channel_type: 'openai_api', channel_label: 'WhatsApp' }] },
  }));
  await browser.route(/\/api\/scheduler\/recipients/, (route: any) => route.fulfill({
    json: { status: 'success', recipients: [recipient(GROUP_A, 'Group: Sales'), recipient(GROUP_B, 'Group: Finance')] },
  }));
  await browser.route(/\/api\/scheduler\/update/, (route: any) => {
    saved.push(JSON.parse(route.request.postData || '{}'));
    return route.fulfill({ json: { status: 'success' } });
  });
  await browser.route(/\/whatsapp\/api\/groups\/members/, async (route: any) => {
    const jid = new URL(route.request.url).searchParams.get('jid') || '';
    const { status, json } = await loadMembers(jid);
    return route.fulfill({ status, json });
  });
  return saved;
}

async function openTasks(app: any, browser: any) {
  await signIn(app, browser);
  await browser.evaluate(() => (window as any).navigateTo('tasks'));
}

// Opens a task's form and waits until its recipient picker has loaded (Save needs it).
async function editTask(browser: any, id: string, jid: string) {
  await browser.locator(`#tasks-list [data-task-id="${id}"] p`).first().click();
  const picked = () => browser.evaluate(() => (window as any).eval('getDropdownValue(document.getElementById("task-edit-recipient"))'));
  await expect.poll(picked).toBe(`${INST}:${PRE}${jid}`);
}
const mentionBox = (browser: any, inner = '') => browser.locator(`#lrm-mentions ${inner}`.trim());
const tick = (browser: any, n: number) => browser.locator(`#lrm-mentions .lrm-ment-list input[value="${num(n)}"]`);

test('Tasks: a closed edit leaves no mentions behind for the next task', async ({ app, browser }) => {
  const saved = await mockTasks(browser, [
    task('e2e-a', 'Sales report', GROUP_A, { mentions: [num(1)] }),
    // Same group as A, so only the task, not the group, tells the two forms apart.
    task('e2e-b', 'Sales follow-up', GROUP_A),
  ], async () => ({ status: 200, json: members(3) }));
  await openTasks(app, browser);
  // The card names who the task tags.
  await expect(browser.locator('#tasks-list [data-task-id="e2e-a"] .lrm-card-ment')).toContainText(num(1));

  await editTask(browser, 'e2e-a', GROUP_A);
  await expect(tick(browser, 1)).toBeChecked();
  await tick(browser, 2).click();
  await expect(mentionBox(browser, '.lrm-ment-count')).toHaveText('2 of 10');
  await browser.locator('#task-edit-modal-cancel').click();

  await editTask(browser, 'e2e-b', GROUP_A);
  await expect(mentionBox(browser)).toHaveAttribute('data-state', 'ready');
  await expect(mentionBox(browser, '.lrm-ment-count')).toHaveText('0 of 10');
  await expect(tick(browser, 2)).not.toBeChecked();
  await browser.locator('#task-edit-modal-save').click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].task_id).toBe('e2e-b');
  expect(saved[0].action.mentions).toEqual([]);

  // Reopening A shows what A has stored, not the edit that was cancelled.
  await editTask(browser, 'e2e-a', GROUP_A);
  await expect(tick(browser, 1)).toBeChecked();
  await expect(tick(browser, 2)).not.toBeChecked();
  await healthy(browser);
});

test('Tasks: saving while the members load keeps the stored mentions', async ({ app, browser }) => {
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  const saved = await mockTasks(browser, [
    task('e2e-a', 'Sales report', GROUP_A, { mentions: [num(1), num(2)], mention_from_text: true }),
  ], async () => { await held; return { status: 200, json: members(3) }; });
  try {
    await openTasks(app, browser);
    await editTask(browser, 'e2e-a', GROUP_A);
    await expect(mentionBox(browser)).toHaveAttribute('data-state', 'loading');
    await expect(mentionBox(browser, '.lrm-ment-count')).toHaveText('2 of 10');
    await browser.locator('#task-edit-modal-save').click();
    await expect.poll(() => saved.length).toBe(1);
    expect(saved[0].action.mentions).toEqual([num(1), num(2)]);
    expect(saved[0].action.mention_from_text).toBe(true);
  } finally {
    release();
  }
  await healthy(browser);
});

test('Tasks: an expired sign-in while loading members says so, and Retry loads them again', async ({ app, browser }) => {
  let status = 401;
  await mockTasks(browser, [task('e2e-a', 'Sales report', GROUP_A, { mentions: [num(1)] })],
    async () => (status === 200 ? { status, json: members(3) } : { status, json: { error: 'Sign in to the LRMagent console first.' } }));
  await openTasks(app, browser);
  await editTask(browser, 'e2e-a', GROUP_A);
  await expect(mentionBox(browser, '.lrm-ment-error')).toContainText('Your sign-in has expired');
  // The stored number stays visible while the list is unavailable.
  await expect(mentionBox(browser, '.lrm-ment-chips')).toContainText(num(1));

  // The console's own sign-in screen also opens on the 401; signing in there keeps the form.
  status = 200;
  const password = browser.locator('#login-overlay input[type=password]');
  if (await password.count()) {
    await password.fill(process.env.E2E_PASSWORD ?? 'testpassword123');
    await password.press('Enter');
    await expect(browser.locator('#login-overlay')).toBeHidden();
  }
  // The failure was not cached: Retry asks the gateway again.
  await mentionBox(browser, '.lrm-ment-retry').click();
  await expect(tick(browser, 3)).toBeVisible();
  await expect(tick(browser, 1)).toBeChecked();
  await healthy(browser);
});

test('Tasks: a group closed to agents explains where to open it', async ({ app, browser }) => {
  await mockTasks(browser, [task('e2e-a', 'Sales report', GROUP_A)], async () => ({ status: 404, json: { error: 'not open' } }));
  await openTasks(app, browser);
  await editTask(browser, 'e2e-a', GROUP_A);
  await expect(mentionBox(browser, '.lrm-ment-error')).toContainText('WhatsApp page');
  await healthy(browser);
});

test('Tasks: mentions stop at 10, and local numbers ask for the country code', async ({ app, browser }) => {
  await mockTasks(browser, [task('e2e-a', 'Sales report', GROUP_A)], async () => ({ status: 200, json: members(12) }));
  await openTasks(app, browser);
  await editTask(browser, 'e2e-a', GROUP_A);

  const add = mentionBox(browser, '.lrm-ment-add input');
  await add.fill('0812 3456 789');
  await mentionBox(browser, '.lrm-ment-addbtn').click();
  await expect(mentionBox(browser, '.lrm-ment-hint')).toContainText('country code');
  await add.fill('+62 812');
  await mentionBox(browser, '.lrm-ment-addbtn').click();
  await expect(mentionBox(browser, '.lrm-ment-hint')).toContainText('8 to 15 digits');
  await add.fill('');

  for (let i = 1; i <= 10; i++) await tick(browser, i).click();
  await expect(mentionBox(browser, '.lrm-ment-count')).toHaveText('10 of 10');
  await expect(tick(browser, 11)).toBeDisabled();
  await expect(tick(browser, 11)).not.toBeChecked();
  await expect(mentionBox(browser, '.lrm-ment-addbtn')).toBeDisabled();
  await expect(mentionBox(browser, '.lrm-ment-full')).toBeVisible();

  // Freeing one place opens the rest again.
  await tick(browser, 10).click();
  await expect(tick(browser, 11)).toBeEnabled();
  await healthy(browser);
});

for (const [name, path] of [['Activity', 'activity'], ['Usage', 'usage'], ['Connections', 'connections']]) {
  test(`${name} page opens`, async ({ app, browser }) => {
    await signIn(app, browser);
    await app.open(`/assets/brand/${path}.html`);
    const text = await browser.evaluate(() => document.body.innerText.trim().length);
    expect(text).toBeGreaterThan(0);
    await healthy(browser);
  });
}

test('Signed out: the LRMagent APIs answer 401', async ({ app, browser }) => {
  await app.open('/');
  const codes = await browser.evaluate(async () => {
    const out: Record<string, number> = {};
    for (const p of ['/api/lrm/activity', '/api/lrm/usage', '/api/lrm/org', '/api/lrm/chats', '/api/lrm/vault']) {
      out[p] = (await fetch(p, { credentials: 'omit' })).status;
    }
    return out;
  });
  for (const [p, code] of Object.entries(codes)) expect(`${p} ${code}`).toBe(`${p} 401`);
});
