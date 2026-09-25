from __future__ import annotations

import json

from jotpy.auth import OWNER_TOKEN_KEY
from jotpy.markdown_html import escape_html

_TOKEN_SCRIPT = f"<script>window.__OWNER_TOKEN_KEY__ = {json.dumps(OWNER_TOKEN_KEY)};</script>"
_THEME_SCRIPT = (
    "<script>document.querySelectorAll('.theme-toggle').forEach(function(b){"
    "b.innerHTML=window.__themeIcon(document.documentElement.getAttribute('data-theme')||'dark')"
    "});</script>"
)


def render_simple_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape_html(title)}</title>
    <link rel="stylesheet" href="/static/styles.css" />
    <script src="/static/theme.js"></script>
  </head>
  <body class="page-shell simple-page">
    <main class="simple-page-content">{body}</main>
  </body>
</html>"""


def render_auth_page(mode: str) -> str:
    title = "Set password" if mode == "setup" else "Sign in"
    heading = "Set the password" if mode == "setup" else "Enter the password"
    hint = (
        "First startup. This becomes the single owner password for the instance."
        if mode == "setup"
        else "This instance uses one password and per-device tokens."
    )
    confirm = (
        '<input id="confirmPassword" name="confirmPassword" type="password" autocomplete="new-password" placeholder="Confirm password" minlength="8" required />'
        if mode == "setup"
        else ""
    )
    autocomplete = "new-password" if mode == "setup" else "current-password"
    button = "Save password" if mode == "setup" else "Sign in"
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <link rel="stylesheet" href="/static/styles.css" />
    <script src="/static/theme.js"></script>
  </head>
  <body class="page-shell auth-shell" data-auth-mode="{mode}">
    <button type="button" class="text-button theme-toggle auth-theme-toggle" aria-label="Toggle theme"></button>
    <main class="auth-layout">
      <h1>{heading}</h1>
      <p class="auth-hint">{hint}</p>
      <p class="auth-error hidden" id="auth-error"></p>
      <form id="auth-form" class="auth-form">
        <input id="password" name="password" type="password" autocomplete="{autocomplete}" placeholder="Password" minlength="8" required autofocus />
        {confirm}
        <div class="auth-actions">
          <button type="submit">{button}</button>
        </div>
      </form>
    </main>
    {_TOKEN_SCRIPT}
    {_THEME_SCRIPT}
    <script src="/static/login.js" defer></script>
  </body>
</html>"""


def render_app_shell(page: str, title: str, data: dict | None = None) -> str:
    data = data or {}
    attrs = " ".join(
        part
        for part in (
            f'data-page="{page}"',
            f'data-note-id="{escape_html(data["noteId"])}"' if data.get("noteId") else "",
            f'data-sheet-id="{escape_html(data["sheetId"])}"' if data.get("sheetId") else "",
            f'data-share-id="{escape_html(data["shareId"])}"' if data.get("shareId") else "",
            f'data-share-access="{escape_html(data["shareAccess"])}"' if data.get("shareAccess") else "",
            'data-too-large="1"' if data.get("tooLarge") else "",
        )
        if part
    )
    script = "/static/sheet.js" if page == "sheet" else "/static/app.js"
    mermaid = ""
    if page not in ("list", "sheet"):
        mermaid = """
    <script type="module">
      import mermaid from "/static/mermaid/mermaid.esm.min.mjs";
      mermaid.initialize({ startOnLoad: false, theme: document.documentElement.getAttribute("data-theme") === "light" ? "default" : "dark" });
      window.__mermaid = mermaid;
      if (window.__renderMermaid) { var c = document.getElementById("previewContent"); if (c) window.__renderMermaid(c); }
    </script>"""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape_html(title)}</title>
    <link rel="stylesheet" href="/static/styles.css" />
    <script src="/static/theme.js"></script>
  </head>
  <body class="page-shell app-page" {attrs}>
    <div id="app"></div>
    {_TOKEN_SCRIPT}
    {_THEME_SCRIPT}
    <script src="/static/components.js"></script>{mermaid}
    <script src="{script}" defer></script>
  </body>
</html>"""
