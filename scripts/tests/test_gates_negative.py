#!/usr/bin/env python3
"""
Negative tests for the CI gates.

A gate that has only ever passed proves nothing. Each case below injects a REAL
violation into a scratch copy of the repo, runs the gate, and asserts it fails
with a useful message. If a gate silently stops working (regex rot, refactor,
path change) this catches it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent.parent  # repo root
PY = sys.executable

results: list[tuple[bool, str]] = []


def run_gate(repo: Path, gate: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(repo / "scripts" / gate)],
        capture_output=True, text=True, cwd=repo,
    )


def case(name: str, gate: str, mutate, expect_substring: str) -> None:
    """Copy the repo, apply `mutate`, assert the gate fails mentioning the text."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(
            SRC, repo,
            ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"),
        )
        mutate(repo)
        proc = run_gate(repo, gate)
        out = proc.stdout + proc.stderr
        failed = proc.returncode != 0
        mentioned = expect_substring.lower() in out.lower()
        ok = failed and mentioned
        results.append((ok, name))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}")
        if not ok:
            print(f"       exit={proc.returncode} (want non-zero)  "
                  f"mentions {expect_substring!r}={mentioned}")
            print("       --- gate output ---")
            for line in out.splitlines()[:14]:
                print("       " + line)


# ── GATE 1: raw interpolation into JS ────────────────────────────────────────
def mutate_raw_interp(repo: Path) -> None:
    # Inject a new factory with the exact v1.0.0 bug shape. Appending a whole
    # function is robust; string-replacing an existing line was not (the shape
    # it targeted was refactored away, so the case silently mutated nothing).
    f = repo / "browser-core/src/main/kotlin/com/apex/browser/core/BrowserScript.kt"
    s = f.read_text(encoding="utf-8")
    inject = (
        "\n    fun vulnerableJs(ref: String): String =\n"
        "        \"\"\"(function(){ var el = document.querySelector('[data-apex-hash=${ref}]'); return !!el; })();\"\"\"\n"
    )
    s = s.replace("\n    /** 物理触摸注入", inject + "\n    /** 物理触摸注入", 1)
    assert "vulnerableJs" in s, "mutation did not apply"
    f.write_text(s, encoding="utf-8")


case("GATE 1 catches bare ${ref} interpolation",
     "check_js_injection.py", mutate_raw_interp, "GATE 1")


# ── GATE 1 must NOT flag compile-time constants ──────────────────────────────
def mutate_const_interp(repo: Path) -> None:
    f = repo / "browser-core/src/main/kotlin/com/apex/browser/core/BrowserScript.kt"
    s = f.read_text(encoding="utf-8")
    s = s.replace("var MAX = $SNAPSHOT_MAX_ELEMENTS;",
                  "var MAX = $SNAPSHOT_MAX_ELEMENTS;\n          var __dbg = '$UNSAFE_CONSTANT';",
                  1)
    f.write_text(s, encoding="utf-8")


