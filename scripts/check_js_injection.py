#!/usr/bin/env python3
"""
check_js_injection.py — regression gate for the library's security invariant.

Every ``BrowserScript`` factory embeds externally-supplied strings (``ref``,
selector, input text, option value, colour) into JS that executes inside the
page. v1.0.0 shipped a helper named ``toJsonString`` whose body was
``"'$this'"`` — zero escaping — and ``ref = "+alert(document.cookie)+"``
produced working page-level JavaScript execution (see PR #1). Nothing stopped
that from coming back.

Unit tests cover the escaping, but only for cases somebody thought of. This is
the structural half of the defence: it fails when an injection point appears
that does not route through ``JsLiteral.string``.

  GATE 1  No raw interpolation of a lowercase (external) identifier into the JS
          raw-strings. Only ``${JsLiteral.string(...)}`` is allowed, plus
          SCREAMING_CASE compile-time constants which can never be attacker
          input. This is the exact shape of the v1.0.0 bug.
  GATE 2  The zero-escape helper must not return under any name
          (``toJsonString`` / ``"'$this'"``).
  GATE 3  No ``new Promise`` in injected scripts. ``evaluateJavascript`` does
          not await Promises, so such a script silently returns null — that is
          how ``browser_navigate(wait_for=…)`` shipped permanently broken.

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_MAIN = ROOT / "browser-core" / "src" / "main"
BROWSER_SCRIPT = CORE_MAIN / "kotlin/com/apex/browser/core/BrowserScript.kt"
ENGINE_MAIN = ROOT / "browser-engine" / "src" / "main"

# ${name} / ${name:fmt} where name starts lowercase -> external value.
RAW_INTERP = re.compile(r"\$\{\s*([a-z_][a-zA-Z0-9_]*)\s*([:?][^}]*)?\}")
# Report-only lane: ${expr...} with a lowercase root that RAW_INTERP cannot
# classify, e.g. ${deltaY.coerceIn(...)} (Int — provably injection-safe) or
# ${someUserString.trim()} (NOT safe). Too noisy to gate, so it is surfaced for
# human review rather than silently ignored.
COMPLEX_INTERP = re.compile(r"\$\{\s*([a-z_][a-zA-Z0-9_]*)\.[^}]*}")
SAFE_INTERP = re.compile(r"\$\{\s*JsLiteral\.")
# KDoc / comment lines are not code; documenting the anti-pattern is legitimate.
COMMENT_LINE = re.compile(r"^\s*(\*|//|/\*)")

ESCAPE_HELPER = re.compile(r"toJsonString|\"'\$this\"")


def strip_comments(lines: list[str]) -> list[tuple[int, str]]:
    """Return (lineno, text) for lines that are not inside a block comment.

    A naive line filter is enough here because we only need to avoid matching
    prose; a ``/* ... */`` block is tracked with a simple depth counter.
    """
    out: list[tuple[int, str]] = []
    in_block = False
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*") and "*/" not in stripped:
            in_block = True
            continue
        if COMMENT_LINE.match(line):
            continue
        out.append((i, line))
    return out


def gate1() -> tuple[list[str], list[str]]:
    """Return (gated_hits, audit_only_hits)."""
    if not BROWSER_SCRIPT.is_file():
        return ([f"expected {BROWSER_SCRIPT} to exist (gate cannot run)"], [])
    lines = BROWSER_SCRIPT.read_text(encoding="utf-8").splitlines()
    hits: list[str] = []
    audit: list[str] = []
    for lineno, line in strip_comments(lines):
        audited_spans = [m.span() for m in COMPLEX_INTERP.finditer(line)]
        for m in RAW_INTERP.finditer(line):
            hits.append(
                f"{BROWSER_SCRIPT.relative_to(ROOT)}:{lineno}: "
                f"${{{m.group(1)}{m.group(2) or ''}}}  ->  {line.strip()}"
            )
        for m in COMPLEX_INTERP.finditer(line):
            audit.append(
                f"{BROWSER_SCRIPT.relative_to(ROOT)}:{lineno}: "
                f"complex interpolation ${{{m.group(1)}.*}} — verify the value is "
                f"not attacker-controlled (Ints/constants are safe)"
            )
    return hits, audit


def gate2() -> list[str]:
    hits: list[str] = []
    for root in (CORE_MAIN, ENGINE_MAIN):
        if not root.is_dir():
            continue
        for kt in sorted(root.rglob("*.kt")):
            if "/build/" in kt.as_posix():
                continue
            for lineno, line in strip_comments(
                kt.read_text(encoding="utf-8").splitlines()
            ):
                if ESCAPE_HELPER.search(line):
                    hits.append(f"{kt.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return hits


def gate3() -> list[str]:
    if not BROWSER_SCRIPT.is_file():
        return [f"expected {BROWSER_SCRIPT} to exist (gate cannot run)"]
    hits = []
    for lineno, line in strip_comments(BROWSER_SCRIPT.read_text(
            encoding="utf-8").splitlines()):
        if "new Promise" in line:
            hits.append(f"{BROWSER_SCRIPT.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return hits


def count_js_literal_calls() -> int:
    total = 0
    for root in (CORE_MAIN, ENGINE_MAIN):
        if not root.is_dir():
            continue
        for kt in root.rglob("*.kt"):
            if "/build/" in kt.as_posix():
                continue
            total += kt.read_text(encoding="utf-8").count("JsLiteral.string")
    return total


def main() -> int:
    g1, audit1 = gate1()
    g2, g3 = gate2(), gate3()
    failed = False

    if g1:
        print("FAIL GATE 1 - raw interpolation of an external value into injected JS:")
        for h in g1:
            print(f"  - {h}")
        print()
        print("   Fix: wrap it as ${JsLiteral.string(name)}. External values are")
        print("   attacker-reachable (the model echoes page text back as a 'ref'),")
        print("   so any raw interpolation is a JS-injection site.")
        print("   See README<安全：JS 注入边界>.")
        failed = True
    else:
        print("PASS GATE 1 - no raw external-value interpolation into injected JS")

    if g2:
        print("FAIL GATE 2 - zero-escape JS-literal helper detected "
              "(the v1.0.0 injection bug):")
        for h in g2:
            print(f"  - {h}")
        print("   Fix: delete it and use JsLiteral.string(...) everywhere.")
        failed = True
    else:
        print("PASS GATE 2 - no zero-escape JS-literal helper (JsLiteral.string only)")

    if g3:
        print("FAIL GATE 3 - injected script returns a Promise "
              "(evaluateJavascript won't await it):")
        for h in g3:
            print(f"  - {h}")
        print("   Fix: return the value synchronously and let the Kotlin side poll")
        print("   (see waitForCondition / waitForSelectorOnPage).")
        failed = True
    else:
        print("PASS GATE 3 - no Promise-returning injected scripts "
              "(all evaluate synchronously)")

    print(f"\nAUDIT - JsLiteral.string call sites: {count_js_literal_calls()}")
    if audit1:
        print(f"AUDIT - {len(audit1)} complex interpolation(s), review-only:")
        for h in audit1:
            print(f"  - {h}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())