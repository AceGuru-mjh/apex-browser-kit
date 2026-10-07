#!/usr/bin/env python3
"""
Negative tests for scripts/check_secrets.py.

Each case injects a REAL token shape (random filler, not a real credential)
into a scratch copy and asserts the gate fails mentioning it - plus one
positive control asserting the gate stays silent on documented placeholders.
"""
from __future__ import annotations

import secrets as secretlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent.parent  # repo root
PY = sys.executable

results: list[tuple[bool, str]] = []


def run_gate(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(repo / "scripts" / "check_secrets.py")],
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
            print(f"       exit={proc.returncode} (want non-zero)")
            print("       --- gate output ---")
            for line in out.splitlines()[:12]:
                print("       " + line)


def mutate_github_token(repo: Path) -> None:
    rnd = secretlib.token_hex(19)[:36]
    f = repo / "browser-core" / "build.gradle.kts"
    s = f.read_text(encoding="utf-8")
    s += f'\n// debug helper\nval debugToken = "ghp_{rnd}"\n'
    f.write_text(s, encoding="utf-8")


def mutate_aws_key(repo: Path) -> None:
    f = repo / "gradle.properties"
    s = f.read_text(encoding="utf-8")
    s += "\naws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
    f.write_text(s, encoding="utf-8")


def mutate_private_key(repo: Path) -> None:
    f = repo / "browser-engine" / "consumer-rules.pro"
    s = f.read_text(encoding="utf-8")
    s += "\n# pinned key\n# -----BEGIN RSA PRIVATE KEY-----\n"
    f.write_text(s, encoding="utf-8")


def mutate_secret_assign(repo: Path) -> None:
    # gradle.properties leak shape: a bare key with a concrete scalar value.
    # (Identifier-embedded keys like `val releasePassword = ...` are code, not
    # leaks, and deliberately do NOT fire - see the gate docstring.)
    f = repo / "gradle.properties"
    s = f.read_text(encoding="utf-8")
    s += "\nrelease_password = s3cr3t-prod-password\n"
    f.write_text(s, encoding="utf-8")


case("catches a GitHub PAT literal", mutate_github_token, "credential")
case("catches an AWS access key", mutate_aws_key, "credential")
case("catches a PEM private-key header", mutate_private_key, "credential")
case("catches a concrete password assignment", mutate_secret_assign,
     "secret assignment")

# ── Positive control: documented placeholders must stay silent ────────────────
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    shutil.copytree(
        SRC, repo,
        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"),
    )
    f = repo / "README.md"
    s = f.read_text(encoding="utf-8")
    s += ("\nCI reads it back:\n"
          "```\npassword = System.getenv(\"SIGNING_PASSWORD\")\n"
          "token = secrets.GITHUB_TOKEN\n"
          "```\n")
    f.write_text(s, encoding="utf-8")
    proc = run_gate(repo)
    ok = proc.returncode == 0
    results.append((ok, "stays silent on env/placeholder secret references"))
    print(f"[{'PASS' if ok else 'FAIL'}] stays silent on env/placeholder "
          "secret references")
    if not ok:
        for line in (proc.stdout + proc.stderr).splitlines()[:12]:
            print("       " + line)

# ── Summary ───────────────────────────────────────────────────────────────────
ok = sum(1 for r, _ in results if r)
total = len(results)
print(f"\n{ok}/{total} secret-gate negative-tests behaved correctly")
if ok != total:
    print("FAILURES:")
    for r, n in results:
        if not r:
            print(f"  - {n}")
    sys.exit(1)
print("ALL SECRET-GATE NEGATIVE-TESTS PASS")
