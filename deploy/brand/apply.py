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


# ---- Color theme: built-in, listed first after the stock one, selected by default.
patch("static/js/core/themes.js",
      "const BUILTIN_COLOR_THEMES = [\n",
      "const BUILTIN_COLOR_THEMES = [\n    " + json.dumps(theme) + ",\n")
patch("static/js/core/themes.js",
      "localStorage.getItem(COLOR_THEME_KEY) || DEFAULT_COLOR_THEME;",
      "localStorage.getItem(COLOR_THEME_KEY) || 'lrm';")

# Light appearance by default, before first paint and after scripts load.
patch("chat.html", "localStorage.getItem('cow_theme') || 'dark';", "localStorage.getItem('cow_theme') || 'light';")
patch("static/js/core/theme.js", "|| window.__COW_DEFAULT_THEME__ || 'dark';", "|| window.__COW_DEFAULT_THEME__ || 'light';")

# ---- Stylesheet and layout script: the OpenBot-style shell (Inter, the stock font)
# loads after themes.css and after the console's own scripts.
patch("chat.html", '<script defer src="/assets/js/workspace.js"></script>',
      '<script defer src="/assets/js/workspace.js"></script>\n    <script defer src="/assets/brand/shell.js"></script>')
patch("chat.html",
      '<link rel="stylesheet" href="/assets/css/themes.css">',
      '<link rel="stylesheet" href="/assets/css/themes.css">\n'
      '    <link rel="stylesheet" href="/assets/brand/brand.css">')

# ---- English only: every language lookup resolves to English.
for page in ("chat.html", "templates/guest/page.html"):
    patch(page,
          "window.__cowNormalizeLang__ = function(raw) {",
          "window.__cowNormalizeLang__ = function(raw) {\n        return 'en';  // LRM: English only")

# The Settings language picker offers Chinese too; leave English as its only option.
patch("static/js/views/config.js",
      "[{ value: 'zh', label: '简体中文' }, { value: 'zh-Hant', label: '繁體中文' }, { value: 'en', label: 'English' }]",
      "[{ value: 'en', label: 'English' }]")

# ---- Brand marks.
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

# ---- Name: every visible upstream product name in the console and its /config title.
for rel in ("chat.html", "templates/layout/sidebar.html", "templates/layout/login.html",
            "templates/views/chat.html", "static/js/chat/new-chat.js", "static/js/chat/state.js",
            "static/js/chat/send.js", "static/js/boot.js", "static/js/views/shares.js",
            "static/js/core/i18n.js", "api/config.py"):
    patch(rel, "CowAgent", NAME, count="all")
# The built-in agent's fallback name, used when config.json has not been written
# yet (upstream then ignores DEFAULT_AGENT_NAME on the Agents page).
patch("agent/registry.py", 'name=builtin_name or "CowAgent",', f'name=builtin_name or "{NAME}",', root=app)

# ---- WhatsApp: a built-in menu entry after Channels that opens the gateway page
# (/whatsapp/) inside the console. The sidebar is drawn from a menu document, so
# the entry is merged into it (and restored if a saved menu lacks it).
patch("static/js/core/menu.js",
      "    star: 'fa-star',",
      "    whatsapp: 'fa-comments', vault: 'fa-gem', personal: 'fa-circle-dot', star: 'fa-star',")
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
    return doc;
}

