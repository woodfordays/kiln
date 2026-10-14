#!/usr/bin/env python3
"""Compile design/ember-tokens.json into web/css/tokens.css.

Follows the Ember design-system compile rules: the first theme is the :root
default, further themes override under [data-theme], one .text-* class per type
style, one @font-face per font file.

Usage: python3 scripts/gen_tokens_css.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "design" / "ember-tokens.json"
OUT = ROOT / "web" / "css" / "tokens.css"

ALIAS = re.compile(r"^\{([A-Za-z0-9][A-Za-z0-9_.-]*)\}$")


def css_value(v):
    if isinstance(v, str):
        m = ALIAS.match(v)
        if m:
            return f"var(--{m.group(1)})"
    return str(v)


def themed(tokens, theme_ids):
    """Split tokens into {theme_id: [(name, value)]}; plain strings belong to the first theme."""
    out = {t: [] for t in theme_ids}
    for tok in tokens:
        val = tok["value"]
        if isinstance(val, dict):
            for tid in theme_ids:
                if tid in val:
                    out[tid].append((tok["name"], css_value(val[tid])))
        else:
            out[theme_ids[0]].append((tok["name"], css_value(val)))
    return out


def main():
    t = json.loads(SRC.read_text())
    themes = [th["id"] for th in t["color"]["themes"]]
    first = themes[0]
    lines = [f"/* {t['name']} — generated from tokens.json by scripts/gen_tokens_css.py */", ""]

    for font in t["type"].get("fonts", []):
        fname = Path(font["file"]).name
        lines += [
            "@font-face {",
            f'  font-family: "{font["family"]}";',
            f'  src: url("../fonts/{fname}") format("woff2");',
            f'  font-weight: {font.get("weight", "400")};',
            f'  font-style: {font.get("style", "normal")};',
            "  font-display: swap;",
            "}",
        ]
    lines.append("")

    colors = themed(t["color"]["tokens"], themes)
    shadows = themed(t.get("shadow", {}).get("tokens", []), themes)
    for tid in themes:
        sel = f':root, [data-theme="{tid}"]' if tid == first else f'[data-theme="{tid}"]'
        lines.append(sel + " {")
        decls = colors[tid] + shadows[tid]
        if tid != first:
            # aliases must be re-declared so they resolve against this theme's values
            names = {n for n, _ in decls}
            for tok in t["color"]["tokens"]:
                v = tok["value"]
                if isinstance(v, str) and ALIAS.match(v) and tok["name"] not in names:
                    decls.append((tok["name"], css_value(v)))
        for name, val in decls:
            lines.append(f"  --{name}: {val};")
        lines.append("}")
    lines.append("")

    lines.append(":root {")
    for fam in ("spacing", "radius", "size", "zIndex"):
        for tok in t.get(fam, {}).get("tokens", []):
            lines.append(f"  --{tok['name']}: {css_value(tok['value'])};")
    for key, stack in t["type"].get("families", {}).items():
        lines.append(f"  --font-{key}: {stack};")
    lines.append("}")
    lines.append("")

    for group in t["type"].get("groups", []):
        fam = group.get("family", "sans")
        for s in group["styles"]:
            lines.append(f".{s['name']} {{")
            lines.append(f"  font-family: var(--font-{s.get('family', fam)});")
            lines.append(f"  font-size: {s['fontSize']};")
            lines.append(f"  line-height: {s['lineHeight']};")
            lines.append(f"  font-weight: {s['fontWeight']};")
            if s.get("letterSpacing"):
                lines.append(f"  letter-spacing: {s['letterSpacing']};")
            lines.append("}")
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
