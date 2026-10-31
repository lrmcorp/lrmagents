"""Plain-language chat errors.

When a model call fails, upstream shows the provider's raw error in the chat,
e.g. ``{"error":{"message":"Insufficient Balance",...}} (Status: 402, ...)``.
``friendly`` turns that into a sentence saying what happened and what to do;
the raw text stays in the logs (Monitor -> Logs). Installed as
agent/lrm_errors.py by the LRMagent image and called from agent_stream.py.
"""

import json
import re

LOGS = "Details are in Monitor → Logs."

# (pattern in the lower-cased error, message). First match wins, so the more
# specific causes come before the generic HTTP codes.
RULES = (
    (r"\[context_overflow\]|context length|context window|maximum context|too many tokens|prompt is too long",
     "This conversation has become too long for the AI model. Start a new chat and continue there."),
    (r"insufficient[ _]balance|insufficient[ _]quota|credit balance|out of credit|billing|payment required|exceeded your current quota|status: 402",
     "Your AI provider account{provider} has run out of credit, so the model can't answer. Top up the balance on the "
     "provider's website, or switch to another model under Config → Models (or say \"switch to Claude\" in chat)."),
    (r"invalid[ _]api[ _]key|incorrect api key|invalid x-api-key|authentication|unauthori[sz]ed|api key not valid|status: 401",
     "The AI provider{provider} rejected the API key. Check the key under Config → Models, or in the GitHub secrets if it is set there."),
    (r"rate[ _]limit|too many requests|status: 429",
     "The AI provider{provider} is getting too many requests right now. Wait a minute and try again."),
    (r"model[ _]not[ _]found|does not exist|no such model|unknown model|invalid model|status: 404",
     "The selected AI model isn't available from the provider{provider}. Pick another model under Config → Models."),
    (r"permission|forbidden|not available in your (country|region)|unsupported_country|status: 403",
     "The AI provider{provider} refused access for this account. Check the account and its API key permissions."),
    (r"content[ _](policy|filter|management)|safety|flagged|data_inspection_failed",
     "The AI provider blocked this request under its content policy. Try rephrasing the message."),
    (r"timed? ?out|timeout|connection (error|reset|refused|aborted)|failed to connect|name or service not known|network",
     "LRMagent couldn't reach the AI provider{provider}. Try again in a moment; if it keeps happening the provider may be down."),
    (r"overloaded|server is busy|service unavailable|bad gateway|internal server error|status: (500|502|503|504|529)",
     "The AI provider{provider} is having problems right now. Try again in a few minutes."),
)

ALL_FAILED = "main model and every fallback model failed"


def _provider(model_name):
    name = str(model_name or "").lower()
    for key, label in (("deepseek", "DeepSeek"), ("claude", "Anthropic"), ("gpt", "OpenAI"),
                       ("o1", "OpenAI"), ("o3", "OpenAI"), ("o4", "OpenAI"), ("gemini", "Google Gemini"),
                       ("qwen", "Alibaba Qwen"), ("glm", "Zhipu GLM"), ("kimi", "Moonshot Kimi"),
                       ("moonshot", "Moonshot Kimi"), ("minimax", "MiniMax"), ("doubao", "ByteDance Doubao")):
        if name.startswith(key) or f"/{key}" in name or f"-{key}" in name:
            return f" ({label})"
    return ""


def _short(text):
    """The provider's own message, without the JSON wrapping and status suffix."""
    m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        try:
            text = json.loads(f'"{m.group(1)}"')
        except ValueError:
            text = m.group(1)
    text = re.sub(r"\s*\(Status:[^)]*\)\s*$", "", text).strip()
    return text[:300]


def friendly(error, model_name=None):
    """A message for the chat explaining ``error`` in plain words."""
    raw = str(error or "").strip()
    try:
        low = raw.lower()
        provider = _provider(model_name)
        for pattern, message in RULES:
            if re.search(pattern, low):
                text = message.format(provider=provider)
                if ALL_FAILED in low:
                    text = "None of your AI models could answer (the main model and every fallback failed). " + text
                return f"{text} {LOGS}"
        if ALL_FAILED in low:
            return ("None of your AI models could answer: the main model and every fallback failed. "
                    "Check Config → Models and your provider accounts. " + LOGS)
        short = _short(raw)
        return f"Something went wrong: {short}. {LOGS}" if short else f"Something went wrong. {LOGS}"
    except Exception:
        return raw
