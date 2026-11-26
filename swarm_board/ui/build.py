#!/usr/bin/env python3
"""Assemble swarm_board/ui/src/* into self-contained swarm_board/ui/index.html."""
from __future__ import annotations

import os
import re
import sys

sys.dont_write_bytecode = True

UI_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(UI_DIR, "src")
OUT_PATH = os.path.join(UI_DIR, "index.html")
SIZE_LIMIT = 153_600

CSS_FILES = ["style.css", "style2.css", "style3.css", "style4.css"]
JS_FILES = [
    "core.js",
    "live.js",
    "bar.js",
    "md.js",
    "tokens.js",
    "tokens2.js",
    "map.js",
    "layouts.js",
    "layouts2.js",
    "mapcards.js",
    "reader.js",
    "find.js",
    "compare.js",
    "views.js",
    "views2.js",
    "stats.js",
    "stats2.js",
    "ui.js",
    "events.js",
]

_REGEX_PREV_KEYWORDS = frozenset(
    {
        "return",
        "throw",
        "case",
        "delete",
        "void",
        "typeof",
        "in",
        "instanceof",
        "new",
        "else",
        "do",
        "yield",
        "await",
        "of",
    }
)
_REGEX_PREV_CHARS = frozenset("=([,:;!&|?{}~^+-*/%<>")
_NO_ASI_PREV = frozenset("{([,;=&|?:!<>~^*.")