def expect_gate_passes(repo: Path, gate: str, name: str) -> None:
    proc = run_gate(repo, gate)
    ok = proc.returncode == 0
    results.append((ok, name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        print("       gate failed but should not have:")
        for line in (proc.stdout + proc.stderr).splitlines()[:12]:
            print("       " + line)


with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    shutil.copytree(
        SRC, repo,
        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"),
    )
    mutate_const_interp(repo)
    expect_gate_passes(repo, "check_js_injection.py",
                       "GATE 1 does not false-positive on SCREAMING_CASE constants")


# ── GATE 2: zero-escape helper returns ───────────────────────────────────────
def mutate_helper(repo: Path) -> None:
    f = repo / "browser-core/src/main/kotlin/com/apex/browser/core/BrowserScript.kt"
    s = f.read_text(encoding="utf-8")
    s += '\nprivate fun String.toJsonString(): String = "\'$this\'"\n'
    f.write_text(s, encoding="utf-8")


case("GATE 2 catches reintroduced toJsonString helper",
     "check_js_injection.py", mutate_helper, "GATE 2")


# ── GATE 3: Promise in injected script ───────────────────────────────────────
def mutate_promise(repo: Path) -> None:
    f = repo / "browser-core/src/main/kotlin/com/apex/browser/core/BrowserScript.kt"
    s = f.read_text(encoding="utf-8")
    s = s.replace(
        "    fun selectorPresentJs(selector: String): String =",
        "    fun waitProbe(selector: String): String =\n"
        "        \"\"\"(function(){ return new Promise(function(r){ r(true); }); })();\"\"\"\n\n"
        "    fun selectorPresentJs(selector: String): String =",
        1,
    )
    f.write_text(s, encoding="utf-8")


case("GATE 3 catches Promise-returning injected script",
     "check_js_injection.py", mutate_promise, "GATE 3")


# ── RULE 1: android import in core ───────────────────────────────────────────
def mutate_android_import(repo: Path) -> None:
    f = repo / "browser-core/src/main/kotlin/com/apex/browser/core/JsLiteral.kt"
    s = f.read_text(encoding="utf-8")
    s = s.replace("package com.apex.browser.core",
                  "package com.apex.browser.core\n\nimport android.util.Log", 1)
    assert "import android.util.Log" in s, "mutation did not apply"
    f.write_text(s, encoding="utf-8")


case("RULE 1 catches android.* import in :browser-core",
     "check_core_purity.py", mutate_android_import, "RULE 1")


def mutate_android_import_in_test(repo: Path) -> None:
    f = repo / "browser-core/src/test/kotlin/com/apex/browser/core/JsLiteralTest.kt"
    s = f.read_text(encoding="utf-8")
    s = s.replace("import org.junit.Test",
                  "import androidx.test.core.app.ApplicationProvider\nimport org.junit.Test", 1)
    assert "androidx" in s, "mutation did not apply"
    f.write_text(s, encoding="utf-8")


case("RULE 1 catches android.* import in a core TEST (plain-JVM claim breaks)",
     "check_core_purity.py", mutate_android_import_in_test, "RULE 1")


# ── RULE 2: core reaching up into engine ─────────────────────────────────────
def mutate_upward_import(repo: Path) -> None:
    f = repo / "browser-core/src/main/kotlin/com/apex/browser/core/PageClassifier.kt"
    s = f.read_text(encoding="utf-8")
    s = s.replace("package com.apex.browser.core",
                  "package com.apex.browser.core\n\nimport com.apex.browser.engine.BrowserEngine", 1)
    assert "com.apex.browser.engine" in s, "mutation did not apply"
    f.write_text(s, encoding="utf-8")


case("RULE 2 catches core -> engine dependency (cycle)",
     "check_core_purity.py", mutate_upward_import, "RULE 2")


# ── Gates must not flag prose that documents the anti-pattern ────────────────
def mutate_doc_only(repo: Path) -> None:
    f = repo / "browser-core/src/main/kotlin/com/apex/browser/core/BrowserScript.kt"
    s = f.read_text(encoding="utf-8")
    s = s.replace("package com.apex.browser.core",
                  "package com.apex.browser.core\n\n"
                  "/*\n * Historic note: the old code did\n"
                  " *   var el = document.querySelector('[data-apex-hash=${ref}]');\n"
                  " * which was vulnerable. Never do that; see JsLiteral.\n */", 1)
    f.write_text(s, encoding="utf-8")


with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    shutil.copytree(
        SRC, repo,
        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"),
    )
    mutate_doc_only(repo)
    expect_gate_passes(repo, "check_js_injection.py",
                       "GATE 1 ignores KDoc that documents the anti-pattern")


# ── Summary ──────────────────────────────────────────────────────────────────
ok = sum(1 for r, _ in results if r)
total = len(results)
print(f"\n{ok}/{total} gate negative-tests behaved correctly")
if ok != total:
    print("FAILURES:")
    for r, n in results:
        if not r:
            print(f"  - {n}")
    sys.exit(1)
print("ALL GATE NEGATIVE-TESTS PASS")