"""Apply the LRMagent brand and look to the upstream web console at image build time.

Every edit must match upstream, so an upstream change fails the build instead
of silently shipping the stock upstream look or name.
"""
import json
import pathlib
import sys

app = pathlib.Path(sys.argv[1])
theme = json.loads(pathlib.Path(sys.argv[2]).read_text())
web = app / "channel" / "web"
NAME = "LRMagent"
LEGAL = "Liando Reyfort Management"


def patch(rel, old, new, count=1, root=web):
    path = root / rel
    text = path.read_text(encoding="utf-8")
    found = text.count(old)
    if (count == "all" and not found) or (count != "all" and found != count):
        sys.exit(f"{rel}: expected {count} match(es) for {old!r}, found {found}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def pair(cls, black, white, alt):
    """Black artwork for light surfaces, white for dark; brand.css shows one."""
    return (f'<img src="/assets/brand/{black}" alt="{alt}" class="{cls} lrm-on-light">'
            f'<img src="/assets/brand/{white}" alt="" aria-hidden="true" class="{cls} lrm-on-dark">')


# Color theme: built-in, listed first after the stock one, selected by default.
patch("static/js/core/themes.js",
      "const BUILTIN_COLOR_THEMES = [\n",
      "const BUILTIN_COLOR_THEMES = [\n    " + json.dumps(theme) + ",\n")
patch("static/js/core/themes.js",
      "localStorage.getItem(COLOR_THEME_KEY) || DEFAULT_COLOR_THEME;",
      "localStorage.getItem(COLOR_THEME_KEY) || 'lrm';")

# Light appearance by default, before first paint and after scripts load.
patch("chat.html", "localStorage.getItem('cow_theme') || 'dark';", "localStorage.getItem('cow_theme') || 'light';")
patch("static/js/core/theme.js", "|| window.__COW_DEFAULT_THEME__ || 'dark';", "|| window.__COW_DEFAULT_THEME__ || 'light';")

# Stylesheet and layout script: the OpenBot-style shell (Inter, the stock font)
# loads after themes.css and after the console's own scripts.
patch("chat.html", '<script defer src="/assets/js/workspace.js"></script>',
      '<script defer src="/assets/js/workspace.js"></script>\n    <script defer src="/assets/brand/shell.js"></script>')
patch("chat.html",
      '<link rel="stylesheet" href="/assets/css/themes.css">',
      '<link rel="stylesheet" href="/assets/css/themes.css">\n'
      '    <link rel="stylesheet" href="/assets/brand/brand.css">')

# English only: every language lookup resolves to English.
for page in ("chat.html", "templates/guest/page.html"):
    patch(page,
          "window.__cowNormalizeLang__ = function(raw) {",
          "window.__cowNormalizeLang__ = function(raw) {\n        return 'en';  // LRM: English only")

# The Settings language picker offers Chinese too; leave English as its only option.
patch("static/js/views/config.js",
      "[{ value: 'zh', label: '简体中文' }, { value: 'zh-Hant', label: '繁體中文' }, { value: 'en', label: 'English' }]",
      "[{ value: 'en', label: 'English' }]")

# Brand marks.
patch("templates/layout/sidebar.html",
      '<span class="text-white font-semibold text-sm truncate">CowAgent</span>\n'
      '                    <span class="text-neutral-500 text-xs" data-i18n="console">控制台</span>',
      pair("lrm-wordtext", "wordtext.png", "wordtext-white.png", LEGAL))
patch("templates/views/chat.html",
      '<img src="/assets/logo.jpg" alt="CowAgent" class="w-16 h-16 rounded-2xl mb-6 shadow-lg shadow-primary-500/20">',
      pair("lrm-wordmark mb-6", "wordmark.png", "wordmark-white.png", LEGAL))
patch("static/js/chat/new-chat.js",
      '<img src="/assets/logo.jpg" alt="CowAgent" class="w-16 h-16 rounded-2xl mb-6 shadow-lg shadow-primary-500/20">',
      pair("lrm-wordmark mb-6", "wordmark.png", "wordmark-white.png", LEGAL))
patch("templates/layout/login.html",
      '<img src="/assets/logo.jpg" alt="CowAgent" class="w-16 h-16 rounded-2xl mb-4 shadow-lg">',
      pair("lrm-wordmark mb-4", "wordmark.png", "wordmark-white.png", LEGAL))

# Name: every visible upstream product name in the console and its /config title.
for rel in ("chat.html", "templates/layout/sidebar.html", "templates/layout/login.html",
            "templates/views/chat.html", "static/js/chat/new-chat.js", "static/js/chat/state.js",
            "static/js/chat/send.js", "static/js/boot.js", "static/js/views/shares.js",
            "static/js/core/i18n.js", "api/config.py"):
    patch(rel, "CowAgent", NAME, count="all")
# The built-in agent's fallback name, used when config.json has not been written
# yet (upstream then ignores DEFAULT_AGENT_NAME on the Agents page).
patch("agent/registry.py", 'name=builtin_name or "CowAgent",', f'name=builtin_name or "{NAME}",', root=app)

# WhatsApp: a built-in menu entry after Channels that opens the gateway page
# (/whatsapp/) inside the console. The sidebar is drawn from a menu document, so
# the entry is merged into it (and restored if a saved menu lacks it).
patch("static/js/core/menu.js",
      "    star: 'fa-star',",
      "    whatsapp: 'fa-comments', vault: 'fa-gem', safe: 'fa-lock', personal: 'fa-circle-dot', org: 'fa-sitemap', star: 'fa-star',")
patch("static/js/core/menu.js",
      "function menuMerge(saved) {",
      """function menuMerge(saved) {
    return _lrmEnsureWhatsApp(_menuMergeStock(saved));
}

function _lrmEnsureWhatsApp(doc) {
    // LRMagent pages: Vault in place of Memory and Knowledge, WhatsApp after Channels,
    // Usage after Logs. Pages it replaces are hidden once, when it is first added, so
    // showing them again from the menu editor sticks.
    const pages = [
        { after: 'chat', item: { id: 'lrm-personal', type: 'url', url: '/assets/brand/personal.html', open: 'frame', title: 'Personal', icon: 'personal', hidden: false } },
        { after: 'knowledge', hide: ['memory', 'knowledge'], item: { id: 'lrm-vault', type: 'url', url: '/assets/brand/vault.html', open: 'frame', title: 'Vault', icon: 'vault', hidden: false } },
        { after: 'knowledge', item: { id: 'lrm-safe', type: 'url', url: '/whatsapp/safe/', open: 'frame', title: 'Private vault', icon: 'safe', hidden: false } },
        { after: 'agents', item: { id: 'lrm-team', type: 'url', url: '/assets/brand/team.html', open: 'frame', title: 'Team', icon: 'org', hidden: false } },
        { after: 'skills', item: { id: 'lrm-teammates', type: 'url', url: '/teammates/', open: 'frame', title: 'Teammates', icon: 'robot', hidden: false } },
        { after: 'channels', item: { id: 'lrm-whatsapp', type: 'url', url: '/whatsapp/', open: 'frame', title: 'WhatsApp', icon: 'whatsapp', hidden: false } },
        { after: 'tasks', item: { id: 'lrm-browser', type: 'url', url: '/assets/brand/browser.html', open: 'frame', title: 'Browser', icon: 'globe', hidden: false } },
        { after: 'tasks', item: { id: 'lrm-activity', type: 'url', url: '/assets/brand/activity.html', open: 'frame', title: 'Activity', icon: 'flag', hidden: false } },
        { after: 'channels', item: { id: 'lrm-connections', type: 'url', url: '/assets/brand/connections.html', open: 'frame', title: 'Connections', icon: 'link', hidden: false } },
        { after: 'logs', item: { id: 'lrm-usage', type: 'url', url: '/assets/brand/usage.html', open: 'frame', title: 'Usage', icon: 'chart', hidden: false } },
    ];
    for (const page of pages) {
        if (doc.groups.some(g => g.items.some(i => i.id === page.item.id))) continue;
        const group = doc.groups.find(g => g.items.some(i => i.view === page.after)) || doc.groups[doc.groups.length - 1];
        const at = group.items.findIndex(i => i.view === page.after);
        group.items.splice(at + 1, 0, Object.assign({}, page.item));
        for (const g of doc.groups) for (const i of g.items) if ((page.hide || []).includes(i.view)) i.hidden = true;
    }
    return _lrmGroup(doc);
}

// Short labelled sections, easier to scan than one long list. Pages the owner added
// themselves keep their order in a last section; hidden pages stay hidden.
function _lrmGroup(doc) {
    const sections = [
        { id: 'chat', title: '', keys: ['chat', 'artifacts'] },
        { id: 'lrm-assistants', title: 'Assistants', keys: ['lrm-personal', 'agents', 'lrm-team'] },
        { id: 'lrm-knowledge', title: 'Knowledge', keys: ['lrm-vault', 'lrm-safe', 'skills', 'memory', 'knowledge'] },
        { id: 'lrm-connect', title: 'Connect', keys: ['lrm-whatsapp', 'lrm-connections', 'channels'] },
        { id: 'lrm-work', title: 'Work', keys: ['tasks', 'lrm-activity', 'lrm-browser'] },
        { id: 'lrm-settings', title: 'Settings', keys: ['config', 'lrm-usage', 'logs'] },
    ];
    const keyOf = (i) => (i.type === 'builtin' ? i.view : i.id);
    // Teammates is where the AI plans are signed in (Codex, Claude, Gemini...): a setting.
    // AI plans is a tab of Config now (Config → AI plans): no menu entry of its own.
    const retired = new Set(['lrm-teammates']);
    const all = doc.groups.flatMap(g => g.items);
    const used = new Set();
    const groups = sections.map(sec => ({ id: sec.id, title: sec.title, items: sec.keys.map(k => all.find(i => keyOf(i) === k)).filter(Boolean) }));
    groups.forEach(g => g.items.forEach(i => used.add(i)));
    all.forEach(i => { if (retired.has(keyOf(i))) used.add(i); });
    const rest = all.filter(i => !used.has(i));
    if (rest.length) groups.push({ id: 'lrm-more', title: 'More', items: rest });
    return { groups: groups.filter(g => g.items.length) };
}

function _menuMergeStock(saved) {""")

# Memory recall: OpenClaw's tighter default of 6 results (snippets are up to
# 500 characters each), so a lookup costs ~3k characters instead of ~5k.
for old, new in (('"description": "Maximum number of results to return (default: 10)",',
                  '"description": "Maximum number of results to return (default: 6)",'),
                 ('"default": 10', '"default": 6'),
                 ('max_results = args.get("max_results", 10)', 'max_results = args.get("max_results", 6)')):
    patch("agent/tools/memory/memory_search.py", old, new, root=app)

# Second brain: the knowledge wiki is kept as an Obsidian-compatible vault.
patch("skills/knowledge-wiki/SKILL.md", "## Core Operations", """## Vault conventions (Obsidian-compatible)

`knowledge/` doubles as an Obsidian vault, so keep every page valid Obsidian Markdown:
- Start each page with YAML properties: `tags`, `aliases`, `created`, `updated`, and `source` for ingested material.
- Link pages with Obsidian wikilinks, `[[page-slug]]` or `[[page-slug|Page title]]` (`[[category/page-slug]]` when two pages share a name); `[[page#Heading]]` links a section. The console's Vault page and Obsidian both follow them, show backlinks and draw the graph from them. Existing relative Markdown links still work; don't rewrite them just to convert.
- Tag with `#tags` or the `tags` property; use callouts (`> [!note]`) for key takeaways and `- [ ]` for open tasks.
- End a page with a `## Related` list of links; keep `index.md` as the map of content.
- For callouts, embeds and properties load the `obsidian-markdown` skill; for visual maps `json-canvas`; for table views `obsidian-bases`.

## Recall with few tokens

- Search before reading: `memory_search` covers memory and this vault and returns short snippets. Then read only the page, or the lines, you need.
- Never write knowledge/ files yourself: put finished results into the vault with `vault_update` (action propose, the full note). Lorium checks every proposal and the owner approves it; to update a note, propose its complete new version with the latest information.
- Never read the whole vault, or long pages end to end, to answer a question.

## Core Operations""", root=app)
# English only: the regenerated knowledge/index.md is headed in Chinese upstream.
patch("agent/knowledge/service.py", 'lines = ["# 知识库目录", ""]', 'lines = ["# Knowledge Index", ""]', root=app)
# Switch_model tool (deploy/agent-tools/model_switch, copied in by the Dockerfile):
# register it so every agent can change model when asked in chat.
patch("agent/tools/__init__.py",
      "from agent.tools.subagent.subagent import SubagentTool\n",
      "from agent.tools.subagent.subagent import SubagentTool\n"
      "from agent.tools.model_switch.model_switch import ModelSwitch\n"
      "from agent.tools.transcribe.transcribe import TranscribeAudio\n"
      "from agent.tools.teammates.teammate import AskTeammate\n"
      "from agent.tools.google_tool.google_tool import GoogleTool\n"
      "from agent.tools.whatsapp_tool.whatsapp_tool import WhatsAppTool\n"
      "from agent.tools.ask_agent.ask_agent import AskAgent\n"
      "from agent.tools.vault_update.vault_update import VaultUpdate\n"
      "from agent.tools.site_login.site_login import SiteLogin\n"
      "from agent.tools.chat_monitor.chat_monitor import ChatMonitor\n"
      "from agent.tools.team_memory.team_memory import TeamMemory\n", root=app)
patch("agent/tools/__init__.py", "    'SubagentTool',\n", "    'SubagentTool',\n    'ModelSwitch',\n    'TranscribeAudio',\n    'AskTeammate',\n    'GoogleTool',\n    'WhatsAppTool',\n    'AskAgent',\n    'VaultUpdate',\n    'SiteLogin',\n    'ChatMonitor',\n    'TeamMemory',\n", root=app)
# Scheduled messages from WhatsApp chats go back to WhatsApp through the
# gateway (deploy/agent-tools/whatsapp_relay.py, installed as channel/lrm_whatsapp_relay.py).
patch("agent/tools/scheduler/integration.py",
      'def _resolve_delivery_channel(channel_type: str, instance_id: str = "", receiver: str = ""):\n',
      'def _resolve_delivery_channel(channel_type: str, instance_id: str = "", receiver: str = ""):\n'
      '    if channel_type == "openai_api":\n'
      '        from channel.lrm_whatsapp_relay import relay_for\n'
      '        return relay_for(receiver)\n', root=app)
# Lock-down: settings the deployment owns can't be changed from the console.
# The password comes from the COWAGENT_WEB_PASSWORD secret; clearing it in the
# UI would turn login off on a public console whose agent can run commands.
patch("api/config.py", '"self_evolution_enabled", "web_password",', '"self_evolution_enabled",')
# The listen address is fixed by the compose file; changing it takes the site down.
patch("api/integrations.py",
      '        if "web_host" not in data and "web_port" not in data:\n',
      '        if "web_host" in data or "web_port" in data:\n'
      '            return _error("The listen address is managed by the LRMagent deployment.", "managed_by_deploy")\n'
      '        if "web_host" not in data and "web_port" not in data:\n')
# Updates arrive by redeploying the image; an in-place update would fight it.
patch("api/update.py",
      '            logger.info("[WebChannel] one-click update requested")\n',
      '            return json.dumps({"status": "error", "message": "LRMagent updates are installed by redeploying."})\n')
# Conversations over the chat API (WhatsApp, and anything holding the API token)
# come from outside the console: they run with workspace-write, unless a stricter
# setting applies, and can never be raised to full access that way.
patch("bridge/agent_bridge.py",
      'agent.apply_permission_mode(permission_mode or prefs.get("permission"))',
      'agent.apply_permission_mode('
      '("read-only" if (permission_mode or prefs.get("permission")) == "read-only" else "workspace-write") '
      'if str(session_id).startswith("openai:") else (permission_mode or prefs.get("permission")))', root=app)
# Usage: every model call's tokens are recorded per agent/model/channel
# (agent/lrm_usage.py) and served to the Usage page (channel/web/api/lrm_usage.py).
patch("agent/protocol/agent_stream.py",
      "        if snapshot:\n            self.run_usage = self._merge_usage(self.run_usage, snapshot)\n",
      "        if snapshot:\n            self.run_usage = self._merge_usage(self.run_usage, snapshot)\n"
      "            from agent.lrm_usage import record as _lrm_record_usage\n"
      "            _lrm_record_usage(self, snapshot)\n", root=app)
patch("web_channel.py", "\nURLS = (\n",
      "\nfrom channel.web.api.lrm_usage import LrmUsageHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_vault import LrmVaultHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_activity import LrmActivityHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_google import LrmGoogleCallbackHandler, LrmGoogleHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_browser import LrmBrowserHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_tts import LrmTtsHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_voice import LrmVoiceHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_chats import LrmChatsHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_vault_updates import LrmVaultUpdatesHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_busy import LrmBusyHandler  # noqa: F401,E402\n"
      "from channel.web.api.lrm_org import LrmOrgHandler  # noqa: F401,E402\n\nURLS = (\n")
patch("web_channel.py", "    '/api/update/status', 'UpdateStatusHandler',\n",
      "    '/api/update/status', 'UpdateStatusHandler',\n    '/api/lrm/usage', 'LrmUsageHandler',\n"
      "    '/api/lrm/vault', 'LrmVaultHandler',\n"
      "    '/api/lrm/activity', 'LrmActivityHandler',\n"
      "    '/api/lrm/google/callback', 'LrmGoogleCallbackHandler',\n"
      "    '/api/lrm/google', 'LrmGoogleHandler',\n"
      "    '/api/lrm/browser', 'LrmBrowserHandler',\n"
      "    '/api/lrm/tts', 'LrmTtsHandler',\n"
      "    '/api/lrm/voice', 'LrmVoiceHandler',\n"
      "    '/api/lrm/chats', 'LrmChatsHandler',\n"
      "    '/api/lrm/vault-updates', 'LrmVaultUpdatesHandler',\n"
      "    '/api/lrm/busy', 'LrmBusyHandler',\n"
      "    '/api/lrm/org', 'LrmOrgHandler',\n")
# Plain-language errors: a failed model call showed the provider's raw JSON in
# the chat; agent/lrm_errors.py (deploy/agent-tools) explains it instead. Logs keep the raw text.
patch("agent/protocol/agent_stream.py",
      '            self._emit_event("error", {"error": str(e)})\n',
      '            from agent.lrm_errors import friendly as _lrm_friendly\n'
      '            self._emit_event("error", {"error": _lrm_friendly(e, getattr(self.model, "model", None))})\n', root=app)
# Approvals and activity log (agent/lrm_activity.py): risky tool calls wait for the
# owner's OK, and every call is logged for the Activity page.
patch("agent/protocol/agent_stream.py",
      """        if not own_cards:
            self._emit_event("tool_execution_start", start_event)

        try:
            if not tool:
""",
      """        if not own_cards:
            self._emit_event("tool_execution_start", start_event)

        from agent.lrm_activity import gate as _lrm_gate, record as _lrm_record
        _lrm_refusal = _lrm_gate(self, tool_name, arguments, tool_id)
        if _lrm_refusal:
            refused = {"status": "error", "result": _lrm_refusal, "execution_time": 0}
            if own_cards:
                self._emit_event("tool_execution_start", start_event)
            self._emit_event("tool_execution_end", {"tool_call_id": tool_id, "tool_name": tool_name, **refused})
            _lrm_record(self, tool_name, arguments, refused)
            return refused

        try:
            if not tool:
""", root=app)
patch("agent/protocol/agent_stream.py",
      "            success = result.status == \"success\"\n            self._record_tool_result(tool_name, arguments, success)\n",
      "            success = result.status == \"success\"\n            self._record_tool_result(tool_name, arguments, success)\n"
      "            _lrm_record(self, tool_name, arguments, result_dict)\n", root=app)
patch("agent/protocol/agent_stream.py",
      "            # Record failure\n            self._record_tool_result(tool_name, arguments, False)\n",
      "            # Record failure\n            self._record_tool_result(tool_name, arguments, False)\n"
      "            _lrm_record(self, tool_name, arguments, error_result)\n", root=app)
# Watchable browser: agents hand logins, CAPTCHAs and 2FA to the owner, who can watch
# and drive on the console's Browser page (channel/web/api/lrm_browser.py).
patch("agent/tools/browser/browser_tool.py",
      '"For login/CAPTCHA/authorization etc., screenshot and ask the user for help. "',
      '"For login/CAPTCHA/2FA/authorization etc., ask the user to do that step themselves on the console\'s Browser page "'
      '"(they can watch the page live and take over), and continue when they say it is done. "', root=app)
# Sandbox (agent/lrm_sandbox.py): WhatsApp and API chats run shell commands in the
# sandbox container, away from LRMagent's secrets; background jobs stay console-only.
patch("agent/tools/bash/bash.py",
      "            if run_in_background:\n                # Ownership of temp_script_path passes to the registry",
      "            if run_in_background:\n"
      "                from agent.lrm_sandbox import sandboxed as _lrm_sandboxed\n"
      "                if _lrm_sandboxed(self):\n"
      "                    return ToolResult.fail(\"Background commands are not available in WhatsApp and API chats; \"\n"
      "                                           \"run it normally instead (it can take up to the timeout).\")\n"
      "                # Ownership of temp_script_path passes to the registry", root=app)
patch("agent/tools/bash/bash.py",
      """                result = self._run_streaming(
                    command,
                    timeout,
                    env,
                    dotenv_vars,
                )
""",
      """                from agent.lrm_sandbox import run as _lrm_sandbox_run
                result = _lrm_sandbox_run(self, command, timeout, env, dotenv_vars)
                if result is None:
                    result = self._run_streaming(command, timeout, env, dotenv_vars)
""", root=app)
# Logs: upstream masks settings named *key*/*secret* only, so the console
# password and the internal API token were written to run.log in clear text.
patch("config.py", 'if ("key" in k or "secret" in k) and isinstance(v, str):',
      'if any(word in k for word in ("key", "secret", "password", "token")) and isinstance(v, str):', root=app)
# Login lockout per visitor: behind Caddy every request came from one private address, so anyone's
# 5 wrong guesses locked the owner out. Only Caddy or loopback reach the console: trust their X-Forwarded-For.
patch("core/_common.py",
      'if addr in ("::1", "::ffff:127.0.0.1") or addr.startswith("127."):',
      'if addr in ("::1", "::ffff:127.0.0.1") or addr.startswith(("127.", "10.", "172.", "192.168.")):')
# Files the agent sends (send tool) reach chat-API replies as links to their
# local path; the WhatsApp gateway turns those into real attachments. Upstream
# only links files when a cloud URL exists, so self-hosted API replies lost them.
patch("agent/chat/service.py",
      '                url = data.get("url") or ""\n',
      '                url = data.get("url") or (data.get("path") if channel_type == "openai_api" else "") or ""\n', root=app)
# Your plans as chat models (agent/lrm_plans.py): a virtual custom provider shown while a plan is
# signed in; each request names its chat so the CLI keeps one session per conversation.
patch("models/custom_provider.py",
      '    if not providers or not provider_id:\n        return None\n    for p in providers:',
      '    if provider_id == "lrmplans":\n        from agent.lrm_plans import provider as _lrm_plans\n        return _lrm_plans()\n'
      '    if not providers or not provider_id:\n        return None\n    for p in providers:', root=app)
patch("api/sessions.py",
      '    except Exception as e:\n        logger.debug(f"[WebChannel] custom providers unavailable: {e}")\n\n    return catalog\n',
      '    except Exception as e:\n        logger.debug(f"[WebChannel] custom providers unavailable: {e}")\n\n'
      '    try:\n        from agent.lrm_plans import catalog_group as _lrm_plans_group\n'
      '        _plans = _lrm_plans_group()\n        if _plans:\n            catalog.append(_plans)\n'
      '        # Providers the owner retired (LRMAGENT_HIDDEN_PROVIDERS, e.g. deepseek once OpenRouter is in).\n'
      '        import os as _os\n'
      '        _hidden = {p.strip() for p in _os.environ.get("LRMAGENT_HIDDEN_PROVIDERS", "").split(",") if p.strip()}\n'
      '        catalog[:] = [g for g in catalog if g.get("id") not in _hidden]\n'
      '    except Exception as e:\n        logger.debug(f"[WebChannel] your plans unavailable: {e}")\n\n    return catalog\n')
patch("models/openai/openai_http_client.py",
      '        if extra_headers:\n            headers.update(extra_headers)\n        return headers\n',
      '        if extra_headers:\n            headers.update(extra_headers)\n'
      '        from agent.lrm_plans import headers_for as _lrm_plans_headers\n'
      '        headers.update(_lrm_plans_headers(url))\n        return headers\n', root=app)
patch("agent/protocol/agent_stream.py",
      '        _active_token = _ACTIVE_RUN_MODELS.set(_active_models | {_model_key})\n',
      '        _active_token = _ACTIVE_RUN_MODELS.set(_active_models | {_model_key})\n'
      '        from agent.lrm_plans import begin_run as _lrm_plans_begin, end_run as _lrm_plans_end\n'
      '        _lrm_plans_token = _lrm_plans_begin(self.model)\n', root=app)
patch("agent/protocol/agent_stream.py",
      '        finally:\n            _ACTIVE_RUN_MODELS.reset(_active_token)\n',
      '        finally:\n            _ACTIVE_RUN_MODELS.reset(_active_token)\n            _lrm_plans_end(_lrm_plans_token)\n', root=app)
# Skills from GitHub also accept what skill directories publish: an
# `npx skills add owner/repo` line or a skills.sh link (agent/lrm_skills.py).
patch("api/skills.py",
      '    if source == "github":\n        if value.startswith(',
      '    if source == "github":\n        from agent.lrm_skills import github_spec as _lrm_github_spec\n'
      '        value = _lrm_github_spec(value)\n        if value.startswith(')
patch("api/skills.py",
      '                spec = (body.get("spec") or name).strip()\n',
      '                spec = (body.get("spec") or name).strip()\n'
      '                from agent.lrm_skills import github_spec as _lrm_github_spec\n'
      '                spec = _lrm_github_spec(spec)\n')
patch("static/js/core/i18n.js", "skill_value_github: 'Repository or folder URL'",
      "skill_value_github: 'Repository, folder URL or npx command'")
patch("static/js/core/i18n.js", "skill_hint_github: 'A repository URL, a folder URL (/tree/branch/path), or owner/repo'",
      "skill_hint_github: 'A repository URL, a folder URL (/tree/branch/path), owner/repo, a skills.sh link, or an npx skills add command'")
patch("static/js/views/skills.js", "https://github.com/owner/repo/tree/main/skills/my-skill", "https://github.com/owner/repo  ·  npx skills add owner/repo")
# Skills installed from the console go to every agent (agent/lrm_skills.py).
patch("api/skills.py",
      '                    _record_staged_fields(staged["dir"], installed, service)\n',
      '                    _record_staged_fields(staged["dir"], installed, service)\n'
      '                    from agent.lrm_skills import share_with_all as _lrm_share\n'
      '                    _lrm_share(installed, staged["agent_id"])\n')
patch("api/skills.py",
      '                service.manager.refresh_skills()\n                # What the caller knows',
      '                service.manager.refresh_skills()\n'
      '                from agent.lrm_skills import share_with_all as _lrm_share\n'
      '                _lrm_share(result.installed, agent_id)\n                # What the caller knows')
# Chat notes (agent/lrm_memory.py) are the owner's: WhatsApp contacts and API
# callers neither find them in memory_search nor open them with memory_get.
patch("agent/tools/memory/memory_search.py",
      '            if not results:\n',
      '            from agent.lrm_memory import hidden_from as _lrm_hidden\n'
      '            _lrm_session = getattr(getattr(self, "model", None), "session_id", "")\n'
      '            results = [r for r in (results or []) if not _lrm_hidden(_lrm_session, r.path)]\n'
      '            if not results:\n', root=app)
patch("agent/tools/memory/memory_get.py",
      '        if not path:\n',
      '        from agent.lrm_memory import hidden_from as _lrm_hidden\n'
      '        if path and _lrm_hidden(getattr(getattr(self, "model", None), "session_id", ""), path):\n'
      '            return ToolResult.fail("Chat notes are private to the owner, so they can\'t be opened from this chat.")\n'
      '        if not path:\n', root=app)
# Chat API failures say why (out of credit, key rejected, ...) instead of a bare
# "failed", so the WhatsApp gateway can tell the owner (agent/lrm_errors.py).
patch("api/openai_compat.py",
      '        logger.exception("[OpenAI API] Chat completion failed")\n        raise OpenAIAPIError(\n'
      '            500, "CowAgent failed to complete the request.", "internal_error"\n        ) from error',
      '        logger.exception("[OpenAI API] Chat completion failed")\n'
      '        from agent.lrm_errors import friendly as _lrm_friendly\n        raise OpenAIAPIError(\n'
      '            500, _lrm_friendly(error), "internal_error"\n        ) from error')
patch("api/openai_compat.py", '"CowAgent failed to complete the request."', '"LRMagent failed to complete the request."', count="all")
patch("api/openai_compat.py", '"CowAgent timed out before producing a response."', '"LRMagent timed out before producing a response."')
# The owner's signed-in plans are the last fallback for the owner's own chats.
patch("bridge/agent_bridge.py",
      '            chain.append({"provider": provider, "model": model})\n        return {"chain": chain}\n',
      '            chain.append({"provider": provider, "model": model})\n'
      '        try:\n            from agent.lrm_plans import fallback_links as _lrm_plan_links\n'
      '            chain += [link for link in _lrm_plan_links(self, chain)\n'
      '                      if (link["provider"], link["model"]) not in seen]\n'
      '        except Exception as e:\n            logger.debug(f"[AgentBridge] plans fallback unavailable: {e}")\n'
      '        return {"chain": chain}\n', root=app)
patch("bridge/agent_bridge.py",
      '        if not isinstance(raw, dict) or not raw.get("enabled"):\n            return empty\n',
      '        if not isinstance(raw, dict) or not raw.get("enabled"):\n'
      '            raw = {"enabled": True, "chain": []}  # LRMagent: the plans below may still apply\n', root=app)
# Console pages on this server (the Vault's gesture mode) may ask for the camera;
# the browser still asks the owner. Other sites embedded in the menu get nothing new.
patch("static/js/core/menu.js",
      "        frame.setAttribute('referrerpolicy', 'no-referrer');\n",
      "        frame.setAttribute('referrerpolicy', 'no-referrer');\n"
      "        if (/^\\/(?!\\/)/.test(String(item.url))) frame.setAttribute('allow', 'camera; microphone; clipboard-write; autoplay');\n")
# No Chinese flash on reload: the templates carry Chinese text until applyI18n()
# swaps in English, a moment after first paint. brand.css keeps the page hidden until
# that has run (and reveals it after 1.5 s regardless).
patch("static/js/core/i18n.js", "function applyI18n() {\n",
      "function applyI18n() {\n    requestAnimationFrame(() => document.documentElement.classList.add('lrm-i18n'));\n")
# Dates as DD-MM-YYYY (the owner's format) on the Tasks page.
patch("static/js/views/tasks.js", "d.toLocaleString()",
      "(d.toLocaleDateString('en-GB').replace(/\\//g, '-') + ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }))", count="all")
# A custom provider may list several models ("models"), e.g. OpenRouter's: the chat
# picker offers them all, not only the default "model".
patch("api/sessions.py",
      '                cp_model = str(cp.get("model") or "").strip()\n                if cp_model:\n                    models.append(cp_model)\n',
      '                cp_model = str(cp.get("model") or "").strip()\n                if cp_model:\n                    models.append(cp_model)\n'
      '                models += [m for m in (cp.get("models") or []) if isinstance(m, str) and m.strip() and m not in models]\n')
# Keep trying: three rounds over the fallback chain instead of two (the owner asked that a
# turn goes on to other models until one answers).
patch("bridge/agent_bridge.py", "    _FALLBACK_MAX_PASSES = 2\n", "    _FALLBACK_MAX_PASSES = 3\n", root=app)

# Light only: the owner does not want dark mode (an earlier "dark" choice is ignored).
patch("static/js/core/theme.js",
      "let currentTheme = localStorage.getItem('cow_theme') || window.__COW_DEFAULT_THEME__ || 'light';\n",
      "let currentTheme = 'light';\n")
patch("static/js/core/theme.js",
      "    currentTheme = currentTheme === 'dark' ? 'light' : 'dark';\n",
      "    currentTheme = 'light';\n")

# Delegation: a chat with no team set (Lorium's own chats, the WhatsApp sessions that
# scheduled reports run in) works with all the owner's business agents; upstream refused
# Lorium's requests to CFO, HRD and Operations there. Sora (personal) is never a member.
patch("agent/tools/agent_delegate/agent_delegate.py",
      '                logger.warning(f"[AgentDelegate] Could not read team members: {exc}")\n'
      '                return []\n\n        policy = self._policy_safe()\n',
      '                logger.warning(f"[AgentDelegate] Could not read team members: {exc}")\n'
      '                return []\n'
      '            if not members:\n'
      '                try:\n'
      '                    members = [p.id for p in self.agent_bridge.agent_registry.list(include_disabled=False) if p.id != "personal"]\n'
      '                except Exception as exc:\n'
      '                    logger.warning(f"[AgentDelegate] Could not list the agents: {exc}")\n'
      '                    members = []\n'
      '\n        policy = self._policy_safe()\n', root=app)

# Redeploys wait for running work (agent/lrm_busy.py counts every agent run), and a
# scheduled report cut off by a restart is run again for up to 2 hours (upstream: 10 min,
# then the day's report was skipped).
_agent_py = app / "agent/protocol/agent.py"
_agent_src = _agent_py.read_text(encoding="utf-8")
if "class Agent:" not in _agent_src or "    def run_stream(self, user_message: str, on_event=None" not in _agent_src:
    sys.exit("agent/protocol/agent.py: Agent.run_stream moved")
_agent_py.write_text(_agent_src + """

# LRMagent: count runs in progress, so a redeploy waits for them (agent/lrm_busy.py).
_lrm_original_run_stream = Agent.run_stream


def _lrm_counted_run_stream(self, *args, **kwargs):
    from agent import lrm_busy
    with lrm_busy.running(getattr(self, "agent_id", "") or ""):
        return _lrm_original_run_stream(self, *args, **kwargs)


Agent.run_stream = _lrm_counted_run_stream
""", encoding="utf-8")
patch("agent/tools/scheduler/scheduler_service.py", "                if time_diff <= 600:\n", "                if time_diff <= 7200:\n", root=app)

# Scheduler tool: in English (models picked it less from the Chinese text), "act, don't
# promise", and from the console it can deliver to the owner's WhatsApp (the lrm-wa:<agent>
# recipients of channel/lrm_whatsapp_relay.py, as on the Tasks page).
_sched = "agent/tools/scheduler/scheduler_tool.py"
_text = (app / _sched).read_text(encoding="utf-8")
_start = _text.index('    description: str = (\n        "创建')
_end = _text.index("    params: dict = {", _start)
_desc = (
    "Create and manage scheduled tasks: anything that should happen at a time or on repeat (daily or weekly "
    "reports, reminders, checks at 12:00 and 17:00, a message in 2 hours). When the owner asks for one, CREATE IT "
    "NOW with this tool in the same reply; never only promise it. Actions: 'create' (name, message for a fixed "
    "text OR ai_task for work the agent does at that time, schedule_type, schedule_value, timezone), 'list', 'get', "
    "'delete', 'enable', 'disable' (task_id), 'list_recipients'. schedule_type: 'cron' for repeats (e.g. '0 12 * * *' "
    "every day 12:00, '0 12,17 * * 1-5' weekdays 12:00 and 17:00), 'interval' (seconds), 'once' (+10m, +2h, +1d or an "
    "ISO time). Use timezone 'Asia/Jakarta' unless the owner says otherwise. From the console, results come back to "
    "this chat unless you name a recipient: for the owner's WhatsApp call 'list_recipients' and pass the 'You (owner)' "
    "entry's channel_type, instance_id and receiver. Tasks appear on the console's Tasks page."
)
(app / _sched).write_text(_text[:_start] + "    description: str = " + repr(_desc) + "\n" + _text[_end:], encoding="utf-8")
for _old, _new in (
        ('"description": "操作类型: create(创建), list(列表), get(查询), delete(删除), enable(启用), disable(禁用)"',
         '"description": "What to do: create, list, list_recipients, get, delete, enable or disable"'),
        ('"description": "任务ID (用于 get/delete/enable/disable 操作)"', '"description": "Task id (for get, delete, enable, disable)"'),
        ('"description": "任务名称 (用于 create 操作)"', '"description": "Task name (for create), e.g. Daily report 12:00"'),
        ('"description": "固定消息内容 (与ai_task二选一)"', '"description": "A fixed message to send (or use ai_task instead)"'),
        ('"description": "Optional IANA timezone (e.g. Asia/Shanghai) used for cron wall-clock times and naive ISO timestamps"',
         '"description": "IANA timezone for cron times and plain ISO times, normally Asia/Jakarta"'),
):
    patch(_sched, _old, _new, root=app)
patch(_sched,
      "            target = self.recipient_store.get(target_instance, target_receiver)\n",
      "            target = self.recipient_store.get(target_instance, target_receiver)\n"
      "            if not target:\n"
      "                from channel.lrm_whatsapp_relay import task_recipient as _lrm_wa_recipient\n"
      "                target = _lrm_wa_recipient(target_instance, target_receiver)\n", root=app)
patch(_sched,
      "        recipients = self.recipient_store.list()\n        if not recipients:\n",
      "        try:\n"
      "            from channel.lrm_whatsapp_relay import task_recipients as _lrm_wa_recipients\n"
      "            recipients = _lrm_wa_recipients() + self.recipient_store.list()\n"
      "        except Exception:\n"
      "            recipients = self.recipient_store.list()\n"
      "        if not recipients:\n", root=app)

# Several recipients per scheduled task (channel/lrm_whatsapp_relay.py: clean_receivers,
# fanout): the agent works once, the result goes to each receiver, each one's outcome is
# noted in the run history ("Delivered: Reyner ✓, Hansel ✓").
_integ = "agent/tools/scheduler/integration.py"
patch(_integ,
      '\n        channel = _resolve_delivery_channel(channel_type, action.get("instance_id") or "", receiver)\n',
      '\n        channel = _resolve_delivery_channel(channel_type, action.get("instance_id") or "", receiver)\n'
      '        if channel is not None:\n'
      '            from channel.lrm_whatsapp_relay import fanout as _lrm_fanout\n'
      '            channel = _lrm_fanout(channel, action, channel_type)\n', count="all", root=app)
patch(_integ,
      '\n            channel = _resolve_delivery_channel(channel_type, action.get("instance_id") or "", receiver)\n',
      '\n            channel = _resolve_delivery_channel(channel_type, action.get("instance_id") or "", receiver)\n'
      '            if channel is not None:\n'
      '                from channel.lrm_whatsapp_relay import fanout as _lrm_fanout\n'
      '                channel = _lrm_fanout(channel, action, channel_type)\n', count="all", root=app)
patch(_integ, '                output_sink["preview"] = str(reply.content)\n',
      '                output_sink["preview"] = str(reply.content) + getattr(channel, "delivery_note", "")\n', root=app)
patch(_integ, '            output_sink["preview"] = str(content)\n',
      '            output_sink["preview"] = str(content) + getattr(channel, "delivery_note", "")\n', count="all", root=app)
# Tasks page: create and update accept action.receivers (checked against the allow-list)
patch("api/scheduler.py",
      '            if action_type == "send_message":\n                action["content"] = content\n',
      '            if action_in.get("receivers"):\n'
      '                from channel.lrm_whatsapp_relay import clean_receivers as _lrm_clean\n'
      '                try:\n'
      '                    action["receivers"] = _lrm_clean(action["instance_id"], action["receiver"], [r for r in action_in.get("receivers") if isinstance(r, str)])\n'
      '                except ValueError as _e:\n'
      '                    return json.dumps({"status": "error", "message": str(_e)})\n'
      '            if action_type == "send_message":\n                action["content"] = content\n')
patch("api/scheduler.py",
      '                updates["action"] = action\n',
      '                if action.get("receivers"):\n'
      '                    from channel.lrm_whatsapp_relay import clean_receivers as _lrm_clean\n'
      '                    try:\n'
      '                        action["receivers"] = _lrm_clean(action.get("instance_id"), action.get("receiver"), [r for r in action["receivers"] if isinstance(r, str)])\n'
      '                    except ValueError as _e:\n'
      '                        return json.dumps({"status": "error", "message": str(_e)})\n'
      '                updates["action"] = action\n')
# The agents' scheduler tool: receivers too
patch(_sched,
      '            "receiver": {\n                "type": "string",\n                "description": "Trusted target receiver returned by list_recipients. It cannot be an arbitrary user ID."\n            },\n',
      '            "receiver": {\n                "type": "string",\n                "description": "Trusted target receiver returned by list_recipients. It cannot be an arbitrary user ID."\n            },\n'
      '            "receivers": {\n                "type": "array", "items": {"type": "string"},\n'
      '                "description": "Further receivers (from list_recipients, same instance_id) who get the same result: e.g. the owner, a designated contact and a group. The agent works once."\n            },\n', root=app)
patch(_sched,
      '        # 针对钉钉单聊，额外存储 sender_staff_id\n',
      '        _extra = [r for r in (kwargs.get("receivers") or []) if isinstance(r, str) and r.strip()]\n'
      '        if _extra:\n'
      '            from channel.lrm_whatsapp_relay import clean_receivers as _lrm_clean\n'
      '            try:\n'
      '                action["receivers"] = _lrm_clean(instance_id, receiver, _extra)\n'
      '            except ValueError as _e:\n'
      '                return _SchedulerError(f"Error: {_e}")\n'
      '        # 针对钉钉单聊，额外存储 sender_staff_id\n', root=app)

# The scheduler's replies are read by the agents and often repeated to the owner: English.
_sched_text = (app / _sched).read_text(encoding="utf-8")
for _zh, _en in (
        ("任务描述 (与message二选一)，描述到点时需要 Agent 执行的任务，例如执行什么操作、交付什么内容，", "What the agent should do each time it runs (or use message instead): the work and what to report. "),
        ("无需重复声明调度时间（由 schedule 决定）。", ""),
        ("调度类型 (用于 create 操作): cron(cron表达式), interval(固定间隔秒数), once(一次性)", "cron (repeating, a cron expression), interval (every N seconds) or once"),
        ("调度值: cron表达式/间隔秒数/时间(+5s,+10m,+1h或ISO格式)", "The cron expression, the seconds, or the time (+5s, +10m, +1h, +1d or ISO)"),
        ("错误: 定时任务系统未初始化", "Error: the scheduler is not running"),
        ("未知操作: ", "Unknown action: "), ("操作失败: ", "Failed: "),
        ("错误: 缺少任务名称 (name)", "Error: missing name"),
        ("错误: 必须提供 message（固定消息）或 ai_task（AI任务）之一", "Error: give message (a fixed text) or ai_task (work to do)"),
        ("错误: message 和 ai_task 只能提供其中一个", "Error: give message or ai_task, not both"),
        ("错误: 缺少调度类型 (schedule_type)", "Error: missing schedule_type"),
        ("错误: 缺少调度值 (schedule_value)", "Error: missing schedule_value"),
        ("错误: 无效的调度配置 - type: ", "Error: invalid schedule - type: "),
        ("错误: 无法获取当前对话上下文", "Error: no conversation context"),
        ("错误: 定时时间已过去 - ", "Error: that time has passed - "),
        ("一次性任务只能安排在将来的时间，请改用一个将来的时间", " A one-time task needs a future time: use a later time"),
        ("或 +10m 这类相对时间。", " or a relative time like +10m."),
        ("💬 固定消息: ", "💬 Message: "), ("🤖 AI任务: ", "🤖 Task: "),
        ("🔇 静默模式: 执行后不会推送结果", "🔇 Silent: the result is not sent"),
        ("✅ 定时任务创建成功", "✅ Scheduled task created"),
        ("📋 任务ID: ", "📋 Task id: "), ("📝 名称: ", "📝 Name: "), ("⏰ 调度: ", "⏰ Schedule: "),
        ("👤 接收者: ", "👤 Delivered to: "), ("🕐 下次执行: ", "🕐 Next run: "),
        ("📋 暂无定时任务", "📋 No scheduled tasks"), ("📋 定时任务列表 (共 ", "📋 Scheduled tasks ("), (" 个)", ")"),
        (" | 下次: ", " | next: "), ("错误: 缺少任务ID (task_id)", "Error: missing task_id"),
        ("错误: 任务 '", "Error: task '"), ("' 不存在", "' does not exist"),
        ('"启用"', '"enabled"'), ('"禁用"', '"disabled"'), ('"从未执行"', '"never"'), ("📋 任务详情", "📋 Task"),
        ("名称: ", "Name: "), ("状态: ", "Status: "), ("调度: ", "Schedule: "), ("接收者: ", "Delivered to: "),
        ("消息: ", "Message: "), ("下次执行: ", "Next run: "), ("上次执行: ", "Last run: "), ("创建时间: ", "Created: "),
        ("✅ 任务 '", "✅ Task '"), ("已删除", "deleted"), ("已启用", "enabled"), ("已禁用", "disabled"),
        ('"每天 9:00"', '"every day 9:00"'), ('"每小时"', '"every hour"'), ('"每30分钟"', '"every 30 minutes"'),
        ("每 {days} 天", "every {days} days"), ("每 {hours} 小时", "every {hours} hours"),
        ("每 {minutes} 分钟", "every {minutes} minutes"), ("每 {seconds} 秒", "every {seconds} seconds"),
        ("一次性 (", "once ("), ('"一次性"', '"once"'), ('"群聊"', '"group"'), ('"用户"', '"user"'),
        ("'未知'", "'unknown'"), ('"未知"', '"unknown"'),
):
    _sched_text = _sched_text.replace(_zh, _en)
(app / _sched).write_text(_sched_text, encoding="utf-8")

# Browser: logins survive the browser closing itself (agent/lrm_browser_keep.py).
# Session cookies (no expiry) are lost when Chromium closes after its idle timeout, which
# signed the owner out of their apps; they are saved on close and put back on launch.
patch("agent/tools/browser/browser_service.py",
      '        logger.info("[Browser] Browser ready")\n',
      '        logger.info("[Browser] Browser ready")\n'
      '        try:\n'
      '            from agent.lrm_browser_keep import restore_cookies as _lrm_restore\n'
      '            _lrm_restore(self._context)\n'
      '        except Exception as _e:\n'
      '            logger.debug(f"[Browser] session cookies not restored: {_e}")\n', root=app)
patch("agent/tools/browser/browser_service.py",
      '        would never return.\n        """\n        self._cancel_idle_timer()\n',
      '        would never return.\n        """\n        self._cancel_idle_timer()\n'
      '        try:\n'
      '            from agent.lrm_browser_keep import save_cookies as _lrm_save\n'
      '            _lrm_save(self._context)\n'
      '        except Exception as _e:\n'
      '            logger.debug(f"[Browser] cookies not saved: {_e}")\n', root=app)

# Tasks: LRMagent's WhatsApp numbers as delivery channels in the task form (upstream listed none);
# channel/lrm_whatsapp_relay.py supplies them, and delivery goes through its relay.
patch("agent/tools/scheduler/integration.py",
      "    instance_id = (instance_id or \"\").strip()\n    if not instance_id:\n        return \"\"\n    try:\n        from channel.channel_instances import get_instance\n",
      "    instance_id = (instance_id or \"\").strip()\n    if not instance_id:\n        return \"\"\n    if instance_id.startswith(\"lrm-wa:\"):\n        return instance_id[len(\"lrm-wa:\"):]\n    try:\n        from channel.channel_instances import get_instance\n", root=app)
patch("api/scheduler.py",
      "            return json.dumps({\"status\": \"success\", \"instances\": instances}, ensure_ascii=False)\n",
      "            from channel.lrm_whatsapp_relay import task_instances as _lrm_wa_instances\n"
      "            instances = _lrm_wa_instances() + instances\n"
      "            return json.dumps({\"status\": \"success\", \"instances\": instances}, ensure_ascii=False)\n")
patch("api/scheduler.py",
      "            recipients = get_recipient_store().list()\n            instance_meta = self._instance_meta()\n",
      "            from channel.lrm_whatsapp_relay import task_recipients as _lrm_wa_recipients\n"
      "            recipients = get_recipient_store().list() + _lrm_wa_recipients()\n            instance_meta = self._instance_meta()\n")
patch("api/scheduler.py",
      "                item[\"instance_name\"] = (meta or {}).get(\"name\") or inst_id\n",
      "                item[\"instance_name\"] = item.get(\"instance_name\") or (meta or {}).get(\"name\") or inst_id\n")
patch("api/scheduler.py",
      "                    target = get_recipient_store().get(new_instance, new_receiver)\n",
      "                    from channel.lrm_whatsapp_relay import task_recipient as _lrm_wa_recipient\n"
      "                    target = get_recipient_store().get(new_instance, new_receiver) or _lrm_wa_recipient(new_instance, new_receiver)\n")
patch("api/scheduler.py",
      "            target = get_recipient_store().get(instance_id, receiver)\n",
      "            from channel.lrm_whatsapp_relay import task_recipient as _lrm_wa_recipient\n"
      "            target = get_recipient_store().get(instance_id, receiver) or _lrm_wa_recipient(instance_id, receiver)\n")
patch("static/js/views/tasks-modal.js",
      "        return { value: key, label: name, hint: truncateRecipientId(r.receiver) };\n",
      "        return { value: key, label: name, hint: r.hint || truncateRecipientId(r.receiver) };\n")

# OpenRouter reserves credit for a request's whole max_tokens (the model maximum when unset), so a
# small balance got 402 for a reply costing cents: cap it (LRMAGENT_OPENROUTER_MAX_TOKENS).
patch("models/openai_compatible_bot.py",
      '            # Add max_tokens if specified\n'
      '            if kwargs.get("max_tokens"):\n'
      '                request_params["max_tokens"] = kwargs["max_tokens"]\n',
      '            # Add max_tokens if specified\n'
      '            if kwargs.get("max_tokens"):\n'
      '                request_params["max_tokens"] = kwargs["max_tokens"]\n'
      '            if "openrouter.ai" in str(api_config.get("api_base") or ""):\n'
      '                import os as _lrm_os\n'
      '                _cap = int(_lrm_os.environ.get("LRMAGENT_OPENROUTER_MAX_TOKENS") or 8192)\n'
      '                request_params["max_tokens"] = min(int(request_params.get("max_tokens") or _cap), _cap)\n',
      root=app)
# AI plans (sign-ins for Codex, Claude Code, Gemini, ...) is a tab of Config, next to Models,
# instead of its own menu page.
patch("templates/views/config.html",
      '                                    <button id="config-tab-integrations" onclick="switchConfigTab(\'integrations\')"',
      '                                    <button id="config-tab-plans" onclick="switchConfigTab(\'plans\')"\n'
      '                                            class="config-tab px-4 py-1.5 rounded-md text-xs font-medium cursor-pointer transition-colors duration-150">\n'
      '                                        <i class="fas fa-robot mr-1.5"></i><span>AI plans</span>\n'
      '                                    </button>\n'
      '                                    <button id="config-tab-integrations" onclick="switchConfigTab(\'integrations\')"')
patch("templates/views/config.html",
      '                            <div id="config-panel-integrations" class="grid gap-6 hidden">',
      '                            <div id="config-panel-plans" class="hidden">\n'
      '                                <p class="text-sm text-slate-500 mb-3">Your own subscriptions (ChatGPT, Claude, Google, xAI) as models and coding teammates. '
      'Sign in once here; signed-in plans appear under <b>Your plans</b> in the model picker.</p>\n'
      '                                <iframe id="lrm-plans-frame" title="AI plans" style="width:100%;height:calc(100vh - 230px);min-height:520px;border:1px solid var(--lrm-line,#dbdbe5);border-radius:12px;background:#fff"></iframe>\n'
      '                            </div>\n'
      '                            <div id="config-panel-integrations" class="grid gap-6 hidden">')
patch("static/js/views/config.js",
      "    ['basic', 'models', 'integrations'].forEach(name => {",
      "    ['basic', 'models', 'plans', 'integrations'].forEach(name => {")
patch("static/js/views/config.js",
      "    if (tab === 'integrations') loadIntegrationsView();\n",
      "    if (tab === 'integrations') loadIntegrationsView();\n"
      "    if (tab === 'plans') { const f = document.getElementById('lrm-plans-frame'); if (f && !f.src) f.src = '/teammates/'; }\n")
print("LRM brand applied")
