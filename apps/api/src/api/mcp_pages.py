"""The pages PPAP serves on the authorization origin: consent, approvals and connections.

They carry no script and load nothing remote, so their look is one stylesheet inlined in each page. Every colour, the
font, the type scale and the corners come from the Web design tokens (`apps/web/tokens`, the only source of visual
values); this module only lays them out as `design.md` describes: a grey canvas, one white card, Feishu blue for the
one commitment, quiet text for everything else, and the same in dark mode.
"""

from __future__ import annotations

import json
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


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """A complete page around `body`, which callers build from escaped values only."""
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)} · PPAP</title><style>{stylesheet()}</style>"
        f'<main><p class="brand">PPAP</p><h1>{escape(title)}</h1>{body}</main></html>',
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
