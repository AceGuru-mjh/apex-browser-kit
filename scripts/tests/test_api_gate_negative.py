#!/usr/bin/env python3
"""
Negative tests for scripts/check_api_surface.py.

Each case mutates a scratch copy and asserts the gate fails mentioning the
break - plus a positive control that additive-only changes stay green.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent.parent  # repo root
PY = sys.executable
CORE = ("browser-core/src/main/kotlin/com/apex/browser/core/"
        "BrowserScript.kt")

results: list[tuple[bool, str]] = []


def run_gate(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(repo / "scripts" / "check_api_surface.py"), *args],
        capture_output=True, text=True, cwd=repo,
    )


def case(name: str, mutate, expect_substring: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(
            SRC, repo,
            ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"),
        )
        mutate(repo)
        proc = run_gate(repo)
        out = proc.stdout + proc.stderr
        ok = (proc.returncode != 0) and (expect_substring.lower() in out.lower())
        results.append((ok, name))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"       exit={proc.returncode} (want non-zero)  "
                  f"mentions {expect_substring!r}")
            print("       --- gate output ---")
            for line in out.splitlines()[:12]:
                print("       " + line)


def mutate_remove_fun(repo: Path) -> None:
    # Narrowing visibility to private removes the factory from the public
    # surface exactly the way deleting it would, without disturbing the
    # surrounding raw-string bodies (a line-range delete risks orphaning a
    # `"""` and creating phantom `var` hits out of JS payload text).
    f = repo / CORE
    s = f.read_text(encoding="utf-8")
    new = s.replace("    fun selectorPresentJs(selector: String): String =",
                    "    private fun selectorPresentJs(selector: String)"
                    ": String =", 1)
    assert new != s, "mutation did not apply"
    f.write_text(new, encoding="utf-8")


def mutate_change_signature(repo: Path) -> None:
    f = repo / CORE
    s = f.read_text(encoding="utf-8")
    new = s.replace("fun scrollByJs(deltaY: Int): String",
                    "fun scrollByJs(deltaY: Long): String", 1)
    assert new != s, "mutation did not apply"
    f.write_text(new, encoding="utf-8")


def mutate_remove_param(repo: Path) -> None:
    f = (repo / "browser-core/src/main/kotlin/com/apex/browser/core/"
         "DomElement.kt")
    s = f.read_text(encoding="utf-8")
    new = s.replace("val childCount: Int = 0", "val offspring: Int = 0", 1)
    assert new != s, "mutation did not apply"
    f.write_text(new, encoding="utf-8")


case("catches a removed public factory", mutate_remove_fun, "REMOVED")
case("catches a changed parameter type", mutate_change_signature, "CHANGED")
case("catches a renamed data-class property", mutate_remove_param, "REMOVED")


# ── Positive control: pure addition stays green ───────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    shutil.copytree(
        SRC, repo,
        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"),
    )
    f = repo / CORE
    s = f.read_text(encoding="utf-8")
    anchor = "    fun selectorPresentJs(selector: String): String ="
    assert anchor in s, "anchor for additive mutation not found"
    s = s.replace(anchor,
                  "    fun brandNewProbeJs(selector: String): String =\n"
                  "        selectorPresentJs(selector)\n\n" + anchor, 1)
    f.write_text(s, encoding="utf-8")
    proc = run_gate(repo)
    out = proc.stdout + proc.stderr
    ok = proc.returncode == 0 and "ADDED" in out
    results.append((ok, "purely additive API stays green (reported, not failed)"))
    print(f"[{'PASS' if ok else 'FAIL'}] purely additive API stays green "
          "(reported, not failed)")
    if not ok:
        print(f"       exit={proc.returncode} (want 0)")
        for line in out.splitlines()[:12]:
            print("       " + line)

# ── Summary ───────────────────────────────────────────────────────────────────
ok = sum(1 for r, _ in results if r)
total = len(results)
print(f"\n{ok}/{total} api-gate negative-tests behaved correctly")
if ok != total:
    print("FAILURES:")
    for r, n in results:
        if not r:
            print(f"  - {n}")
    sys.exit(1)
print("ALL API-GATE NEGATIVE-TESTS PASS")
