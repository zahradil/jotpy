from __future__ import annotations

import nh3
from markdown_it import MarkdownIt
from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound

_ALLOWED_TAGS = {
    "address",
    "article",
    "aside",
    "footer",
    "header",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hgroup",
    "main",
    "nav",
    "section",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "hr",
    "li",
    "menu",
    "ol",
    "p",
    "pre",
    "ul",
    "a",
    "abbr",
    "b",
    "bdi",
    "bdo",
    "br",
    "cite",
    "code",
    "data",
    "dfn",
    "em",
    "i",
    "kbd",
    "mark",
    "q",
    "rb",
    "rp",
    "rt",
    "rtc",
    "ruby",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
    "var",
    "wbr",
    "caption",
    "col",
    "colgroup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "img",
}

_ATTRIBUTES = {
    "a": {"href", "name", "target"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "span": {"class"},
    "pre": {"class"},
}

def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _token_class(ttype) -> str | None:
    if ttype in Token.Comment:
        return "hljs-comment"
    if ttype in Token.Keyword:
        return "hljs-keyword"
    if ttype in Token.String:
        return "hljs-string"
    if ttype in Token.Number:
        return "hljs-number"
    if ttype in Token.Name.Function or ttype in Token.Name.Class:
        return "hljs-title"
    if ttype in Token.Name.Builtin:
        return "hljs-built_in"
    if ttype in Token.Name.Tag:
        return "hljs-name"
    if ttype in Token.Name.Attribute:
        return "hljs-attr"
    if ttype in Token.Name.Decorator:
        return "hljs-meta"
    if ttype in Token.Name.Variable:
        return "hljs-variable"
    if ttype in Token.Literal:
        return "hljs-literal"
    return None


def _lexer(language: str):
    try:
        return get_lexer_by_name(language)
    except ClassNotFound:
        return None


def _highlight(text: str, language: str) -> str:
    lexer = _lexer(language)
    if lexer is None:
        return escape_html(text)
    parts: list[str] = []
    for ttype, value in lex(text, lexer):
        escaped = escape_html(value)
        css = _token_class(ttype)
        if css:
            parts.append(f'<span class="{css}">{escaped}</span>')
        else:
            parts.append(escaped)
    return "".join(parts)


def _render_code(text: str, info: str) -> str:
    if text.endswith("\n"):
        text = text[:-1]
    language = (info or "").strip().split()[0] if info and info.strip() else ""
    if language == "mermaid":
        return f'<pre class="mermaid">{escape_html(text)}</pre>'
    if language and _lexer(language) is not None:
        css = f' class="hljs language-{escape_html(language)}"'
        body = _highlight(text, language)
    else:
        css = ' class="hljs"'
        body = escape_html(text)
    return f"<pre><code{css}>{body}</code></pre>"


def _render_fence(_renderer, tokens, idx, options, env) -> str:
    token = tokens[idx]
    return _render_code(token.content, token.info or "")


def _render_code_block(_renderer, tokens, idx, options, env) -> str:
    token = tokens[idx]
    return _render_code(token.content, "")


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    if attr != "class":
        return value
    classes = value.split()
    if tag == "code":
        kept = [item for item in classes if item == "hljs" or item.startswith("language-")]
    elif tag == "span":
        kept = [item for item in classes if item.startswith("hljs")]
    elif tag == "pre":
        kept = [item for item in classes if item == "mermaid"]
    else:
        kept = []
    if not kept:
        return None
    return " ".join(kept)


_CLEANER = nh3.Cleaner(
    tags=_ALLOWED_TAGS,
    attributes=_ATTRIBUTES,
    attribute_filter=_attribute_filter,
    url_schemes={"http", "https", "mailto"},
    link_rel="noopener noreferrer",
    set_tag_attribute_values={"a": {"target": "_blank", "rel": "noopener noreferrer"}},
    clean_content_tags={"script", "style"},
    strip_comments=True,
)

_MARKDOWN = MarkdownIt("gfm-like", {"breaks": True, "html": True, "linkify": True})
_MARKDOWN.add_render_rule("fence", _render_fence)
_MARKDOWN.add_render_rule("code_block", _render_code_block)


def render_markdown(markdown: str) -> str:
    raw = _MARKDOWN.render(markdown or "")
    return _CLEANER.clean(raw)
