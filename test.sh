#!/usr/bin/env bash
# ============================================================================
# test.sh — single local entry point mirroring CI.
#
# A PR template that says "run ./test.sh" is only useful because this script
# exists; borrowed from coil, which keeps its template to a single line for
# exactly this reason.
#
#   ./test.sh          full local check (gates + pure-JVM tests)
#   ./test.sh gates    structural gates only — no Gradle, seconds
#   ./test.sh tests    Gradle tests only
#
# `gates` needs neither a JDK nor an Android SDK (pure stdlib Python 3), so it
# is runnable on a bare machine — which is what makes the feedback loop tight.
# `tests` needs a JDK 17; Android modules additionally need an SDK.
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; NC=$'\033[0m'
FAILED=0

step() {
    local name="$1"; shift
    echo ""
    echo "── $name ──"
    if "$@"; then
        echo "${GREEN}✔ $name${NC}"
    else
        echo "${RED}✘ $name${NC}"
        FAILED=1
    fi
}

run_gates() {
    step "core purity (zero-Android + one-way arrow)"      python3 scripts/check_core_purity.py
    step "JS injection boundary"                           python3 scripts/check_js_injection.py
    step "WebView sandbox hardening"                       python3 scripts/check_webview_hardening.py
    step "resources (prefix + locale mirror)"              python3 scripts/check_resources.py
    step "structural quality (budget + anti-patterns)"     python3 scripts/check_code_quality.py
    step "secrets (no credential literals in repo)"         python3 scripts/check_secrets.py
    step "supply chain (wrapper checksum + JAR + workflows)" python3 scripts/check_supply_chain.py
    step "versions (single release train + README tracks it)" python3 scripts/check_versions.py
    step "public API surface (no REMOVED/CHANGED vs api/)"  python3 scripts/check_api_surface.py
    step "kotlin bracket balance (lexer-aware)" \
        python3 scripts/kotlin_balance.py \
        $(find . -name "*.kt" -not -path "./.git/*" -not -path "*/build/*")
}

run_gate_selftests() {
    step "gate self-tests (gates must detect real violations)" \
        python3 scripts/tests/test_gates_negative.py
    step "resource gate self-test" python3 scripts/tests/test_resource_gate_negative.py
    step "code-quality gate self-test" python3 scripts/tests/test_code_quality_gate_negative.py
    step "webview-hardening gate self-test" python3 scripts/tests/test_webview_gate_negative.py
    step "secrets gate self-test" python3 scripts/tests/test_secrets_gate_negative.py
    step "supply-chain gate self-test" python3 scripts/tests/test_supply_chain_gate_negative.py
    step "versions gate self-test" python3 scripts/tests/test_versions_gate_negative.py
    step "API-surface gate self-test" python3 scripts/tests/test_api_gate_negative.py
}

run_gradle_tests() {
    if ! command -v java >/dev/null 2>&1; then
        echo "${YELLOW}! JDK not found on PATH — skipping Gradle tests.${NC}"
        echo "  Only the gates above need no JDK. Install JDK 17 to run tests."
        return 0
    fi
    # :browser-core is a plain JVM module: the single highest-value test target,
    # and the one that proves the zero-Android-dependency claim for real.
    step ":browser-core:test (pure JVM, no Android SDK)" \
        ./gradlew :browser-core:test --no-daemon
    step ":browser-engine:testDebugUnitTest" \
        ./gradlew :browser-engine:testDebugUnitTest --no-daemon
    step ":browser-chrome:testDebugUnitTest" \
        ./gradlew :browser-chrome:testDebugUnitTest --no-daemon
}

case "${1:-all}" in
    gates)
        run_gates
        run_gate_selftests
        ;;
    selftests)
        run_gate_selftests
        ;;
    tests)
        run_gradle_tests
        ;;
    all)
        run_gates
        run_gate_selftests
        run_gradle_tests
        ;;
    *)
        echo "usage: ./test.sh [all|gates|selftests|tests]"
        exit 2
        ;;
esac

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "${GREEN}All checks passed.${NC}"
else
    echo "${RED}Some checks failed.${NC}"
fi
exit $FAILED