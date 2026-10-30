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

# ---- Font and stylesheet: Plus Jakarta Sans first, brand sheet after themes.css.
patch("chat.html", "sans: ['Inter',", "sans: ['\"Plus Jakarta Sans\"', 'Inter',")
patch("chat.html",
      '<link rel="stylesheet" href="/assets/vendor/fonts/inter/inter.css">',
      '<link rel="stylesheet" href="/assets/vendor/fonts/inter/inter.css">\n'
      '    <link rel="stylesheet" href="/assets/brand/plus-jakarta-sans.css">')
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
      "    whatsapp: 'fa-comments', star: 'fa-star',")
patch("static/js/core/menu.js",
      "function menuMerge(saved) {",
      """function menuMerge(saved) {
    return _lrmEnsureWhatsApp(_menuMergeStock(saved));
}

function _lrmEnsureWhatsApp(doc) {
    // LRMagent pages: WhatsApp after Channels, Usage after Logs.
    const pages = [
        { after: 'channels', item: { id: 'lrm-whatsapp', type: 'url', url: '/whatsapp/', open: 'frame', title: 'WhatsApp', icon: 'whatsapp', hidden: false } },
        { after: 'logs', item: { id: 'lrm-usage', type: 'url', url: '/assets/brand/usage.html', open: 'frame', title: 'Usage', icon: 'chart', hidden: false } },
    ];
    for (const page of pages) {
        if (doc.groups.some(g => g.items.some(i => i.id === page.item.id))) continue;
        const group = doc.groups.find(g => g.items.some(i => i.view === page.after)) || doc.groups[doc.groups.length - 1];
        const at = group.items.findIndex(i => i.view === page.after);
        group.items.splice(at + 1, 0, Object.assign({}, page.item));
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
- Link pages with relative Markdown links, `[Page title](../category/page.md)`, not `[[wikilinks]]`: the console's knowledge graph only follows Markdown links, and Obsidian reads both.
- End a page with a `## Related` list of links; keep `index.md` as the map of content.
- For callouts, embeds and properties load the `obsidian-markdown` skill; for visual maps `json-canvas`; for table views `obsidian-bases`.

## Recall with few tokens

- Search before reading: `memory_search` covers memory and this vault and returns short snippets. Then read only the page, or the lines, you need.
- Never read the whole vault, or long pages end to end, to answer a question.

## Core Operations""", root=app)
# ---- switch_model tool (deploy/agent-tools/model_switch, copied in by the Dockerfile):
# register it so every agent can change model when asked in chat.
patch("agent/tools/__init__.py",
      "from agent.tools.subagent.subagent import SubagentTool\n",
      "from agent.tools.subagent.subagent import SubagentTool\n"
      "from agent.tools.model_switch.model_switch import ModelSwitch\n"
      "from agent.tools.transcribe.transcribe import TranscribeAudio\n", root=app)
patch("agent/tools/__init__.py", "    'SubagentTool',\n", "    'SubagentTool',\n    'ModelSwitch',\n    'TranscribeAudio',\n", root=app)
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
# WhatsApp chats come from people outside the console: unless a chat has its own
# setting, they run with workspace-write instead of the console's default.
patch("bridge/agent_bridge.py",
      'agent.apply_permission_mode(permission_mode or prefs.get("permission"))',
      'agent.apply_permission_mode(permission_mode or prefs.get("permission") or ('
      '"workspace-write" if str(session_id).startswith("openai:conversation:whatsapp:") else None))', root=app)
# ---- Usage: every model call's tokens are recorded per agent/model/channel
# (agent/lrm_usage.py) and served to the Usage page (channel/web/api/lrm_usage.py).
patch("agent/protocol/agent_stream.py",
      "        if snapshot:\n            self.run_usage = self._merge_usage(self.run_usage, snapshot)\n",
      "        if snapshot:\n            self.run_usage = self._merge_usage(self.run_usage, snapshot)\n"
      "            from agent.lrm_usage import record as _lrm_record_usage\n"
      "            _lrm_record_usage(self, snapshot)\n", root=app)
patch("web_channel.py", "\nURLS = (\n",
      "\nfrom channel.web.api.lrm_usage import LrmUsageHandler  # noqa: F401,E402\n\nURLS = (\n")
patch("web_channel.py", "    '/api/update/status', 'UpdateStatusHandler',\n",
      "    '/api/update/status', 'UpdateStatusHandler',\n    '/api/lrm/usage', 'LrmUsageHandler',\n")
# ---- Logs: upstream masks settings named *key*/*secret* only, so the console
# password and the internal API token were written to run.log in clear text.
patch("config.py", 'if ("key" in k or "secret" in k) and isinstance(v, str):',
      'if any(word in k for word in ("key", "secret", "password", "token")) and isinstance(v, str):', root=app)
print("LRM brand applied")
