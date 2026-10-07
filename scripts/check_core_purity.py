#!/usr/bin/env python3
"""
check_core_purity.py — enforce the module boundary that IS this library.

``:browser-core`` is advertised as "纯 Kotlin JVM（零 Android 依赖）". That claim
is what lets a consumer use it on a plain JVM (offline snapshot parsing, server
side, plain-JVM fixtures) and what keeps the dependency arrow one-way
(chrome -> engine -> core). Nothing enforced it, so one convenient
``import android.util.Log`` would silently turn it into an Android module.

Two rules, both free (no Android SDK, no Gradle):

  RULE 1  ``:browser-core`` imports NO ``android.*`` / ``androidx.*``
  RULE 2  ``:browser-core`` imports NOTHING from ``engine`` / ``chrome``

RULE 2 matters as much as RULE 1: a core -> engine import is a dependency cycle
waiting to happen, and AGP surfaces it much later as a confusing build failure.

Test sources are included on purpose: an ``android.*`` import in a core unit test
means the test cannot run on a plain JVM either, which quietly invalidates the
"run core tests without a device" claim the README makes.

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "browser-core" / "src"

# Captures the WHOLE dotted path, not just the last segment: capturing the last
# segment made `import android.util.Log` register as package "Log", so RULE 1
# could never fire. (Found by scripts' own negative tests, not by inspection.)
IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)

ANDROID_HITS: list[str] = []
UPWARD_HITS: list[str] = []
SCANNED = 0


def scan() -> None:
    global SCANNED
    if not CORE.is_dir():
        print(f"FAIL: expected source dir {CORE} (gate cannot run)")
        sys.exit(1)

    for kt in sorted(CORE.rglob("*.kt")):
        if "/build/" in kt.as_posix():
            continue
        SCANNED += 1
        for lineno, line in enumerate(
            kt.read_text(encoding="utf-8").splitlines(), start=1
        ):
            m = IMPORT_RE.match(line)
            if not m:
                continue
            fqn = m.group(1)
            root = fqn.split(".")[0]
            rel = kt.relative_to(ROOT).as_posix()
            loc = f"{rel}:{lineno}: {line.strip()}"
            if root in ("android", "androidx"):
                ANDROID_HITS.append(loc)
            elif fqn.startswith("com.apex.browser.engine.") or fqn.startswith(
                "com.apex.browser.chrome."
            ):
                UPWARD_HITS.append(loc)


def main() -> int:
    scan()
    failed = False

    if ANDROID_HITS:
        print("FAIL RULE 1 - ':browser-core' must stay zero-Android "
              "(it is a plain JVM module):")
        for h in ANDROID_HITS:
            print(f"  - {h}")
        print()
        print("   Fix: move Android-touching code to ':browser-engine' (which already")
        print("   depends on core), or drop the dependency. See README<模块结构>.")
        failed = True
    else:
        print("PASS RULE 1 - ':browser-core' has zero android.*/androidx.* imports")

    if UPWARD_HITS:
        print("FAIL RULE 2 - dependency arrow must stay one-way "
              "(chrome -> engine -> core):")
        for h in UPWARD_HITS:
            print(f"  - {h}")
        print()
        print("   Fix: ':browser-core' is the bottom of the stack and must not know")
        print("   about engine/chrome. Invert with an interface owned by core rather")
        print("   than importing upward.")
        failed = True
    else:
        print("PASS RULE 2 - ':browser-core' does not import engine/chrome")

    print(f"\nAUDIT - scanned {SCANNED} Kotlin files under browser-core/src "
          f"(main + test); no Android SDK or Gradle needed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())