def read_src(name: str) -> str:
    with open(os.path.join(SRC_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def minify_css(css: str) -> str:
    """Safely strip CSS comments and redundant whitespace outside quoted strings."""
    out: list[str] = []
    i, n = 0, len(css)
    while i < n:
        ch = css[i]
        if ch in ("'", '"'):
            q = ch
            j = i + 1
            while j < n:
                if css[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if css[j] == q:
                    j += 1
                    break
                j += 1
            out.append(css[i:j])
            i = j
        elif ch == "/" and i + 1 < n and css[i + 1] == "*":
            end = css.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif ch in " \t\r\n":
            while i < n and css[i] in " \t\r\n":
                i += 1
            prev = out[-1][-1] if out and out[-1] else ""
            nxt = css[i] if i < n else ""
            if prev and nxt and prev not in "{}:;,>" and nxt not in "{};,>":
                out.append(" ")
        elif ch == ";" and i + 1 < n:
            j = i + 1
            while j < n and css[j] in " \t\r\n":
                j += 1
            if j < n and css[j] == "}":
                i = j
            else:
                out.append(";")
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out).strip()


def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"


def minify_js(js: str) -> str:
    """Token-aware JS minifier preserving strings, templates, regexes, and ASI."""
    out: list[str] = []
    i, n = 0, len(js)
    state = "code"  # code | sq | dq | tpl
    tpl_brace_stack: list[int] = []
    prev_tok = ""
    prev_word = ""
    last_ch = ""
    prev2_ch = ""

    def emit(s: str) -> None:
        out.append(s)

    def push_code(ch: str) -> None:
        nonlocal prev_tok, prev_word, last_ch, prev2_ch
        prev_tok = ch
        prev_word = ""
        prev2_ch, last_ch = last_ch, ch

    while i < n:
        ch = js[i]

        if state in ("sq", "dq"):
            q = "'" if state == "sq" else '"'
            if ch == "\\" and i + 1 < n:
                emit(js[i : i + 2])
                i += 2
                continue
            emit(ch)
            i += 1
            if ch == q:
                state = "code"
                push_code(q)
            continue

        if state == "tpl":
            if ch == "\\" and i + 1 < n:
                emit(js[i : i + 2])
                i += 2
                continue
            if ch == "`":
                emit(ch)
                i += 1
                state = "code"
                push_code("`")
                continue
            if ch == "$" and i + 1 < n and js[i + 1] == "{":
                emit("${")
                i += 2
                tpl_brace_stack.append(1)
                state = "code"
                push_code("{")
                continue
            emit(ch)
            i += 1
            continue

        if ch in ("'", '"', "`"):
            emit(ch)
            i += 1
            state = "sq" if ch == "'" else "dq" if ch == '"' else "tpl"
            continue

        if ch == "/" and i + 1 < n and js[i + 1] == "/":
            nl = js.find("\n", i + 2)
            i = n if nl < 0 else nl
            continue

        if ch == "/" and i + 1 < n and js[i + 1] == "*":
            end = js.find("*/", i + 2)
            seg_end = n if end < 0 else end + 2
            nl_in_comment = "\n" in js[i:seg_end]
            i = seg_end
            if out and i < n and js[i] not in " \t\r\n" and js[i : i + 2] not in ("//", "/*"):
                nxt = js[i]
                if nl_in_comment and not (
                    last_ch in _NO_ASI_PREV
                    or nxt in "}),.;:?"
                    or (last_ch in "+-" and prev2_ch != last_ch)
                ):
                    emit("\n")
                elif (
                    (_is_ident_char(last_ch) and _is_ident_char(nxt))
                    or (last_ch == "+" and nxt == "+")
                    or (last_ch == "-" and nxt == "-")
                    or (last_ch == "/" and nxt in "/*")
                ):
                    emit(" ")
            continue

        if ch == "/" and (
            prev_tok == ""
            or prev_tok in _REGEX_PREV_CHARS
            or prev_word in _REGEX_PREV_KEYWORDS
        ):
            j = i + 1
            in_class = False
            while j < n:
                rc = js[j]
                if rc == "\\" and j + 1 < n:
                    j += 2
                    continue
                if rc == "[":
                    in_class = True
                elif rc == "]" and in_class:
                    in_class = False
                elif rc == "/" and not in_class:
                    j += 1
                    break
                j += 1
            while j < n and js[j].isalpha():
                j += 1
            emit(js[i:j])
            i = j
            push_code("/")
            continue

        if ch == "{" and tpl_brace_stack:
            tpl_brace_stack[-1] += 1
            emit("{")
            i += 1
            push_code("{")
            continue

        if ch == "}" and tpl_brace_stack:
            tpl_brace_stack[-1] -= 1
            if tpl_brace_stack[-1] == 0:
                tpl_brace_stack.pop()
                if out and out[-1] == ";":
                    out.pop()
                emit("}")
                i += 1
                state = "tpl"
                push_code("}")
                continue
            if out and out[-1] == ";":
                out.pop()
            emit("}")
            i += 1
            push_code("}")
            continue

        if ch == "}" and out and out[-1] == ";":
            out.pop()
            emit("}")
            i += 1
            push_code("}")
            continue

        if ch in " \t\r\n":
            has_nl = False
            while i < n:
                if js[i] in " \t\r\n":
                    if js[i] == "\n":
                        has_nl = True
                    i += 1
                elif js[i : i + 2] == "//":
                    nl = js.find("\n", i + 2)
                    if nl < 0:
                        i = n
                    else:
                        has_nl = True
                        i = nl + 1
                elif js[i : i + 2] == "/*":
                    end = js.find("*/", i + 2)
                    seg_end = n if end < 0 else end + 2
                    if "\n" in js[i:seg_end]:
                        has_nl = True
                    i = seg_end
                else:
                    break
            if i >= n or not out:
                continue
            nxt = js[i]
            if has_nl and not (
                last_ch in _NO_ASI_PREV
                or nxt in "}),.;:?"
                or (last_ch in "+-" and prev2_ch != last_ch)
            ):
                emit("\n")
            elif (
                (_is_ident_char(last_ch) and _is_ident_char(nxt))
                or (last_ch == "+" and nxt == "+")
                or (last_ch == "-" and nxt == "-")
                or (last_ch == "/" and nxt in "/*")
            ):
                emit(" ")
            continue

        if _is_ident_char(ch):
            j = i + 1
            while j < n and _is_ident_char(js[j]):
                j += 1
            word = js[i:j]
            emit(word)
            i = j
            prev_tok = word[-1]
            prev_word = word
            prev2_ch = word[-2] if len(word) > 1 else last_ch
            last_ch = word[-1]
            continue

        emit(ch)
        i += 1
        push_code(ch)

    return "".join(out).strip()


def shorten_css_vars(css: str, js: str) -> tuple[str, str]:
    used = set(re.findall(r"--[a-zA-Z0-9-]+", css + "\n" + js))
    candidates = sorted(
        {v for v in re.findall(r"--[a-zA-Z0-9-]+", css) if len(v) >= 5},
        key=lambda v: (-len(v), v),
    )
    aliases = [
        f"--{c}"
        for c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if f"--{c}" not in used
    ]
    for old, new in zip(candidates, aliases):
        if len(new) >= len(old):
            continue
        pat = re.compile(r"(?<=['\"(\s;:{,])" + re.escape(old) + r"(?![a-zA-Z0-9-])")
        css = pat.sub(new, css)
        js = pat.sub(new, js)
    return css, js


def build() -> tuple[str, dict[str, int]]:
    tpl = re.sub(r">\s+<", "><", read_src("template.html").strip())
    raw_css = "\n".join(read_src(f) for f in CSS_FILES)
    raw_js = "\n".join(read_src(f) for f in JS_FILES)
    css = minify_css(raw_css)
    js = minify_js(raw_js).replace("</script>", "<\\/script>")
    css, js = shorten_css_vars(css, js)
    html = tpl.replace("/*CSS*/", css).replace(
        "/*JS*/", '(function(){"use strict";\n' + js + "\n})();"
    )
    html_bytes = len(html.encode("utf-8"))
    if html_bytes >= SIZE_LIMIT:
        raise RuntimeError(
            f"Built index.html is {html_bytes} B, exceeding budget {SIZE_LIMIT} B"
        )
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)
    return OUT_PATH, {
        "total_bytes": html_bytes,
        "css_bytes": len(css.encode("utf-8")),
        "js_bytes": len(js.encode("utf-8")),
        "tpl_bytes": len(tpl.encode("utf-8")),
        "raw_bytes": len((tpl + raw_css + raw_js).encode("utf-8")),
    }


def main() -> int:
    out_path, stats = build()
    headroom = SIZE_LIMIT - stats["total_bytes"]
    print(
        f"wrote {out_path}\n"
        f"  index.html: {stats['total_bytes']} B (limit {SIZE_LIMIT} B, headroom {headroom} B)\n"
        f"  css: {stats['css_bytes']} B · js: {stats['js_bytes']} B · tpl: {stats['tpl_bytes']} B "
        f"(raw src: {stats['raw_bytes']} B)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
