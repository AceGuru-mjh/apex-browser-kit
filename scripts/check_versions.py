#!/usr/bin/env python3
"""
check_versions.py - single-source-of-truth gate for versions and coordinates.

Repeated real failure mode in this repo: the version was bumped in three
build.gradle.kts files but the CI smoke check still grepped a hardcoded 1.0.0
(main@8f37a25), and the README's consumer snippets still say 1.0.0 while the
build files say 1.1.0. A published library whose README tells consumers to
depend on a stale coordinate is a silent break.

  RULE 1  All three module build files declare the same `version = "x.y.z"`,
          and the mavenPublishing `coordinates(...)` inside each file agree
          with it.
  RULE 2  README consumer snippets (`com.apex.browser:<artifact>:<v>`) match
          that version - docs must not point at a version that no longer
          exists or an older one consumers would silently pin to.
  RULE 3  README compat matrix (Kotlin / AGP / Compose BOM / compileSdk /
          minSdk) matches gradle/libs.versions.toml and the android blocks.
          Same shape as RULE 2: the matrix is what a host checks before
          upgrading, so drift becomes a downstream build break.
  RULE 4  gradle-wrapper.properties distribution version matches the wrapper
          JAR manifest (a hand-edited properties file pointing at a different
          Gradle than the committed wrapper is a classic merge accident).

Negative tests: scripts/tests/test_versions_gate_negative.py.

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ("browser-core", "browser-engine", "browser-chrome")
README = ROOT / "README.md"
CATALOG = ROOT / "gradle" / "libs.versions.toml"
WRAPPER_PROPS = ROOT / "gradle" / "wrapper" / "gradle-wrapper.properties"
WRAPPER_JAR = ROOT / "gradle" / "wrapper" / "gradle-wrapper.jar"

VER_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
COORD_RE = re.compile(
    r"coordinates\(\s*groupId\s*=\s*\"([^\"]+)\"\s*,\s*"
    r"artifactId\s*=\s*\"([^\"]+)\"\s*,\s*"
    r"version\s*=\s*\"([^\"]+)\"", re.DOTALL)
README_COORD_RE = re.compile(r"com\.apex\.browser:([a-z-]+):([0-9][^\"'\s]*)")
COMPILE_SDK_RE = re.compile(r"compileSdk\s*=\s*(\d+)")
MIN_SDK_RE = re.compile(r"minSdk\s*=\s*(\d+)")
TOML_VER_RE = re.compile(r'^([a-z][\w-]*)\s*=\s*"([^"]+)"')
MATRIX_PATTERNS = {
    "Kotlin": (re.compile(r"\|\s*Kotlin\s*\|\s*([0-9][^|\s]*)\s*\|"),
               "kotlin"),
    "AGP": (re.compile(r"\|\s*AGP\s*\|\s*([0-9][^|\s]*)\s*\|"),
            "agp"),
    "Compose BOM": (re.compile(r"\|\s*Compose BOM\s*\|\s*([0-9][^|\s]*)\s*\|"),
                    "compose-bom"),
    "compileSdk / minSdk":
        (re.compile(r"compileSdk.*?\|\s*(\d+)\s*/\s*(\d+)"), None),
}


def parse_toml_versions() -> dict[str, str]:
    if not CATALOG.is_file():
        return {}
    out: dict[str, str] = {}
    in_versions = False
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("["):
            in_versions = s == "[versions]"
            continue
        if in_versions and not s.startswith("#") and "=" in s:
            m = TOML_VER_RE.match(s)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def rule_module_versions() -> tuple[list[str], str]:
    """Return (problems, agreed_version)."""
    hits: list[str] = []
    versions: dict[str, str] = {}
    for mod in MODULES:
        f = ROOT / mod / "build.gradle.kts"
        if not f.is_file():
            hits.append(f"{mod}/build.gradle.kts: missing (gate cannot run)")
            continue
        text = f.read_text(encoding="utf-8")
        m = VER_RE.search(text)
        if not m:
            hits.append(f"{mod}/build.gradle.kts: no `version = \"x\"` found")
            continue
        versions[mod] = m.group(1)
        for g, a, v in COORD_RE.findall(text):
            if g != "com.apex.browser" or a != mod or v != versions[mod]:
                hits.append(f"{mod}/build.gradle.kts: coordinates "
                            f"{g}:{a}:{v} disagree with version "
                            f"{versions[mod]!r}")
    agreed = ""
    if len(set(versions.values())) > 1:
        detail = ", ".join(f"{m}={v}" for m, v in sorted(versions.items()))
        hits.append(f"module versions disagree (single release train "
                    f"required): {detail}")
    elif versions:
        agreed = next(iter(versions.values()))
    return hits, agreed


def rule_readme_coordinates(version: str) -> list[str]:
    if not version or not README.is_file():
        return []
    hits: list[str] = []
    for lineno, line in enumerate(
            README.read_text(encoding="utf-8").splitlines(), start=1):
        for art, v in README_COORD_RE.findall(line):
            if art not in MODULES:
                continue
            if v != version:
                hits.append(f"README.md:{lineno}: consumer snippet pins "
                            f"com.apex.browser:{art}:{v} but the release "
                            f"train is {version} - bump the docs with the "
                            f"version or consumers pin a stale artifact")
    return hits


def rule_compat_matrix() -> list[str]:
    hits: list[str] = []
    if not README.is_file():
        return ["README.md: missing (gate cannot run)"]
    readme = README.read_text(encoding="utf-8")
    toml = parse_toml_versions()

    for label, (rx, key) in MATRIX_PATTERNS.items():
        m = rx.search(readme)
        if not m:
            hits.append(f"README.md: compat matrix row `{label}` not found "
                        "(matrix must stay machine-checkable)")
            continue
        if label == "compileSdk / minSdk":
            cs, ms = m.group(1), m.group(2)
            for mod in ("browser-engine", "browser-chrome"):
                text = (ROOT / mod / "build.gradle.kts").read_text(
                    encoding="utf-8")
                c = COMPILE_SDK_RE.search(text)
                mi = MIN_SDK_RE.search(text)
                if (not c or not mi or c.group(1) != cs
                        or mi.group(1) != ms):
                    hits.append(f"README.md: matrix says compileSdk/minSdk "
                                f"{cs}/{ms} but :{mod} declares "
                                f"{c.group(1) if c else '?'}/"
                                f"{mi.group(1) if mi else '?'}")
        elif key and key in toml and m.group(1) != toml[key]:
            hits.append(f"README.md: matrix says {label} {m.group(1)} but "
                        f"libs.versions.toml says {toml[key]}")
    return hits


def rule_wrapper_agreement() -> list[str]:
    if not (WRAPPER_PROPS.is_file() and WRAPPER_JAR.is_file()):
        return []
    props = WRAPPER_PROPS.read_text(encoding="utf-8")
    m = re.search(r"gradle-(\d+\.\d+(?:\.\d+)?)-", props)
    if not m:
        return []
    prop_ver = m.group(1)
    try:
        with zipfile.ZipFile(WRAPPER_JAR) as z:
            names = z.namelist()
            mf = next((n for n in names
                       if n.endswith("META-INF/MANIFEST.MF")), None)
            if not mf:
                return []
            manifest = z.read(mf).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        return []  # check_supply_chain owns JAR integrity
    mv = re.search(r"Implementation-Version:\s*(\S+)", manifest)
    if mv and not mv.group(1).startswith(prop_ver):
        return [f"gradle-wrapper.properties points at Gradle {prop_ver} but "
                f"the committed wrapper JAR is {mv.group(1)} - regenerate "
                f"with `gradle wrapper --gradle-version {prop_ver}`"]
    return []


def main() -> int:
    failed = False
    r1, version = rule_module_versions()
    r2 = rule_readme_coordinates(version)
    r3 = rule_compat_matrix()
    r4 = rule_wrapper_agreement()
    for title, hits in [
        ("RULE 1 - single release-train version + coordinates agree", r1),
        ("RULE 2 - README consumer snippets track the release train", r2),
        ("RULE 3 - README compat matrix matches build files", r3),
        ("RULE 4 - wrapper properties agree with the wrapper JAR", r4),
    ]:
        if hits:
            print(f"FAIL {title} ({len(hits)}):")
            for h in hits:
                print(f"  - {h}")
            failed = True
        else:
            extra = f" (release train {version})" if "RULE 1" in title else ""
            print(f"PASS {title}{extra}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
