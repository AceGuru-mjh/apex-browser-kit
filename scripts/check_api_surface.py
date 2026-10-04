#!/usr/bin/env python3
"""
check_api_surface.py - public-API stability gate for a published library.

A library's whole value is that consumers can trust its coordinates: every
removal or silent signature change in a `com.apex.browser.*` public
declaration is a downstream build break. The compiler will not catch it here
(the breakage shows up in the *host* repo), unit tests only cover what someone
thought to assert, and the README's "additive API" promise had no enforcement.

How it works: a small lexer strips comments/strings, then a brace-tracking
pass records every default/public declaration as
`<kind> <qualified.name><signature>` into `api/<module>.api`. The gate diffs
the working tree against those committed baselines:

  REMOVED / CHANGED  -> FAIL (breaking; needs a deliberate baseline update)
  ADDED              -> informational (additive is the promise; always allowed)

Intentional API change workflow: change the code, run
`python scripts/check_api_surface.py --update-baseline`, review the
`git diff api/`, commit both together. A baseline-only diff with no source
change is suspicious and should be questioned in review.

Scope notes (deliberate, documented):
  - `internal` / `private` / `protected` declarations are skipped, and anything
    nested inside them is skipped too (unreachable to consumers either way).
  - `protected` members are out of scope: this library is consumed, not
    subclassed.
  - Formatting-only edits do not move the baseline (whitespace is collapsed,
    bodies after `=`/`{` are dropped) and member order does not matter
    (comparison is set-based).
  - This is a source-shape check, not bytecode ABI (no Kotlin 2.2 / classfiles
    workaround needed, unlike binary-compatibility-validator). JVM-signature
    subtleties (e.g. `@JvmOverloads` synthetics) are out of scope.

Negative tests: scripts/tests/test_api_gate_negative.py.

Usage:
  python scripts/check_api_surface.py                  # check (CI)
  python scripts/check_api_surface.py --dump            # print current surface
  python scripts/check_api_surface.py --update-baseline # regenerate api/*.api

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "api"
MODULES = ("browser-core", "browser-engine", "browser-chrome")

DECL_RE = re.compile(
    r"(?<![\w.:])(?:(public|internal|protected|private)\s+)?"
    r"(?:(annotation|data|sealed|abstract|open|inner|value|expect|actual|enum|const)\s+)*"
    r"(class|object|interface|enum|fun|val|var|typealias)\b"
)
IDENT_RE = re.compile(r"[A-Za-z_][\w]*")
SKIPPED_VIS = ("internal", "private", "protected")
TYPE_KINDS = ("class", "object", "interface", "enum")
MEMBER_KINDS = ("fun", "val", "var")


def strip_kotlin(src: str) -> str:
    """Replace comments and string contents with spaces (length-preserving)."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c == '"':
            if src[i:i + 3] == '"""':
                j = src.find('"""', i + 3)
                j = n if j == -1 else j + 3
                for k in range(i, j):
                    if out[k] != "\n":
                        out[k] = " "
                i = j
            else:
                j = i + 1
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == '"':
                        j += 1
                        break
                    if src[j] == "\n":
                        break
                    j += 1
                for k in range(i, j):
                    out[k] = " "
                i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "'":
                    j += 1
                    break
                if src[j] == "\n":
                    break
                j += 1
            for k in range(i, j):
                out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def skip_ws(code: str, i: int) -> int:
    while i < len(code) and code[i] in " \t\r\n":
        i += 1
    return i


def skip_generic_params(code: str, i: int) -> tuple[int, str]:
    """Skip one balanced `<...>` group if present.

    Returns (new_pos, normalized_text) - text is "" when no group is present.
    The text is kept because generic arity/bounds are API: `fun <T> f` vs
    `fun <T, R> f` must not compare equal.
    """
    i = skip_ws(code, i)
    if i >= len(code) or code[i] != "<":
        return i, ""
    start = i
    depth = 0
    while i < len(code):
        if code[i] == "<":
            depth += 1
        elif code[i] == ">":
            depth -= 1
            if depth == 0:
                end = skip_ws(code, i + 1)
                return end, re.sub(r"\s+", " ", code[start:i + 1]).strip()
        i += 1
    return start, ""


def make_entry(kind: str, qname: str, generics: str, sig: str) -> str:
    head = f"{kind} {qname}{generics}"
    if not sig:
        return head
    return head + (sig if sig.startswith("(") else " " + sig)


def capture_line(code: str, i: int, limit: int = 200) -> str:
    """Rest of the current line, whitespace-collapsed (for typealias RHS)."""
    j = code.find("\n", i)
    j = len(code) if j == -1 else j
    return re.sub(r"\s+", " ", code[i:j]).strip()[:limit]


