"""Apply the Daily Dose look to the CowAgent web console at image build time.

Every edit must match exactly once, so an upstream change fails the build
instead of silently shipping the stock look.
"""
import json
import pathlib
import sys

web = pathlib.Path(sys.argv[1])
theme = json.loads(pathlib.Path(sys.argv[2]).read_text())


def patch(rel, old, new):
    path = web / rel
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        sys.exit(f"{rel}: expected one match for {old!r}, found {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# Built-in theme, listed right after the stock one and selected by default.
patch("static/js/core/themes.js",
      "const BUILTIN_COLOR_THEMES = [\n",
      "const BUILTIN_COLOR_THEMES = [\n    " + json.dumps(theme) + ",\n")
patch("static/js/core/themes.js",
      "localStorage.getItem(COLOR_THEME_KEY) || DEFAULT_COLOR_THEME;",
      "localStorage.getItem(COLOR_THEME_KEY) || 'dailydose';")

# Light appearance by default, before first paint and after scripts load.
patch("chat.html", "localStorage.getItem('cow_theme') || 'dark';", "localStorage.getItem('cow_theme') || 'light';")
patch("static/js/core/theme.js", "|| window.__COW_DEFAULT_THEME__ || 'dark';", "|| window.__COW_DEFAULT_THEME__ || 'light';")

# Poppins first in the Tailwind sans stack, then the finishing sheet after themes.css.
patch("chat.html", "sans: ['Inter',", "sans: ['Poppins', 'Inter',")
patch("chat.html",
      '<link rel="stylesheet" href="/assets/vendor/fonts/inter/inter.css">',
      '<link rel="stylesheet" href="/assets/vendor/fonts/inter/inter.css">\n'
      '    <link rel="stylesheet" href="/assets/vendor/fonts/poppins/poppins.css">')
patch("chat.html",
      '<link rel="stylesheet" href="/assets/css/themes.css">',
      '<link rel="stylesheet" href="/assets/css/themes.css">\n'
      '    <link rel="stylesheet" href="/assets/css/dailydose.css">')
print("Daily Dose theme applied")
