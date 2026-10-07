#!/usr/bin/env python3
"""
check_resources.py — resource-prefix and locale-parity gate for :browser-chrome.

Two failure modes this prevents, both of which only show up inside a *host*
app because this is a consumed library:

  CHECK 1  Resource prefix. ``browser-chrome`` sets ``resourcePrefix =
           "browser_"`` so the library's resources cannot collide with the
           host's ``R``. But ``resourcePrefix`` is enforced by AGP only as a
           *lint warning* — it still builds, and the collision surfaces at the
           host as a silently wrong resource id. Gated here instead.

  CHECK 2  Locale mirror parity. ``res/values`` and ``res/values-en`` must
           expose the same key set, and format placeholders must match per key.
           The sibling repo learned this the expensive way (#291: 528 keys
           verified by hand, because a missing key only explodes when a user
           switches language). 75 keys here, so the discipline is cheap to
           enforce.

  CHECK 3  Placeholder parity per key (part of CHECK 2's second half), reported
           separately because "English has two args, translation has one" is a
           runtime crash, not a cosmetic gap.

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "browser-chrome" / "src" / "main" / "res"
REQUIRED_PREFIX = "browser_"
# Android format specifiers: %1$s, %s, %d, %%
PLACEHOLDER = re.compile(r"%\d+\$[sd]|%[sd]|%%")


def parse_keys(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    return {
        n.get("name"): "".join(n.itertext())
        for n in root.iter("string")
        if n.get("name")
    }


def main() -> int:
    values = RES / "values"
    if not values.is_dir():
        print(f"FAIL: expected {values} (gate cannot run)")
        return 1

    problems: list[str] = []
    files = sorted(values.glob("strings*.xml"))
    if not files:
        print(f"FAIL: no strings*.xml under {values}")
        return 1

    total_keys = 0
    prefixed = 0

    for default_path in files:
        rel = default_path.relative_to(ROOT).as_posix()
        try:
            default = parse_keys(default_path)
        except ET.ParseError as e:
            problems.append(f"{rel}: XML 解析失败 — {e}")
            continue

        # ── CHECK 1: prefix ────────────────────────────────────────────────
        for name in default:
            if name.startswith(REQUIRED_PREFIX):
                prefixed += 1
            else:
                problems.append(
                    f"{rel}: 资源 `{name}` 缺少 `{REQUIRED_PREFIX}` 前缀 —— 会与宿主 R 类冲突"
                )
        total_keys += len(default)

        # ── CHECK 2 / 3: locale mirror + placeholder parity ───────────────
        for locale_dir in sorted(p for p in RES.iterdir() if p.is_dir() and p.name != "values"):
            mirror_path = locale_dir / default_path.name
            if not mirror_path.exists():
                problems.append(
                    f"{locale_dir.name}/: 缺同名镜像文件 {default_path.name}"
                )
                continue
            try:
                mirror = parse_keys(mirror_path)
            except ET.ParseError as e:
                problems.append(f"{mirror_path.relative_to(ROOT)}: XML 解析失败 — {e}")
                continue

            only_default = sorted(set(default) - set(mirror))
            only_mirror = sorted(set(mirror) - set(default))
            for k in only_default:
                problems.append(
                    f"{locale_dir.name}/{default_path.name}: 键 `{k}` "
                    f"在默认 locale 存在、{locale_dir.name} 缺失（切语言才炸）"
                )
            for k in only_mirror:
                problems.append(
                    f"{default_path.name}: 键 `{k}` 在 {locale_dir.name} 存在、"
                    f"默认 locale 缺失（默认语言是键的真源）"
                )
            for k in sorted(set(default) & set(mirror)):
                pd = sorted(PLACEHOLDER.findall(default[k]))
                pm = sorted(PLACEHOLDER.findall(mirror[k]))
                if pd != pm:
                    problems.append(
                        f"{locale_dir.name}/{default_path.name}: 键 `{k}` "
                        f"占位符不一致 default={pd} {locale_dir.name}={pm}"
                    )

    if problems:
        print(f"FAIL resource gate ({len(problems)} 项):\n")
        for p in problems:
            print(f"  - {p}")
        print()
        print("   Fix: add the `browser_` prefix (build.gradle.kts resourcePrefix),")
        print("   and keep res/values and every res/values-* key set + placeholders")
        print("   in 1:1 parity.")
        return 1

    print(f"PASS resource gate: {len(files)} strings file(s), {total_keys} keys checked, "
          f"{prefixed} resources carry the `{REQUIRED_PREFIX}` prefix, "
          f"all locale mirrors in 1:1 parity with matching placeholders")
    return 0


if __name__ == "__main__":
    sys.exit(main())