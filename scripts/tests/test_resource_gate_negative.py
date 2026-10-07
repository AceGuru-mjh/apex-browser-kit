#!/usr/bin/env python3
"""Negative tests for check_resources.py (prefix + locale mirror + placeholders)."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent.parent  # repo root
PY = sys.executable
REL = "browser-chrome/src/main/res"

results: list[tuple[bool, str]] = []


def run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(repo / "scripts/check_resources.py")],
                          capture_output=True, text=True, cwd=repo)


def case(name: str, mutate, expect: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(SRC, repo,
                        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"))
        mutate(repo)
        p = run(repo)
        out = p.stdout + p.stderr
        ok = p.returncode != 0 and expect in out
        results.append((ok, name))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"       exit={p.returncode} expect={expect!r}")
            for line in out.splitlines()[:8]:
                print("       " + line)


def case_passes(name: str, mutate) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(SRC, repo,
                        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"))
        mutate(repo)
        p = run(repo)
        ok = p.returncode == 0
        results.append((ok, name))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            for line in (p.stdout + p.stderr).splitlines()[:8]:
                print("       " + line)


def drop_key_from_en(repo: Path) -> None:
    f = repo / REL / "values-en/strings.xml"
    s = f.read_text(encoding="utf-8")
    i = s.index('name="browser_nav_back"')
    start = s.rindex("<string", 0, i)
    end = s.index("</string>", i) + len("</string>")
    f.write_text(s[:start] + s[end:], encoding="utf-8")


case("catches a key present in values/ but missing in values-en/",
     drop_key_from_en, "browser_nav_back")


def add_unprefixed(repo: Path) -> None:
    f = repo / REL / "values/strings.xml"
    s = f.read_text(encoding="utf-8")
    s = s.replace("</resources>",
                  '  <string name="ok_button">OK</string>\n</resources>')
    f.write_text(s, encoding="utf-8")
    f2 = repo / REL / "values-en/strings.xml"
    s2 = f2.read_text(encoding="utf-8")
    s2 = s2.replace("</resources>",
                    '  <string name="ok_button">OK</string>\n</resources>')
    f2.write_text(s2, encoding="utf-8")


case("catches a resource missing the browser_ prefix (host R collision)",
     add_unprefixed, "ok_button")


def break_placeholder(repo: Path) -> None:
    # Strip a placeholder from the English mirror -> parity violation.
    f = repo / REL / "values-en/strings.xml"
    s = f.read_text(encoding="utf-8")
    s = s.replace("%1$s", "%s", 1)
    f.write_text(s, encoding="utf-8")


case("catches placeholder mismatch between locales",
     break_placeholder, "占位符不一致")


def delete_mirror_file(repo: Path) -> None:
    (repo / REL / "values-en/strings.xml").unlink()


case("catches a missing locale mirror file",
     delete_mirror_file, "缺同名镜像文件")


def nothing(repo: Path) -> None:
    return None


case_passes("passes on the real repo (no false positive)", nothing)

ok = sum(1 for r, _ in results if r)
print(f"\n{ok}/{len(results)} resource-gate negative-tests behaved correctly")
sys.exit(0 if ok == len(results) else 1)