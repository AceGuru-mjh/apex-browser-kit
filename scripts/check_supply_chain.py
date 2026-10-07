#!/usr/bin/env python3
"""
check_supply_chain.py - build-input integrity gate (Gradle wrapper + actions).

A library's reproducible build starts before any compiler runs: if the Gradle
distribution or the wrapper JAR is swapped, everything the build proves is
worthless. This gate is stdlib-only Python, so it runs in seconds on any
runner with no JDK or Android SDK.

  RULE 1  gradle-wrapper.properties pins `distributionSha256Sum` (Gradle
          verifies the distribution download against it) and the pinned
          distribution is a -bin (not -all) zip of an expected-major Gradle.
  RULE 2  gradle/wrapper/gradle-wrapper.jar is a real ZIP/JAR artifact of a
          sane size - not a text stub, not a Git-LFS pointer, not empty. A
          common supply-chain trick is replacing the binary wrapper with a
          downloader stub; size + magic bytes catch the cheap variants.
  RULE 3  No `curl ... | sh/bash` (or wget -O- piped to a shell) in
          .github/workflows - remote-code execution hidden in a workflow step
          bypasses every other check in this repo.

AUDIT (report-only, not failing): third-party `uses:` pins in workflows. Full
SHA pinning is the end state (it makes tag-mutability attacks fail closed),
but flipping it to a hard gate now would turn every Dependabot-style tag bump
into manual SHA chasing. The audit lists which pins are still floating tags so
the drift is visible; promote to RULE when the repo adopts a pinning bot.

Negative tests: scripts/tests/test_supply_chain_gate_negative.py.

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER_PROPS = ROOT / "gradle" / "wrapper" / "gradle-wrapper.properties"
WRAPPER_JAR = ROOT / "gradle" / "wrapper" / "gradle-wrapper.jar"
WORKFLOWS = ROOT / ".github" / "workflows"

# Gradle 8.x only (this repo's build scripts target Gradle 8 APIs).
DIST_RE = re.compile(r"gradle-(\d+)\.(\d+)(?:\.\d+)?-(bin|all)\.zip")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PIPE_SH_RE = re.compile(r"curl[^\n|]*\|\s*(ba)?sh|wget[^\n|]*\|\s*(ba)?sh",
                        re.IGNORECASE)
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
FIRST_PARTY = ("github/",)


def rule_wrapper_checksum() -> list[str]:
    hits: list[str] = []
    if not WRAPPER_PROPS.is_file():
        return [f"expected {WRAPPER_PROPS.relative_to(ROOT)} (gate cannot run)"]
    props = WRAPPER_PROPS.read_text(encoding="utf-8")
    dist = next((ln.split("=", 1)[1].strip()
                 for ln in props.splitlines()
                 if ln.startswith("distributionUrl=")), "")
    m = DIST_RE.search(dist.replace("\\", ""))
    if not m:
        hits.append(f"gradle-wrapper.properties: distributionUrl is not a "
                    f"recognised Gradle distribution: {dist!r}")
    else:
        major, _minor, kind = int(m.group(1)), m.group(2), m.group(3)
        if major != 8:
            hits.append(f"gradle-wrapper.properties: Gradle major {major} is "
                        f"unexpected (repo targets Gradle 8): {dist!r}")
        if kind != "bin":
            hits.append(f"gradle-wrapper.properties: use a -bin distribution, "
                        f"not -{kind} (smaller attack surface, faster CI)")
    sha = next((ln.split("=", 1)[1].strip()
                for ln in props.splitlines()
                if ln.startswith("distributionSha256Sum=")), "")
    if not sha:
        hits.append("gradle-wrapper.properties: missing distributionSha256Sum "
                    "- the wrapper downloads ~130 MB of executable code "
                    "without verifying it. Fix: add the official SHA-256 "
                    "from https://services.gradle.org/distributions/")
    elif not SHA_RE.match(sha):
        hits.append(f"gradle-wrapper.properties: distributionSha256Sum is not "
                    f"a 64-hex SHA-256: {sha!r}")
    return hits


def rule_wrapper_jar() -> list[str]:
    hits: list[str] = []
    if not WRAPPER_JAR.is_file():
        return [f"expected {WRAPPER_JAR.relative_to(ROOT)} "
                "(gradlew cannot bootstrap without it)"]
    raw = WRAPPER_JAR.read_bytes()
    if raw[:4] != b"PK\x03\x04":
        preview = raw[:60].decode("utf-8", errors="replace").strip()
        hits.append(f"gradle/wrapper/gradle-wrapper.jar: not a ZIP/JAR "
                    f"(magic mismatch) - refusing to run it. "
                    f"Head: {preview!r}")
        return hits
    if len(raw) < 30_000:
        hits.append(f"gradle/wrapper/gradle-wrapper.jar: suspiciously small "
                    f"({len(raw)} bytes) - a stub downloader, not the real "
                    f"wrapper")
    try:
        with zipfile.ZipFile(WRAPPER_JAR) as z:
            names = z.namelist()
            if not any(n.startswith("org/gradle/wrapper/")
                       for n in names):
                hits.append("gradle/wrapper/gradle-wrapper.jar: ZIP but no "
                            "org/gradle/wrapper/* entries - not the real "
                            "wrapper")
    except zipfile.BadZipFile:
        hits.append("gradle/wrapper/gradle-wrapper.jar: corrupt ZIP")
    return hits


def rule_no_pipe_to_shell() -> tuple[list[str], int]:
    hits: list[str] = []
    scanned = 0
    if not WORKFLOWS.is_dir():
        return ([f"expected {WORKFLOWS.relative_to(ROOT)} (gate cannot run)"],
                scanned)
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        scanned += 1
        text = wf.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if PIPE_SH_RE.search(line):
                hits.append(f"{wf.name}:{lineno}: pipe-to-shell in workflow "
                            f"(remote code execution): {s[:100]}")
    return hits, scanned


def audit_action_pins() -> list[str]:
    seen: set[str] = set()
    notes: list[str] = []
    if not WORKFLOWS.is_dir():
        return notes
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        for m in USES_RE.finditer(text):
            ref = m.group(1)
            if ref.startswith("./") or ref.startswith("docker://"):
                continue
            action, _, pin = ref.partition("@")
            first = action.split("/")[0]
            if first in FIRST_PARTY:
                continue  # github/* maintained by GitHub; tag risk accepted
            if not re.fullmatch(r"[0-9a-f]{40}", pin or ""):
                tag = pin or "(no pin)"
                key = f"{wf.name}:{action}@{tag}"
                if key in seen:
                    continue
                seen.add(key)
                notes.append(f"{wf.name}: {action} pinned to floating tag "
                             f"`{tag}` - promote to a full commit SHA")
    return notes


def main() -> int:
    failed = False
    r1 = rule_wrapper_checksum()
    r2 = rule_wrapper_jar()
    r3, wf_count = rule_no_pipe_to_shell()
    audit = audit_action_pins()

    for title, hits, fix in [
        ("RULE 1 - Gradle distribution checksum pin",
         r1, "add distributionSha256Sum=<official sha256> to "
              "gradle/wrapper/gradle-wrapper.properties"),
        ("RULE 2 - wrapper JAR integrity", r2,
         "restore gradle/wrapper/gradle-wrapper.jar from "
         "`gradle wrapper --gradle-version <v>` and commit the binary"),
        ("RULE 3 - no pipe-to-shell in workflows", r3,
         "vendor the script into the repo and run it from disk so it is "
         "reviewable"),
    ]:
        if hits:
            print(f"FAIL {title} ({len(hits)}):")
            for h in hits:
                print(f"  - {h}")
            print(f"   Fix: {fix}")
            failed = True
        else:
            print(f"PASS {title}")

    print(f"\nAUDIT - scanned {wf_count} workflow file(s)")
    if audit:
        print(f"AUDIT - {len(audit)} floating third-party action pin(s) "
              "(report-only; promote to a gate with a pinning bot):")
        for n in audit:
            print(f"  - {n}")
    else:
        print("AUDIT - all third-party actions pinned to full commit SHAs")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
