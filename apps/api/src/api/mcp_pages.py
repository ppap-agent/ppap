"""The pages PPAP serves on the authorization origin: consent, approvals and connections.

They carry no script and load nothing remote, so their look is one stylesheet inlined in each page. Every colour, the
font, the type scale and the corners come from the Web design tokens (`apps/web/tokens`, the only source of visual
values); this module only lays them out as `design.md` describes: a grey canvas, one white card, Feishu blue for the
one commitment, quiet text for everything else, and the same in dark mode.
"""

from __future__ import annotations

import json
import re
from functools import cache
from html import escape
from pathlib import Path

from fastapi.responses import HTMLResponse

TOKENS = Path(__file__).resolve().parents[4] / "apps" / "web" / "tokens"
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}
# The page's own layout. Every value that is a design decision is a token variable; what remains is geometry.
LAYOUT = """
*{box-sizing:border-box}
html{color-scheme:light dark}
body{margin:0;min-height:100vh;background:var(--color-canvas);color:var(--color-foreground);font-family:var(--font-sans);
font-size:var(--text-sm);line-height:var(--leading-sm);-webkit-font-smoothing:antialiased;display:flex;justify-content:center;padding:48px 16px}
main{width:100%;max-width:480px;align-self:flex-start;background:var(--color-card);border-radius:var(--radius-card);padding:24px}
.brand{margin:0 0 16px;color:var(--color-muted-foreground);font-size:var(--text-xs);line-height:var(--leading-xs);font-weight:600;letter-spacing:.04em}
h1{margin:0 0 12px;font-size:var(--text-xl);line-height:var(--leading-xl);font-weight:600}
h2{margin:0;font-size:var(--text-sm);line-height:var(--leading-sm);font-weight:600}
p{margin:0 0 8px}
strong{font-weight:600}
small,.muted{color:var(--color-muted-foreground);font-size:var(--text-xs);line-height:var(--leading-xs)}
a{color:var(--color-primary);text-decoration:none}
a:hover{text-decoration:underline}
ul{margin:4px 0 12px;padding:0;list-style:none}
li{padding:8px 0;border-top:1px solid var(--color-border);overflow-wrap:anywhere}
li:first-child{border-top:0}
.notice{margin:16px 0 0;padding:8px 12px;background:var(--color-secondary);border-radius:calc(var(--radius-control) - 2px);color:var(--color-muted-foreground);
font-size:var(--text-xs);line-height:var(--leading-xs)}
form{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px;margin:20px 0 0}
form>:not(button){flex-basis:100%}
form>p{margin:4px 0}
label{display:flex;align-items:center;gap:8px;padding:8px 0;border-top:1px solid var(--color-border);cursor:pointer}
form>label:first-of-type{border-top:0}
input[type=checkbox]{width:16px;height:16px;margin:0;accent-color:var(--color-primary)}
button{font:inherit;height:32px;padding:0 16px;border-radius:calc(var(--radius-control) - 2px);border:1px solid var(--color-input);
background:var(--color-card);color:var(--color-foreground);cursor:pointer}
button:hover{background:var(--color-secondary)}
button:focus-visible,a:focus-visible,input:focus-visible{outline:2px solid var(--color-ring);outline-offset:2px}
button[value=approve],button.primary{order:2;background:var(--color-primary);border-color:var(--color-primary);color:var(--color-primary-foreground)}
button[value=approve]:hover,button.primary:hover{background:var(--color-primary-strong);border-color:var(--color-primary-strong)}
section{padding:12px 0;border-top:1px solid var(--color-border)}
section:first-of-type{border-top:0}
section form{margin:8px 0 0;justify-content:flex-start}
main.wide{max-width:1040px}
h3{margin:20px 0 8px;font-size:var(--text-lg);line-height:var(--leading-lg);font-weight:600}
dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:12px 0 0}
dt{color:var(--color-muted-foreground)}
dd{margin:0;overflow-wrap:anywhere}
.scroll{overflow-x:auto;margin:0 -4px;padding:0 4px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-weight:400;color:var(--color-muted-foreground);padding:8px 12px 8px 0;border-bottom:1px solid var(--color-border);white-space:nowrap}
td{padding:10px 12px 10px 0;border-bottom:1px solid var(--color-border);vertical-align:top;overflow-wrap:anywhere}
.quote{margin:0;padding:10px 12px;background:var(--color-secondary);border-radius:calc(var(--radius-control) - 2px);overflow-wrap:anywhere}
.quote ol{margin:0;padding-left:20px}
.quote ol>li{padding:2px 0;border:0}
.quote ul{margin:4px 0;padding-left:18px;list-style:disc}
.quote ul>li{padding:1px 0;border:0}
.tag{display:inline-block;padding:0 6px;border-radius:calc(var(--radius-control) - 4px);font-size:var(--text-xs);line-height:20px;
background:var(--color-secondary);color:var(--color-muted-foreground);white-space:nowrap}
.tag.ng{color:var(--color-destructive);background:color-mix(in srgb,var(--color-destructive) 10%,transparent)}
.tag.pass{color:var(--color-success);background:color-mix(in srgb,var(--color-success) 10%,transparent)}
.tag.warn{color:var(--color-warning);background:color-mix(in srgb,var(--color-warning) 12%,transparent)}
@media (max-width:520px){body{padding:16px}main{padding:20px}}
"""


