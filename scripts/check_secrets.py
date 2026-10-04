#!/usr/bin/env python3
"""
check_secrets.py - secret / credential leak gate.

Why this matters here: CI diffs and PR bodies for this repo get pasted around
(OpenRouter keys, GitHub PATs, Android keystore passwords), and a library that
publishes coordinates other people consume must never commit a credential -
leaking a token in a published repo poisons the dependency graph it feeds.

Two rules:

  RULE 1  No high-confidence credential literals anywhere in tracked content:
          GitHub tokens, AWS keys, Google API keys, Slack tokens, PEM private
          keys, OpenRouter / Anthropic API keys.
  RULE 2  No generic `password / secret / api_key / token = "..."` assignments
          whose value is not an obvious placeholder, EXCEPT reads from the
          environment (`System.getenv(...)`, `$VAR`, `${VAR}`) and references
          to secret *names* (e.g. `secrets.GITHUB_TOKEN` in workflows).

The gate scripts themselves contain token *patterns* (not tokens), so the
patterns are built from fragments that never appear literally in this file -
otherwise the gate would fail on itself. Negative tests under
scripts/tests/test_secrets_gate_negative.py inject real token shapes into a
scratch copy and assert the gate fires.

Known exclusions (documented, not silent):

  - scripts/check_secrets.py itself and its negative tests (token shapes).
  - .md / .kt prose that quotes a token *prefix* as documentation,
    e.g. `github_pat_` shown as "rotate tokens like this". Documented redacted
    examples must use obviously fake values ("ghp_example...REMOVE...", 3-4
    chars of payload max) so this self-exclusion stays honest.
  - .git/, build/, .gradle/, .kotlin/ (generated, unreviewed).

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── High-confidence token shapes (fragment-built so they never match source) ──
GH = "gh" + "p_"                       # GitHub classic PAT prefix
GHU = "gh" + "o_"                      # OAuth
GHS = "gh" + "s_"                      # server-to-server
GHU2 = "gh" + "u_"                     # user-to-server
GHR = "gh" + "r_"                      # refresh
GPAT = "github" + "_pat_"              # fine-grained PAT
AK = "AK" + "IA"                       # AWS access-key prefix
GOOG = "AI" + "za"                     # Google API key prefix
SLACK = "xox" + "b-"
ORKEY = "sk-or-v1-"                    # OpenRouter
AKEY = "sk-ant-"                       # Anthropic

RULE1_RES = [
    re.compile(re.escape(GH) + r"[A-Za-z0-9]{20,}"),
    re.compile(re.escape(GHU) + r"[A-Za-z0-9]{20,}"),
    re.compile(re.escape(GHS) + r"[A-Za-z0-9]{20,}"),
    re.compile(re.escape(GHU2) + r"[A-Za-z0-9]{20,}"),
    re.compile(re.escape(GHR) + r"[A-Za-z0-9]{20,}"),
    re.compile(re.escape(GPAT) + r"[A-Za-z0-9_]{10,}"),
    re.compile(re.escape(AK) + r"[0-9A-Z]{16}"),
    re.compile(re.escape(GOOG) + r"[0-9A-Za-z\-_]{35}"),
    re.compile(re.escape(SLACK) + r"[0-9A-Za-z\-]+"),
    re.compile(re.escape(ORKEY) + r"[0-9A-Za-z]{10,}"),
    re.compile(re.escape(AKEY) + r"[0-9A-Za-z\-_]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY"),
    re.compile(r"aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{30,}"),
]

# ── RULE 2: generic secret assignments with non-placeholder values ────────────
# The RHS is captured whole and judged as one string: only scalar-looking
# values (no parens/brackets/whitespace/operators = not a code expression)
# are flaggable. `password: q('input[type=password]')` or
# `val password = signals["password"]` are code, not credentials.
ASSIGN_RE = re.compile(
    r"""(?ix)
    # Block identifier-embedded keys (`releasePassword`, `my.token`) but allow
    # separator-joined secret names (`release_password`, `api-key`): the guard
    # is "not preceded by alnum/dot"; `_`/`-`/line-start may precede.
    (?<![A-Za-z0-9.])(password|passwd|pwd|secret|token|api[_-]?key|api[_-]?token|
       auth[_-]?token|access[_-]?token|private[_-]?key|client[_-]?secret)\s*
    [:=]\s*(?P<rhs>.+?)\s*$
    """
)
# A scalar credential value: quoted string or a bare token without any
# code-expression characters.
SCALAR_RE = re.compile(r"""^(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9._\-/+*=]+))$""")
# Values that are obviously not credentials (env reads, placeholders, refs).
PLACEHOLDER_RES = [
    re.compile(r"^\$\{?[A-Z0-9_]+\}?$"),          # $VAR / ${VAR}
    re.compile(r"^System\.getenv\(.*\)$"),
    re.compile(r"^secrets\.[A-Z0-9_]+$"),        # GitHub secrets.* context
    re.compile(r"^sys\.props?\..*$", re.IGNORECASE),
    re.compile(r"^BuildConfig\..*$"),
    re.compile(r"^(your|my|example|sample|xxx+|todo|fixme|changeme|placeholder|"
               r"none|null|empty|test123|password123|<.*>|xxx)$", re.IGNORECASE),
    re.compile(r"^\*+$"),                         # *****
    re.compile(r"^x{3,}$", re.IGNORECASE),
    re.compile(r"^\.\.\.$"),
    re.compile(r"^here$", re.IGNORECASE),
    re.compile(r"^[0-9]+$"),                      # port-like numbers
    re.compile(r"^true|false$"),
]

SELF_EXCLUDE = {
    "scripts/check_secrets.py",
    "scripts/tests/test_secrets_gate_negative.py",
}
SKIP_DIRS = {".git", "build", ".gradle", ".kotlin", ".idea", ".vscode"}
SCAN_SUFFIXES = {
    ".kt", ".java", ".kts", ".gradle", ".properties", ".yml", ".yaml",
    ".xml", ".json", ".md", ".sh", ".py", ".toml", ".cfg", ".ini", ".pro",
    ".env", "Dockerfile",
}
SCAN_BASENAMES = {"local.properties", ".env", "Dockerfile"}


def iter_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel in SELF_EXCLUDE:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if p.suffix.lower() in SCAN_SUFFIXES or p.name in SCAN_BASENAMES:
            yield p


def scan() -> tuple[list[str], int]:
    hits: list[str] = []
    scanned = 0
    for f in iter_files():
        try:
            text = f.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        rel = f.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rx in RULE1_RES:
                if rx.search(line):
                    hits.append(f"{rel}:{lineno}: high-confidence credential "
                                f"literal: {line.strip()[:90]}")
                    break
            else:
                m = ASSIGN_RE.search(line)
                if m:
                    rhs = m.group("rhs").strip().rstrip(",;")
                    sm = SCALAR_RE.match(rhs)
                    value = sm.group(1) if sm and sm.group(1) is not None \
                        else (sm.group(2) if sm and sm.group(2) is not None
                              else (sm.group(3) if sm else None))
                    if (value is not None
                            and not any(rx.match(value)
                                        for rx in PLACEHOLDER_RES)):
                        hits.append(f"{rel}:{lineno}: secret assignment with "
                                    f"a concrete value (key `{m.group(1)}`): "
                                    f"{line.strip()[:90]}")
    return hits, scanned


def main() -> int:
    hits, scanned = scan()
    if hits:
        print(f"FAIL secret gate ({len(hits)} hit(s) in {scanned} files):")
        for h in hits:
            print(f"  - {h}")
        print()
        print("   Fix: delete the credential, rotate it (it is public now), and")
        print("   read it from the environment or a secrets manager instead.")
        print("   Legitimate redacted examples must use obviously fake values.")
        return 1
    print(f"PASS secret gate: {scanned} text files scanned, "
          "no credential literals or concrete secret assignments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
