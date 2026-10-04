#!/usr/bin/env python3
"""
check_webview_hardening.py — lock in the WebView sandbox configuration.

Why a hand-written gate instead of leaving this to CodeQL:

GitHub's WebView queries (`java/android/websettings-file-access`,
`webview-addjavascriptinterface`, `websettings-javascript-enabled`,
`improper-webview-certificate-validation`, ...) all live in CodeQL's
``security-extended`` suite, NOT the default suite. Enabling that suite is a
**repository setting**, not something a PR can do, and Kotlin analysis needs a
full build anyway. So until someone flips that switch, nothing is checking the
single most security-relevant file in this library.

This gate is the cheap stand-in: stdlib Python, no Gradle, no build, runs in
seconds. It encodes the invariants from Google's WebView security guidance and
SECURITY.md's "what counts as a vulnerability" list.

Each check is an *invariant*, not a style preference: a future PR that flips
`allowFileAccess` to true, or "helpfully" calls `handler.proceed()` to make an
SSL-warning page load, breaks the build with an explanation instead of shipping.

Severity order in the output is deliberate: SSL and native-bridge checks first,
since those are the ones with public CVEs behind them.

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_MAIN = ROOT / "browser-engine" / "src" / "main"
ENGINE_TEST = ROOT / "browser-engine" / "src" / "test"

# (key, must-appear regex, human explanation)
# Every "secure setting" below is expressed as "this exact assignment must exist",
# so that simply deleting the line (and silently falling back to the platform
# default) also fails the build. That is deliberate: an absent hardening line is
# a regression, not a neutral cleanup.
REQUIRED_SETTINGS = [
    (
        "allowFileAccess = false",
        r"allowFileAccess\s*=\s*false",
        "file:// 访问必须关闭 —— 否则网页可探测本地文件系统",
    ),
    (
        "allowContentAccess = false",
        r"allowContentAccess\s*=\s*false",
        "content:// 访问必须关闭 —— 否则可读取其他应用提供的内容",
    ),
    (
        "allowFileAccessFromFileURLs = false",
        r"allowFileAccessFromFileURLs\s*=\s*false",
        "minSdk 26 起该开关在 API 30 以下默认为 true，必须显式关闭；"
        "它是 API 26~29 真起作用的控制",
    ),
    (
        "allowUniversalAccessFromFileURLs = false",
        r"allowUniversalAccessFromFileURLs\s*=\s*false",
        "禁止 file:// 页面跨域读取任意 http(s) 资源",
    ),
    (
        "mixedContentMode = MIXED_CONTENT_NEVER_ALLOW",
        r"mixedContentMode\s*=\s*[\w.]*MIXED_CONTENT_NEVER_ALLOW",
        "禁止 https 页加载 http 混合内容，防中间人注入",
    ),
    (
        "setWebContentsDebuggingEnabled(false)",
        r"setWebContentsDebuggingEnabled\s*\(\s*false\s*\)",
        "生产环境关闭 WebView 远程调试桥（adb 可注入 / 探测本地端口）",
    ),
]

GATE_HELPERS: list[tuple[str, re.Pattern[str], str]] = []


def kt_sources() -> list[Path]:
    if not ENGINE_MAIN.is_dir():
        print(f"FAIL: expected {ENGINE_MAIN} (gate cannot run)")
        sys.exit(1)
    return sorted(
        p for p in ENGINE_MAIN.rglob("*.kt") if "build" not in p.parts
    )


def strip_line_comment(line: str) -> str:
    """Remove a trailing ``//`` comment, respecting string and char literals.

    Necessary because the real hardening line is::

        handler?.cancel() // 严禁 handler.proceed()

    A naive substring search for ``handler.proceed()`` therefore "finds" a
    call that only exists inside a comment — a false positive that would make
    this gate unrunnable. Naive ``line.split("//")[0]`` is not enough either,
    because URLs such as ``"https://..."`` contain ``//`` inside a string.
    """
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c == '"':
            # raw string?
            if line.startswith('"""', i):
                j = line.find('"""', i + 3)
                i = n if j == -1 else j + 3
                continue
            i += 1
            while i < n and line[i] != '"':
                i += 2 if line[i] == "\\" else 1
            i += 1
            continue
        if c == "'":
            i += 1
            while i < n and line[i] != "'":
                i += 2 if line[i] == "\\" else 1
            i += 1
            continue
        if c == "/" and i + 1 < n and line[i + 1] == "/":
            return line[:i]
        i += 1
    return line