function _menuMergeStock(saved) {""")

# ---- Memory recall: OpenClaw's tighter default of 6 results (snippets are up to
# 500 characters each), so a lookup costs ~3k characters instead of ~5k.
for old, new in (('"description": "Maximum number of results to return (default: 10)",',
                  '"description": "Maximum number of results to return (default: 6)",'),
                 ('"default": 10', '"default": 6'),
                 ('max_results = args.get("max_results", 10)', 'max_results = args.get("max_results", 6)')):
    patch("agent/tools/memory/memory_search.py", old, new, root=app)

# ---- Second brain: the knowledge wiki is kept as an Obsidian-compatible vault.
patch("skills/knowledge-wiki/SKILL.md", "## Core Operations", """## Vault conventions (Obsidian-compatible)

`knowledge/` doubles as an Obsidian vault, so keep every page valid Obsidian Markdown:
- Start each page with YAML properties: `tags`, `aliases`, `created`, `updated`, and `source` for ingested material.
- Link pages with Obsidian wikilinks, `[[page-slug]]` or `[[page-slug|Page title]]` (`[[category/page-slug]]` when two pages share a name); `[[page#Heading]]` links a section. The console's Vault page and Obsidian both follow them, show backlinks and draw the graph from them. Existing relative Markdown links still work; don't rewrite them just to convert.
- Tag with `#tags` or the `tags` property; use callouts (`> [!note]`) for key takeaways and `- [ ]` for open tasks.
- End a page with a `## Related` list of links; keep `index.md` as the map of content.
- For callouts, embeds and properties load the `obsidian-markdown` skill; for visual maps `json-canvas`; for table views `obsidian-bases`.

## Recall with few tokens

- Search before reading: `memory_search` covers memory and this vault and returns short snippets. Then read only the page, or the lines, you need.
- Never read the whole vault, or long pages end to end, to answer a question.

## Core Operations""", root=app)
# ---- English only: the regenerated knowledge/index.md is headed in Chinese upstream.
patch("agent/knowledge/service.py", 'lines = ["# 知识库目录", ""]', 'lines = ["# Knowledge Index", ""]', root=app)
# ---- switch_model tool (deploy/agent-tools/model_switch, copied in by the Dockerfile):
# register it so every agent can change model when asked in chat.
patch("agent/tools/__init__.py",
      "from agent.tools.subagent.subagent import SubagentTool\n",
      "from agent.tools.subagent.subagent import SubagentTool\n"
      "from agent.tools.model_switch.model_switch import ModelSwitch\n"
      "from agent.tools.transcribe.transcribe import TranscribeAudio\n"
      "from agent.tools.teammates.teammate import AskTeammate\n"
      "from agent.tools.google_tool.google_tool import GoogleTool\n"
      "from agent.tools.whatsapp_tool.whatsapp_tool import WhatsAppTool\n"
      "from agent.tools.ask_agent.ask_agent import AskAgent\n", root=app)
patch("agent/tools/__init__.py", "    'SubagentTool',\n", "    'SubagentTool',\n    'ModelSwitch',\n    'TranscribeAudio',\n    'AskTeammate',\n    'GoogleTool',\n    'WhatsAppTool',\n    'AskAgent',\n", root=app)
# ---- Scheduled messages from WhatsApp chats go back to WhatsApp through the
# gateway (deploy/agent-tools/whatsapp_relay.py, installed as channel/lrm_whatsapp_relay.py).
patch("agent/tools/scheduler/integration.py",
      'def _resolve_delivery_channel(channel_type: str, instance_id: str = "", receiver: str = ""):\n',
      'def _resolve_delivery_channel(channel_type: str, instance_id: str = "", receiver: str = ""):\n'
      '    if channel_type == "openai_api":\n'
      '        from channel.lrm_whatsapp_relay import relay_for\n'
      '        return relay_for(receiver)\n', root=app)
# ---- Lock-down: settings the deployment owns can't be changed from the console.
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
# ---- Usage: every model call's tokens are recorded per agent/model/channel
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
      "from channel.web.api.lrm_voice import LrmVoiceHandler  # noqa: F401,E402\n\nURLS = (\n")
patch("web_channel.py", "    '/api/update/status', 'UpdateStatusHandler',\n",
      "    '/api/update/status', 'UpdateStatusHandler',\n    '/api/lrm/usage', 'LrmUsageHandler',\n"
      "    '/api/lrm/vault', 'LrmVaultHandler',\n"
      "    '/api/lrm/activity', 'LrmActivityHandler',\n"
      "    '/api/lrm/google/callback', 'LrmGoogleCallbackHandler',\n"
      "    '/api/lrm/google', 'LrmGoogleHandler',\n"
      "    '/api/lrm/browser', 'LrmBrowserHandler',\n"
      "    '/api/lrm/tts', 'LrmTtsHandler',\n"
      "    '/api/lrm/voice', 'LrmVoiceHandler',\n")
# ---- Plain-language errors: a failed model call showed the provider's raw JSON in
# the chat; agent/lrm_errors.py (deploy/agent-tools) explains it instead. Logs keep the raw text.
patch("agent/protocol/agent_stream.py",
      '            self._emit_event("error", {"error": str(e)})\n',
      '            from agent.lrm_errors import friendly as _lrm_friendly\n'
      '            self._emit_event("error", {"error": _lrm_friendly(e, getattr(self.model, "model", None))})\n', root=app)
# ---- Approvals and activity log (agent/lrm_activity.py): risky tool calls wait for the
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
# ---- Watchable browser: agents hand logins, CAPTCHAs and 2FA to the owner, who can watch
# and drive on the console's Browser page (channel/web/api/lrm_browser.py).
patch("agent/tools/browser/browser_tool.py",
      '"For login/CAPTCHA/authorization etc., screenshot and ask the user for help. "',
      '"For login/CAPTCHA/2FA/authorization etc., ask the user to do that step themselves on the console\'s Browser page "'
      '"(they can watch the page live and take over), and continue when they say it is done. "', root=app)
