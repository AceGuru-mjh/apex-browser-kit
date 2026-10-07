#!/usr/bin/env python3
"""
Negative tests for check_webview_hardening.py.

Each case injects a REAL vulnerability (or a realistic non-violation) into a copy
of the repo and asserts the gate reacts correctly. Includes the false-positive
case that this gate actually shipped with: the hardening line carries a trailing
comment that *mentions* handler.proceed(), which a naive search flags.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent.parent
PY = sys.executable
ENGINE_REL = "browser-engine/src/main/kotlin/com/apex/browser/engine/BrowserEngine.kt"

results: list[tuple[bool, str]] = []


def run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(repo / "scripts/check_webview_hardening.py")],
                          capture_output=True, text=True, cwd=repo)


def case(name: str, mutate, expect_fail: bool, expect: str = "") -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(SRC, repo,
                        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"))
        mutate(repo / ENGINE_REL)
        p = run(repo)
        out = p.stdout + p.stderr
        failed = p.returncode != 0
        ok = (failed == expect_fail) and (expect.lower() in out.lower() if expect else True)
        results.append((ok, name))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"       exit={p.returncode} (want {'non-zero' if expect_fail else 'zero'}) "
                  f"expect={expect!r} present={expect.lower() in out.lower() if expect else 'n/a'}")
            for line in out.splitlines()[:10]:
                print("       " + line)


def sub(path: Path, old: str, new: str) -> None:
    s = path.read_text(encoding="utf-8")
    assert old in s, f"mutation anchor not found: {old!r}"
    path.write_text(s.replace(old, new, 1), encoding="utf-8")


# ── must FAIL: real vulnerabilities ──────────────────────────────────────────
case("catches handler.proceed() (invalid certs accepted → MITM)",
     lambda f: sub(f, "handler?.cancel()", "handler?.proceed()"),
     expect_fail=True, expect="FAIL SSL")

case("catches addJavascriptInterface (origin-less Java bridge)",
     lambda f: sub(f, "wv.settings.javaScriptEnabled = true",
                   "wv.settings.javaScriptEnabled = true\n        wv.addJavascriptInterface(obj, \"x\")"),
     expect_fail=True, expect="FAIL BRIDGE")

case("catches allowFileAccess flipped to true",
     lambda f: sub(f, "wv.settings.allowFileAccess = false", "wv.settings.allowFileAccess = true"),
     expect_fail=True, expect="FAIL allowFileAccess = false")

case("catches allowContentAccess flipped to true",
     lambda f: sub(f, "wv.settings.allowContentAccess = false", "wv.settings.allowContentAccess = true"),
     expect_fail=True, expect="FAIL allowContentAccess = false")

case("catches allowUniversalAccessFromFileURLs flipped to true",
     lambda f: sub(f, "wv.settings.allowUniversalAccessFromFileURLs = false",
                   "wv.settings.allowUniversalAccessFromFileURLs = true"),
     expect_fail=True, expect="FAIL allowUniversalAccessFromFileURLs")

case("catches allowFileAccessFromFileURLs line DELETED (falls back to platform default)",
     # This is the subtle one: on API 26..29 that default is `true`, so deleting
     # the line silently reopens file:// access.
     lambda f: sub(f, "        wv.settings.allowFileAccessFromFileURLs = false\n", ""),
     expect_fail=True, expect="FAIL allowFileAccessFromFileURLs")

case("catches mixed content downgraded to ALWAYS_ALLOW",
     lambda f: sub(f, "WebSettings.MIXED_CONTENT_NEVER_ALLOW",
                   "WebSettings.MIXED_CONTENT_ALWAYS_ALLOW"),
     expect_fail=True, expect="FAIL mixedContentMode")

case("catches remote debugging enabled",
     lambda f: sub(f, "WebView.setWebContentsDebuggingEnabled(false)",
                   "WebView.setWebContentsDebuggingEnabled(true)"),
     expect_fail=True, expect="FAIL debugging")

case("catches removal of the SSL cancel call",
     lambda f: sub(f, "handler?.cancel() // 严禁 handler.proceed()", "// nothing"),
     expect_fail=True, expect="FAIL SSL")

# ── must PASS: realistic non-violations ─────────────────────────────────────
case("real code passes on the unmodified repo", lambda f: None,
     expect_fail=False)

case("a trailing COMMENT mentioning handler.proceed() must NOT trip the SSL gate",
     # Regression guard for the gate's own first bug: the real hardening line is
     #   handler?.cancel() // 严禁 handler.proceed()
     # and a naive substring search flags the comment as a live call.
     lambda f: sub(f, "handler?.cancel() // 严禁 handler.proceed()",
                   "handler?.cancel() // 这里绝不能改成 handler.proceed()"),
     expect_fail=False)

case("a string literal containing // must not truncate the line",
     #   strip_line_comment must not cut at the '//' inside "https://..."
     lambda f: sub(f, 'it == "http" || it == "https"',
                   'it == "https://x.com" || it == "http"'),
     expect_fail=False)

case("a hardening line mentioned ONLY in KDoc does not count as present",
     lambda f: sub(f, "        wv.settings.allowContentAccess = false\n", ""),
     expect_fail=True, expect="FAIL allowContentAccess = false")

ok = sum(1 for r, _ in results if r)
print(f"\n{ok}/{len(results)} webview-hardening gate tests behaved correctly")
sys.exit(0 if ok == len(results) else 1)