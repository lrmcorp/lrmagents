import assert from 'node:assert/strict';
import test from 'node:test';
import { explain } from '../src/jobs.js';
import { TEAMMATES, byId, childEnv, codexModels, pickModel, runArgs, safeFolder } from '../src/teammates.js';

test('every teammate has a sign-in command and a run command', () => {
  for (const t of TEAMMATES) {
    assert.ok(t.login.length, t.id);
    const args = runArgs(t, { prompt: 'do it' });
    assert.ok(args.includes('do it'), t.id);
  }
});

test('runArgs: unattended, resumes the previous session, passes the model', () => {
  assert.deepEqual(runArgs(byId('codex'), { prompt: 'p', resume: true, lastMessageFile: '/x' }),
    ['codex', 'exec', 'resume', '--last', '--skip-git-repo-check', '--dangerously-bypass-approvals-and-sandbox', '-o', '/x', 'p']);
  assert.deepEqual(runArgs(byId('claude'), { prompt: 'p', resume: true, model: 'opus' }),
    ['claude', '-p', 'p', '--dangerously-skip-permissions', '--output-format', 'text', '--continue', '--model', 'opus']);
  assert.deepEqual(runArgs(byId('gemini'), { prompt: 'p', resume: true }), ['gemini', '-p', 'p', '--yolo', '-o', 'text', '--resume', 'latest']);
  assert.deepEqual(runArgs(byId('grok'), { prompt: 'p', model: 'xai/grok-4.7' }), ['opencode', 'run', '-m', 'xai/grok-4.7', 'p']);
  assert.deepEqual(runArgs(byId('deepseek'), { prompt: 'p', resume: true, model: 'deepseek/deepseek-v4-pro' }), ['opencode', 'run', '--continue', '-m', 'deepseek/deepseek-v4-pro', 'p']);
});

test('pickModel: newest general model, not image/video/fast variants', () => {
  const xai = ['xai/grok-4.20-0309-non-reasoning', 'xai/grok-4.3', 'xai/grok-4.7', 'xai/grok-4.6', 'xai/grok-imagine-video', 'xai/grok-build-0.1'];
  assert.equal(pickModel('xai', xai), 'xai/grok-4.7');
  assert.equal(pickModel('deepseek', ['deepseek/deepseek-flash', 'deepseek/deepseek-v4-pro']), 'deepseek/deepseek-v4-pro');
  assert.equal(pickModel('xai', []), '');
});

test('safeFolder: one plain path segment', () => {
  assert.equal(safeFolder(''), 'main');
  assert.equal(safeFolder('ln-repo'), 'ln-repo');
  for (const bad of ['../x', '.git', 'a/b', 'x y', '/etc']) assert.throws(() => safeFolder(bad), bad);
});

test('childEnv: never carries the internal token; DeepSeek key only for DeepSeek', () => {
  process.env.COWAGENT_API_TOKEN = 'secret-token';
  process.env.DEEPSEEK_API_KEY = 'ds-key';
  for (const t of TEAMMATES) {
    const env = childEnv(t);
    assert.equal(Object.values(env).includes('secret-token'), false, t.id);
    assert.equal(env.DEEPSEEK_API_KEY === 'ds-key', t.id === 'deepseek', t.id);
  }
});

test('explain: plain reasons', () => {
  const t = byId('codex');
  assert.match(explain('Error: Not logged in. Run codex login', t), /not signed in/);
  assert.match(explain('429 usage limit reached', t), /usage limit/);
  assert.match(explain('weird', t), /log in Config → AI plans/);
});

test('plans as chat models: owner-only sessions', async () => {
  const { allowedSession, PLAN_MODELS } = await import('../src/chat.js');
  assert.equal(PLAN_MODELS['claude-code'], 'claude');
  assert.equal((await allowedSession('')).ok, false);
  assert.equal((await allowedSession('web-session-1')).ok, true);
  assert.equal((await allowedSession('openai:conversation:lrm-brief:default:2026-11-04')).ok, true);
  assert.equal((await allowedSession('openai:conversation:api-caller')).ok, false);
});

test('codexModels: the plan\'s models from the Codex cache, hidden ones left out', () => {
  const raw = JSON.stringify({ fetched_at: 'x', models: [{ slug: 'gpt-a', display_name: 'A' }, { slug: 'gpt-b', visibility: 'hide' }, { slug: 'gpt-a' }, 'gpt-c'] });
  assert.deepEqual(codexModels(raw), [{ id: 'gpt-a', name: 'A' }, { id: 'gpt-c', name: 'gpt-c' }]);
  assert.deepEqual(codexModels('not json'), []);
});