def capture_signature(code: str, i: int, paren: int) -> str:
    """Capture `(params): ReturnType` / `: Type`.

    Stops at `=`/`{`/`;`, at `,`/`)` that close past the member's own paren
    level (so one ctor param does not swallow its siblings), and at a newline
    once the header already holds `)` or `:` (so a bodyless `val x: Int` does
    not swallow the next declaration).
    """
    i = skip_ws(code, i)
    start = i
    n = len(code)
    depth_paren = 0
    depth_angle = 0
    seen_close_or_colon = False
    while i < n:
        c = code[i]
        if c == "(":
            depth_paren += 1
        elif c == ")":
            if depth_paren == 0 and paren > 0:
                break  # closes a paren opened before this member started
            depth_paren = max(0, depth_paren - 1)
            seen_close_or_colon = True
        elif c == "<" and depth_paren == 0:
            depth_angle += 1
        elif c == ">" and depth_paren == 0 and depth_angle > 0:
            depth_angle -= 1
        elif c == ":" and depth_paren == 0 and depth_angle == 0:
            seen_close_or_colon = True
        elif c in "{;":
            break
        elif c == "," and depth_paren == 0 and paren > 0:
            break  # next sibling param / next enum-independent declarator
        elif c == "=" and depth_paren == 0 and depth_angle == 0:
            break
        elif c == "\n" and depth_paren == 0 and depth_angle == 0 \
                and seen_close_or_colon:
            break
        i += 1
    sig = re.sub(r"\s+", " ", code[start:i]).strip()
    for a, b in (("( ", "("), (" )", ")"), (" ,", ","), ("( )", "()")):
        sig = sig.replace(a, b)
    return sig


def extract(path: Path) -> list[str]:
    code = strip_kotlin(path.read_text(encoding="utf-8"))
    entries: list[str] = []
    # Open type scopes as (name, brace_depth, poisoned). `poisoned` means the
    # scope itself is non-public, so everything inside is unreachable too.
    # Names starting with "!" are synthetic (anonymous object / companion) and
    # never part of a qualified name.
    stack: list[tuple[str, int, bool]] = []
    # A type declaration seen but not yet opened with `{`: (name, poisoned,
    # paren_depth_at_decl). Its ctor params belong to it; a bodyless type
    # never enters the stack.
    pending: tuple[str, bool, int] | None = None
    # A `fun` declaration awaiting its body `{`. Separated from func_depths:
    # only a body that actually opens suppresses members, so bodyless
    # (abstract/interface) funs never hide their siblings. Any `{` consumes
    # it - fun-body and other-body both mean "members inside are locals".
    # A TYPE declaration always clears it (a type cannot appear inside a fun
    # header, so the fun was bodyless).
    pending_fun = False
    # Body tags, one per open `{`: "type" (members are API), "fun"/"other"
    # (members are locals). A member records only under "type" or top level.
    bodies: list[str] = []
    decls = {m.start(): m for m in DECL_RE.finditer(code)}
    depth = 0
    paren = 0
    i, n = 0, len(code)

    def scopes() -> list[str]:
        return [s for s, _, p in stack if not p and not s.startswith("!")]

    def poisoned() -> bool:
        return any(p for s, _, p in stack if not s.startswith("!"))

    def recordable() -> bool:
        return (not bodies or bodies[-1] == "type") and not poisoned()

    while i < n:
        m = decls.get(i)
        if m is None:
            c = code[i]
            if c == "{":
                depth += 1
                if pending is not None:
                    name, poison, _ = pending
                    if name != "!anon":
                        stack.append((name, depth, poison))
                    bodies.append("type" if name != "!anon" else "other")
                    pending = None
                elif pending_fun:
                    bodies.append("fun")
                    pending_fun = False
                else:
                    bodies.append("other")
            elif c == "}":
                while stack and stack[-1][1] >= depth:
                    stack.pop()
                if bodies:
                    bodies.pop()
                depth = max(0, depth - 1)
            elif c == "(":
                paren += 1
            elif c == ")":
                paren = max(0, paren - 1)
            i += 1
            continue

        vis, kind = m.group(1), m.group(3)
        skipped = vis in SKIPPED_VIS
        j, generics = skip_generic_params(code, skip_ws(code, m.end()))
        nm = IDENT_RE.match(code, j)
        name = nm.group(0) if nm else None
        # `enum class X`: `enum` was consumed as a modifier; the real kind is
        # the `class` that follows.
        if name in ("class", "interface", "object", "annotation"):
            kind = name
            generics = ""
            j, _ = skip_generic_params(code, skip_ws(code, nm.end()))
            nm = IDENT_RE.match(code, j)
            name = nm.group(0) if nm else None
        after = nm.end() if nm else j

        if kind == "typealias":
            if name and not skipped and recordable():
                rhs = capture_line(code, after)
                entries.append(make_entry(
                    "typealias", ".".join(scopes() + [name]), "", rhs))
            pending = None
            i = after
            continue
        if kind in TYPE_KINDS:
            pending_fun = False
            if not name:
                pending = ("!anon", True, paren)  # anonymous: swallow `{`
            elif name in ("companion", "Companion") and kind == "object":
                # Companion members are called as Outer.member.
                pending = ("!companion", False, paren)
            else:
                qname = ".".join(scopes() + [name])
                local_type = bool(bodies) and bodies[-1] != "type"
                if not skipped and recordable() and not local_type:
                    sig = capture_signature(code, after, paren)
                    entries.append(make_entry(kind, qname, generics, sig))
                pending = (name, skipped or poisoned() or local_type, paren)
            i = after
            continue
        if kind in MEMBER_KINDS:
            if kind == "fun":
                # A fun header cannot contain a type's `{`: any pending type
                # was bodyless - except the ctor-param list we are inside of,
                # which contains no fun declarations.
                if paren == 0:
                    pending = None
                pending_fun = True
            if name:
                # A member while a type is pending and parens are still open
                # is one of its ctor params: it belongs to the pending type
                # and must NOT consume it. Anything else discards stale
                # pending (a bodyless type never opens a scope).
                scope = scopes()
                ctor_param = (pending is not None
                              and pending[0] not in ("!anon", "!companion")
                              and paren > pending[2])
                if ctor_param:
                    assert pending is not None
                    if pending[1]:
                        name = None  # poisoned type: drop the param
                    else:
                        scope = scope + [pending[0]]
                elif kind != "fun" or paren == 0:
                    # val/var always settle a pending type; a fun does too
                    # unless it sits inside the pending parens (impossible in
                    # valid Kotlin, but harmless to keep symmetric).
                    pending = None
                if name and not skipped and recordable():
                    sig = capture_signature(code, after, paren)
                    entries.append(make_entry(
                        kind, ".".join(scope + [name]), generics, sig))
            else:
                pending = None
            i = after
            continue
        i = m.end()

    return sorted(set(entries))