def _values(name: str) -> dict[str, object]:
    found: dict[str, object] = {}

    def walk(node, path):
        if isinstance(node, dict) and "$value" in node:
            value = node["$value"]
            if isinstance(value, dict) and "hex" in value:
                value = value["hex"]
            elif isinstance(value, dict) and "unit" in value:
                value = f"{value['value']}{value['unit']}"
            elif isinstance(value, list):
                value = ", ".join(f'"{part}"' if " " in part else part for part in value)
            found["-".join(path)] = value
        elif isinstance(node, dict):
            for key, child in node.items():
                if not key.startswith("$"):
                    walk(child, [*path, key])

    walk(json.loads((TOKENS / f"{name}.tokens.json").read_text(encoding="utf-8")), [])
    return found


@cache
def stylesheet() -> str:
    shared, light, dark = _values("shared"), _values("light"), _values("dark")
    names = {"font-sans": "--font-sans", **{f"rounded-{k}": f"--radius-{k}" for k in ("control", "card")}}
    base = [f"{names[key]}:{value}" for key, value in shared.items() if key in names]
    base += [f"--text-{key[10:]}:{value}" for key, value in shared.items() if key.startswith("font-size-")]
    base += [f"--leading-{key[13:]}:{value}" for key, value in shared.items() if key.startswith("font-leading-")]

    def colours(values):
        return ";".join(f"--{key}:{value}" for key, value in values.items() if key.startswith("color-"))

    return f":root{{{';'.join(base)};{colours(light)}}}@media (prefers-color-scheme:dark){{:root{{{colours(dark)}}}}}" + LAYOUT.replace("\n", "")


def page(title: str, body: str, status: int = 200, *, wide: bool = False) -> HTMLResponse:
    """A complete page around `body`, which callers build from escaped values only."""
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)} · PPAP</title><style>{stylesheet()}</style>"
        f'<main{" class=wide" if wide else ""}><p class="brand">PPAP</p><h1>{escape(title)}</h1>{body}</main></html>',
        status_code=status,
        headers=HEADERS,
    )


def lines(summary: list[str]) -> str:
    """An approval's summary: sentences as paragraphs, and the items under them ("· …") as one list."""
    out, items = [], []
    for line in summary:
        if line.startswith("· "):
            items.append(f"<li>{escape(line[2:])}</li>")
            continue
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items = []
        out.append(f"<p>{escape(line)}</p>")
    if items:
        out.append("<ul>" + "".join(items) + "</ul>")
    return "".join(out)


_CLAUSES = re.compile(r"(?<=[；;])\s*|\n+")
_ITEMS = re.compile(r"[、]")


def prose(text: str) -> str:
    """A clause or a reason as the Web would set it: one point per line, and a long enumeration as a list.

    Clauses split at line breaks and semicolons; a point that introduces three or more items ("…：A、B、C") shows
    them as a list under it. Only the layout changes: every word stays as written."""
    points = [part.strip() for part in _CLAUSES.split(text.strip()) if part and part.strip()]
    rendered = []
    for point in points:
        lead, colon, rest = point.partition("：")
        items = [item.strip() for item in _ITEMS.split(rest)] if colon else []
        if len(items) >= 3:
            tail = ""
            if items[-1].endswith(("等。", "等")):
                tail = "等" if items[-1].endswith("等") else "等。"
                items[-1] = items[-1][: -len(tail)]
            body = "".join(f"<li>{escape(item)}</li>" for item in items if item)
            rendered.append(f"{escape(lead)}：<ul>{body}</ul>" + (f"<span class=muted>{escape(tail)}</span>" if tail else ""))
        else:
            rendered.append(escape(point))
    if len(rendered) == 1:
        return rendered[0]
    return "<ol>" + "".join(f"<li>{item}</li>" for item in rendered) + "</ol>"


TAGS = {"阻断": "ng", "警告": "warn", "待正式审核核对": "warn", "符合": "pass", "不符合": "ng", "待审核": "warn", "证据不足以判定": "warn", "没有检查完": "warn", "需要处理": "warn", "提醒": "warn"}


def _cell(value) -> str:
    """A table cell or fact value. A short status word is shown as a tag; everything is text, escaped."""
    text = "—" if value is None or value == "" else str(value)
    if text in TAGS:
        return f'<span class="tag {TAGS[text]}">{escape(text)}</span>'
    return escape(text)


def details(blocks: list[dict], closing: str = "") -> str:
    """What the person is deciding on, as the Web shows it: facts, tables and quoted text, then one notice.

    Blocks come from the confirmation the person approves, so they are rendered exactly as recorded and never re-read."""
    out, notes = [], []
    for block in blocks:
        title = f"<h3>{escape(block['title'])}</h3>" if block.get("title") else ""
        kind = block.get("kind")
        if kind == "facts":
            rows = "".join(f"<dt>{escape(label)}</dt><dd>{_cell(value)}</dd>" for label, value in block["rows"])
            out.append(f"{title}<dl>{rows}</dl>")
        elif kind == "table" and block.get("rows"):
            # A column with nothing in any row says nothing; leave it out.
            keep = [i for i in range(len(block["head"])) if any(row[i] not in (None, "", "—") for row in block["rows"])]
            head = "".join(f"<th>{escape(block['head'][i])}</th>" for i in keep)
            body = "".join("<tr>" + "".join(f"<td>{_cell(row[i])}</td>" for i in keep) + "</tr>" for row in block["rows"])
            more = f'<p class="muted">另有 {block["more"]} 项未列出。</p>' if block.get("more") else ""
            out.append(f'{title}<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{more}')
        elif kind == "text" and block.get("text"):
            out.append(f'{title}<div class="quote">{prose(block["text"])}</div>')
        elif kind == "note" and block.get("text"):
            notes.append(block["text"])
    if closing:
        notes.append(closing)
    if notes:
        out.append(f'<p class="notice">{"".join(escape(note) for note in notes)}</p>')
    return "".join(out)