def code_lines(path: Path) -> list[tuple[int, str]]:
    """Drop comment-only lines AND trailing comments, so neither documented
    examples nor explanatory prose can satisfy or trip a gate."""
    out = []
    in_block = False
    for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = ln.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*") and "*/" not in stripped:
            in_block = True
            continue
        if stripped.startswith(("*", "//")):
            continue
        cleaned = strip_line_comment(ln)
        if cleaned.strip():
            out.append((i, cleaned))
    return out


def main() -> int:
    files = kt_sources()
    if not files:
        print("FAIL: no engine sources discovered; gate would vacuously pass.")
        return 1

    corpus = [
        (p, lineno, ln)
        for p in files
        for lineno, ln in code_lines(p)
    ]
    blob = "\n".join(ln for _, _, ln in corpus)
    failed = False

    def where(pattern: str) -> str:
        rx = re.compile(pattern)
        for p, lineno, ln in corpus:
            if rx.search(ln):
                return f"{p.relative_to(ROOT).as_posix()}:{lineno}"
        return "(not found)"

    print("── native bridge / certificate handling (highest severity) ──")

    # 1. SSL errors must be cancelled, never proceeded.
    proceed = re.search(r"handler\s*\??\.\s*proceed\s*\(", blob)
    cancel = re.search(r"handler\s*\??\.\s*cancel\s*\(", blob)
    if proceed:
        print(f"FAIL SSL — onReceivedSslError must NEVER call handler.proceed() "
              f"(accepts invalid certificates → trivial MITM): {where(r'handler\??\.\s*proceed')}")
        failed = True
    elif not cancel:
        print("FAIL SSL — onReceivedSslError does not cancel the load "
              "(no handler?.cancel() found); a missing cancel means the default "
              "error path is unverified.")
        failed = True
    else:
        print(f"PASS SSL — onReceivedSslError cancels the load at "
              f"{where(r'handler\??\.\s*cancel')}, never proceeds")

    # 2. addJavascriptInterface is a reflection-RCE sink below API 17 and an
    #    origin-less bridge above it. This library must not use it at all: its
    #    only channel into the page is evaluateJavascript.
    if re.search(r"addJavascriptInterface\s*\(", blob):
        print("FAIL BRIDGE — addJavascriptInterface detected. This library needs "
              "no native bridge (evaluateJavascript is sufficient), and the API "
              "exposes Java objects to ALL frames with no origin check "
              "(CVE-2012-6636 / CVE-2013-4710).")
        failed = True
    else:
        print("PASS BRIDGE — no addJavascriptInterface (no origin-less Java bridge)")

    print("")
    print("── sandbox settings (each line must EXIST; absence is a regression) ──")
    for name, pattern, why in REQUIRED_SETTINGS:
        rx = re.compile(pattern)
        if any(rx.search(ln) for _, _, ln in corpus):
            print(f"PASS {name} — {where(pattern)}")
        else:
            print(f"FAIL {name} — not found. {why}")
            failed = True

    print("")
    print("── navigation policy ──")

    # 3. Only http(s) may be navigated to.
    if re.search(r"shouldOverrideUrlLoading", blob) and \
            re.search(r"\"https\"|https?\"|it\s*==\s*\"http\"", blob):
        print(f"PASS scheme allowlist — shouldOverrideUrlLoading present at "
              f"{where('shouldOverrideUrlLoading')}")
    else:
        print("FAIL scheme allowlist — shouldOverrideUrlLoading must restrict "
              "navigation to http/https; file://, javascript: and content:// "
              "must be blocked.")
        failed = True

    # 4. Remote debugging must not be re-enabled anywhere (e.g. a debug build).
    if re.search(r"setWebContentsDebuggingEnabled\s*\(\s*true\s*\)", blob):
        print("FAIL debugging — setWebContentsDebuggingEnabled(true) found; "
              "remote debugging must stay off in shipped code.")
        failed = True
    else:
        print("PASS debugging — remote debugging never enabled")

    print("")
    print(f"AUDIT — scanned {len(files)} engine source file(s); "
          f"{len(corpus)} non-comment lines; no Android SDK or Gradle needed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())