def dump_module(mod: str) -> list[str]:
    srcdir = ROOT / mod / "src" / "main" / "kotlin"
    entries: list[str] = []
    if not srcdir.is_dir():
        return entries
    for kt in sorted(srcdir.rglob("*.kt")):
        if "/build/" in kt.as_posix():
            continue
        for e in extract(kt):
            entries.append(f"{mod} :: {e}")
    return sorted(set(entries))


def load_baseline(mod: str) -> list[str] | None:
    f = API_DIR / f"{mod}.api"
    if not f.is_file():
        return None
    return [ln.rstrip("\n") for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def key_of(entry: str) -> str:
    """`<module> :: <kind> <qualified.name>` - identity ignoring signature."""
    head = entry.split(" :: ", 1)[1] if " :: " in entry else entry
    parts = head.split(" ", 1)
    if len(parts) != 2:
        return entry
    kind, rest = parts
    name = rest.split("(", 1)[0].split(":", 1)[0].split(" ", 1)[0].strip()
    return f"{kind} {name}"


def check() -> int:
    if not API_DIR.is_dir():
        print(f"FAIL: expected {API_DIR.relative_to(ROOT)}/ with committed "
              "baselines (gate cannot run)")
        return 1
    failed = False
    for mod in MODULES:
        base = load_baseline(mod)
        if base is None:
            print(f"FAIL {mod}: no committed baseline api/{mod}.api - run "
                  "`python scripts/check_api_surface.py --update-baseline` "
                  "and commit the result")
            failed = True
            continue
        current = dump_module(mod)
        base_map: dict[str, str] = {}
        for e in base:
            base_map.setdefault(key_of(e), e)
        cur_map: dict[str, str] = {}
        for e in current:
            cur_map.setdefault(key_of(e), e)
        removed = sorted(k for k in base_map if k not in cur_map)
        changed = sorted(k for k in base_map if k in cur_map
                         and base_map[k] != cur_map[k])
        added = sorted(k for k in cur_map if k not in base_map)
        if removed or changed:
            print(f"FAIL {mod}: public API break ({len(removed)} removed, "
                  f"{len(changed)} changed):")
            for k in removed:
                print(f"  - REMOVED  {base_map[k]}")
            for k in changed:
                print(f"  - CHANGED  {base_map[k]}")
                print(f"      -> now {cur_map[k]}")
            print("   Fix: restore the declaration, or (deliberate break) "
                  "run --update-baseline, review `git diff api/`, and "
                  "commit both together.")
            failed = True
        else:
            print(f"PASS {mod}: no removals / signature changes "
                  f"({len(current)} public entries)")
        if added:
            print(f"     ADDED ({len(added)}, allowed - additive is the "
                  "promise):")
            for k in added[:10]:
                print(f"       + {cur_map[k]}")
            if len(added) > 10:
                print(f"       + ... and {len(added) - 10} more")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if "--dump" in argv:
        for mod in MODULES:
            for e in dump_module(mod):
                print(e)
        return 0
    if "--update-baseline" in argv:
        API_DIR.mkdir(exist_ok=True)
        for mod in MODULES:
            entries = dump_module(mod)
            header = (f"# Public API surface of :{mod} - COMMITTED BASELINE.\n"
                      "# Regenerate only with "
                      "`python scripts/check_api_surface.py "
                      "--update-baseline` and review the diff.\n")
            (API_DIR / f"{mod}.api").write_text(
                header + "".join(e + "\n" for e in entries),
                encoding="utf-8")
            print(f"wrote api/{mod}.api ({len(entries)} entries)")
        return 0
    return check()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