# ---- Sandbox (agent/lrm_sandbox.py): WhatsApp and API chats run shell commands in the
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
# ---- Logs: upstream masks settings named *key*/*secret* only, so the console
# password and the internal API token were written to run.log in clear text.
patch("config.py", 'if ("key" in k or "secret" in k) and isinstance(v, str):',
      'if any(word in k for word in ("key", "secret", "password", "token")) and isinstance(v, str):', root=app)
# ---- Login lockout per visitor: behind Caddy every request comes from Caddy's
# private Docker address, which upstream doesn't treat as a proxy, so all
# visitors shared one lockout and 5 wrong guesses by anyone locked the owner out.
# The console is reachable only through Caddy or the host's loopback port, so a
# private-network peer is our proxy and its X-Forwarded-For is the real client.
patch("core/_common.py",
      'if addr in ("::1", "::ffff:127.0.0.1") or addr.startswith("127."):',
      'if addr in ("::1", "::ffff:127.0.0.1") or addr.startswith(("127.", "10.", "172.", "192.168.")):')
# ---- Files the agent sends (send tool) reach chat-API replies as links to their
# local path; the WhatsApp gateway turns those into real attachments. Upstream
# only links files when a cloud URL exists, so self-hosted API replies lost them.
patch("agent/chat/service.py",
      '                url = data.get("url") or ""\n',
      '                url = data.get("url") or (data.get("path") if channel_type == "openai_api" else "") or ""\n', root=app)
# ---- Your plans as chat models: the teammates service answers chat requests on the
# owner's signed-in plans (agent/lrm_plans.py). It is a virtual custom provider that
# never lands in config, listed in the model picker while a plan is signed in, and
# each request names its chat so the CLI keeps one session per conversation.
patch("models/custom_provider.py",
      '    if not providers or not provider_id:\n        return None\n    for p in providers:',
      '    if provider_id == "lrmplans":\n        from agent.lrm_plans import provider as _lrm_plans\n        return _lrm_plans()\n'
      '    if not providers or not provider_id:\n        return None\n    for p in providers:', root=app)
patch("api/sessions.py",
      '    except Exception as e:\n        logger.debug(f"[WebChannel] custom providers unavailable: {e}")\n\n    return catalog\n',
      '    except Exception as e:\n        logger.debug(f"[WebChannel] custom providers unavailable: {e}")\n\n'
      '    try:\n        from agent.lrm_plans import catalog_group as _lrm_plans_group\n'
      '        _plans = _lrm_plans_group()\n        if _plans:\n            catalog.append(_plans)\n'
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
# ---- Skills from GitHub also accept what skill directories publish: an
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
# ---- Skills installed from the console go to every agent (agent/lrm_skills.py).
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
# ---- Chat notes (agent/lrm_memory.py) are the owner's: WhatsApp contacts and API
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
# ---- Chat API failures say why (out of credit, key rejected, ...) instead of a bare
# "failed", so the WhatsApp gateway can tell the owner (agent/lrm_errors.py).
patch("api/openai_compat.py",
      '        logger.exception("[OpenAI API] Chat completion failed")\n        raise OpenAIAPIError(\n'
      '            500, "CowAgent failed to complete the request.", "internal_error"\n        ) from error',
      '        logger.exception("[OpenAI API] Chat completion failed")\n'
      '        from agent.lrm_errors import friendly as _lrm_friendly\n        raise OpenAIAPIError(\n'
      '            500, _lrm_friendly(error), "internal_error"\n        ) from error')
patch("api/openai_compat.py", '"CowAgent failed to complete the request."', '"LRMagent failed to complete the request."', count="all")
patch("api/openai_compat.py", '"CowAgent timed out before producing a response."', '"LRMagent timed out before producing a response."')
# ---- The owner's signed-in plans are the last fallback for the owner's own chats.
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
# ---- Console pages on this server (the Vault's gesture mode) may ask for the camera;
# the browser still asks the owner. Other sites embedded in the menu get nothing new.
patch("static/js/core/menu.js",
      "        frame.setAttribute('referrerpolicy', 'no-referrer');\n",
      "        frame.setAttribute('referrerpolicy', 'no-referrer');\n"
      "        if (/^\\/(?!\\/)/.test(String(item.url))) frame.setAttribute('allow', 'camera; microphone; clipboard-write; autoplay');\n")
print("LRM brand applied")
