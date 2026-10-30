"""Apply the LRM brand and look to the CowAgent web console at image build time.

Every edit must match upstream, so an upstream change fails the build instead
of silently shipping the stock CowAgent look or name.
"""
import json
import pathlib
import sys

app = pathlib.Path(sys.argv[1])
theme = json.loads(pathlib.Path(sys.argv[2]).read_text())
web = app / "channel" / "web"
NAME = "LRM Agents"
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

# ---- Name: every visible "CowAgent" in the console and its /config title.
for rel in ("chat.html", "templates/layout/sidebar.html", "templates/layout/login.html",
            "templates/views/chat.html", "static/js/chat/new-chat.js", "static/js/chat/state.js",
            "static/js/chat/send.js", "static/js/boot.js", "static/js/views/shares.js",
            "static/js/core/i18n.js", "api/config.py"):
    patch(rel, "CowAgent", NAME, count="all")
# The built-in agent's fallback name, used when config.json has not been written
# yet (upstream then ignores DEFAULT_AGENT_NAME on the Agents page).
patch("agent/registry.py", 'name=builtin_name or "CowAgent",', f'name=builtin_name or "{NAME}",', root=app)
print("LRM brand applied")
