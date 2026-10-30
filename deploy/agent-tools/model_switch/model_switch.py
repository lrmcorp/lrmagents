"""switch_model: let the user change the agent's model by asking in chat.

Only models whose provider has an API key on file are offered, the same list
the console's model picker shows. The switch takes effect on the next model
call, so the reply that confirms it already comes from the new model.

Installed into CowAgent by the LRM image (deploy/agent-tools); see README.
"""

import re

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger

# Models listed per provider in `list`, to keep the tool result small.
_LIST_LIMIT = 12


def _norm(text):
    return re.sub(r"[^a-z0-9.]", "", str(text or "").lower())


class ModelSwitch(BaseTool):
    name: str = "switch_model"
    description: str = (
        "Show or change the AI model this agent answers with. Use it when the user asks which "
        "models are available, which model you are using, or to switch/use a different one "
        "(e.g. 'switch to Claude', 'use gpt-5 from now on', 'go back to the default model'). "
        "Actions: 'list' (available models and the current one), 'set' (switch), 'reset' "
        "(this chat follows the agent's default again). Scope 'chat' switches only this "
        "conversation; scope 'agent' also saves it as the agent's default for new conversations. "
        "Only models whose provider has an API key can be chosen."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "set", "reset"]},
            "model": {"type": "string", "description": "Model name or a recognisable part of it, for 'set'"},
            "provider": {"type": "string", "description": "Provider id from 'list', when a model name exists under several"},
            "scope": {"type": "string", "enum": ["chat", "agent"], "default": "chat"},
        },
        "required": ["action"],
    }

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _catalog():
        from channel.web.api.sessions import _session_model_catalog
        return _session_model_catalog()

    def _where(self):
        model = getattr(self, "model", None)
        return model, getattr(model, "session_id", "") or "", getattr(model, "agent_id", "") or None

    @staticmethod
    def _label(group):
        label = group.get("label")
        if isinstance(label, dict):
            return label.get("en") or next(iter(label.values()), group["id"])
        return label or group["id"]

    def _resolve(self, wanted, provider, catalog):
        """(provider, model) for the request, or (None, message) when it is not unique."""
        groups = [g for g in catalog if not provider or g["id"] == provider or _norm(self._label(g)) == _norm(provider)]
        if provider and not groups:
            return None, f"No provider '{provider}' with an API key. Available: " + ", ".join(g["id"] for g in catalog)
        pairs = [(g["id"], m) for g in groups for m in g["models"]]
        exact = [p for p in pairs if p[1].lower() == wanted.lower()]
        if exact:
            return exact[0], None
        key = _norm(wanted)
        found = [p for p in pairs if key and key in _norm(p[1])]
        if not found:
            # "claude sonnet" -> every word must appear in the model name.
            words = [_norm(w) for w in wanted.split() if _norm(w)]
            found = [p for p in pairs if words and all(w in _norm(p[1]) for w in words)]
        if len(found) == 1:
            return found[0], None
        if not found:
            return None, f"No available model matches '{wanted}'. Use action 'list' to see what has an API key."
        # A few matches from one provider ("claude sonnet" -> sonnet 5.5, 5, 4.6) are one
        # family; the catalog lists each provider newest-first, so take the first.
        if len(found) <= 4 and len({pid for pid, _ in found}) == 1:
            return found[0], None
        options = ", ".join(f"{m} ({pid})" for pid, m in found[:10])
        return None, f"'{wanted}' matches several models: {options}. Ask the user which one."

    # ---- actions -----------------------------------------------------------

    def execute(self, args: dict) -> ToolResult:
        action = (args.get("action") or "").strip()
        model, session_id, agent_id = self._where()
        if model is None or not hasattr(model, "set_session_override"):
            return ToolResult.fail("Model switching is not available in this runtime.")
        try:
            catalog = self._catalog()
        except Exception as e:
            logger.warning(f"[switch_model] catalog unavailable: {e}")
            return ToolResult.fail("Could not read the model list.")

        if action == "list":
            lines = [f"Current model: {model.model}"]
            for g in catalog:
                names = g["models"][:_LIST_LIMIT]
                more = f" (+{len(g['models']) - len(names)} more)" if len(g["models"]) > len(names) else ""
                lines.append(f"- {g['id']} ({self._label(g)}): {', '.join(names)}{more}")
            if not catalog:
                lines.append("No provider has an API key yet; add one under Config → Models.")
            return ToolResult.success("\n".join(lines))

        from agent.workspace import session_prefs
        if session_id and session_prefs.get_prefs(session_id, agent_id).get("members"):
            return ToolResult.fail("This is a team conversation: each agent answers on its own model, so a chat-level switch is ignored here.")

        if action == "reset":
            if session_id:
                session_prefs.set_prefs(session_id, agent_id, model=None, provider=None)
            model.set_session_override(None, None)
            return ToolResult.success(f"This chat now follows the agent's default model ({model.model}).")

        if action != "set":
            return ToolResult.fail("action must be 'list', 'set' or 'reset'.")
        wanted = (args.get("model") or "").strip()
        if not wanted:
            return ToolResult.fail("Say which model to switch to.")
        choice, problem = self._resolve(wanted, (args.get("provider") or "").strip(), catalog)
        if problem:
            return ToolResult.fail(problem)
        provider, name = choice
        scope = (args.get("scope") or "chat").strip()

        if session_id:
            session_prefs.set_prefs(session_id, agent_id, model=name, provider=provider)
        model.set_session_override(provider, name)
        if scope != "agent":
            return ToolResult.success(f"Switched this chat to {name} ({provider}). Other chats keep their model.")

        try:
            from channel.web.api.agents import _agent_admin_service
            admin = _agent_admin_service()
            default_id = admin.snapshot().get("default_agent_id")
            target = agent_id or default_id
            if target == default_id:
                # The main agent follows the global model (Config -> Models); save it
                # there exactly as the console does.
                import json
                from channel.web.api.models import ModelsHandler
                saved = json.loads(ModelsHandler()._set_chat(provider, name))
                if saved.get("status") != "success":
                    raise RuntimeError(saved.get("message") or "could not save the model")
            else:
                admin.update_agent(target, model=name, bot_type=provider)
                # Same reload the Agents page does after an edit: swap in the new
                # roster and evict this agent's cached runtimes for their next turn.
                from channel.web.api.agents import _reload_agent_runtime
                _reload_agent_runtime(admin, changed_agent_ids=[target])
        except Exception as e:
            logger.warning(f"[switch_model] saving agent default failed: {e}")
            return ToolResult.success(f"Switched this chat to {name} ({provider}), but saving it as the agent's default failed: {e}")
        return ToolResult.success(f"Switched to {name} ({provider}) and saved it as this agent's default for new conversations.")
