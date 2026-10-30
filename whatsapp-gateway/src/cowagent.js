// Calls into the LRM Agents (CowAgent) container.

const BASE = (process.env.COWAGENT_URL || 'http://cowagent:9899').replace(/\/$/, '');
const API_TOKEN = process.env.COWAGENT_API_TOKEN || '';
const ANSWER_TIMEOUT_MS = Number(process.env.COWAGENT_TIMEOUT_MS || 600_000);

async function json(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text.slice(0, 300) };
  }
}

// Ask an agent, keeping one server-side conversation per WhatsApp chat.
export async function ask({ agentId, conversationId, text }) {
  if (!API_TOKEN) throw new Error('COWAGENT_API_TOKEN is not set');
  const response = await fetch(`${BASE}/v1/chat/completions`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${API_TOKEN}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      agent_id: agentId,
      conversation_id: conversationId,
      stream: false,
      messages: [{ role: 'user', content: text }],
    }),
    signal: AbortSignal.timeout(ANSWER_TIMEOUT_MS),
  });
  const body = await json(response);
  if (!response.ok) throw new Error(`agent HTTP ${response.status}: ${JSON.stringify(body).slice(0, 300)}`);
  return String(body?.choices?.[0]?.message?.content ?? '').trim() || '(No reply.)';
}

// The console's own session decides who may manage WhatsApp: the browser's
// cookie is forwarded to CowAgent, so there is no second password.
export async function consoleUser(cookieHeader) {
  try {
    const response = await fetch(`${BASE}/auth/check`, {
      headers: cookieHeader ? { Cookie: cookieHeader } : {},
      signal: AbortSignal.timeout(5000),
    });
    const body = await json(response);
    return body?.auth_required === false || body?.authenticated === true;
  } catch {
    return false;
  }
}

export async function listAgents(cookieHeader) {
  const response = await fetch(`${BASE}/api/agents`, {
    headers: cookieHeader ? { Cookie: cookieHeader } : {},
    signal: AbortSignal.timeout(5000),
  });
  const body = await json(response);
  const agents = body?.agents ?? body?.data?.agents ?? [];
  return agents
    .filter((a) => a && a.id)
    .map((a) => ({ id: String(a.id), name: String(a.name || a.id), enabled: a.enabled !== false }));
}
