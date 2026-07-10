#!/usr/bin/env bash
# =============================================================================
# EvalOps Security Scanning Script
# =============================================================================
# Runs automated security checks against the backend codebase.
# Usage: bash scripts/security-scan.sh
# Exit codes: 0 = clean, 1 = issues found, 2 = tool missing
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
REPORT_DIR="$PROJECT_ROOT/security-reports"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
REPORT_FILE="$REPORT_DIR/scan-$TIMESTAMP.txt"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

ISSUES_FOUND=0

# Create report directory
mkdir -p "$REPORT_DIR"

echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  EvalOps Security Scan — $(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "Report: $REPORT_FILE"
echo ""

# Helper
header() {
    echo -e "\n${CYAN}━━━ $1 ━━━${NC}"
    echo "=== $1 ===" >> "$REPORT_FILE"
}

pass() {
    echo -e "  ${GREEN}✓ $1${NC}"
    echo "  PASS: $1" >> "$REPORT_FILE"
}

warn() {
    echo -e "  ${YELLOW}⚠ $1${NC}"
    echo "  WARN: $1" >> "$REPORT_FILE"
}

fail() {
    echo -e "  ${RED}✗ $1${NC}"
    echo "  FAIL: $1" >> "$REPORT_FILE"
    ISSUES_FOUND=$((ISSUES_FOUND + 1))
}

# ─────────────────────────────────────────────────────────────
# 1. BANDIT — Python Security Linter
# ─────────────────────────────────────────────────────────────
header "1. BANDIT — Python Security Linter"

if command -v bandit &> /dev/null; then
    echo "Running bandit on backend/ ..."
    BANDIT_OUTPUT=$(bandit -r "$PROJECT_ROOT/backend" \
        --severity-level medium \
        --confidence-level medium \
        -f json 2>/dev/null || true)

    BANDIT_COUNT=$(echo "$BANDIT_OUTPUT" | python -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(len(data.get('results', [])))
except:
    print('0')
" 2>/dev/null || echo "0")

    echo "$BANDIT_OUTPUT" > "$REPORT_DIR/bandit-$TIMESTAMP.json"

    if [ "$BANDIT_COUNT" = "0" ]; then
        pass "No medium/high severity issues found by bandit"
    else
        fail "Bandit found $BANDIT_COUNT issue(s) — see bandit-$TIMESTAMP.json"
        echo "$BANDIT_OUTPUT" | python -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for r in data.get('results', []):
        print(f\"  [{r['issue_severity']}] {r['filename']}:{r['line_number']} — {r['issue_text']}\")
except:
    pass
" 2>/dev/null || true
    fi
else
    warn "bandit not installed — install with: pip install 'bandit[toml]'"
    echo "  Install: pip install 'bandit[toml]'" >> "$REPORT_FILE"
fi

# ─────────────────────────────────────────────────────────────
# 2. PIP-AUDIT — Dependency Vulnerability Check
# ─────────────────────────────────────────────────────────────
header "2. PIP-AUDIT — Dependency Vulnerability Check"

if command -v pip-audit &> /dev/null; then
    echo "Running pip-audit ..."
    AUDIT_OUTPUT=$(pip-audit --format=json 2>/dev/null || true)
    echo "$AUDIT_OUTPUT" > "$REPORT_DIR/pip-audit-$TIMESTAMP.json"

    AUDIT_COUNT=$(echo "$AUDIT_OUTPUT" | python -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(len(data.get('dependencies', [])))
except:
    print('0')
" 2>/dev/null || echo "0")

    if [ "$AUDIT_COUNT" = "0" ]; then
        pass "No known vulnerabilities in installed dependencies"
    else
        fail "pip-audit found vulnerable dependencies — see pip-audit-$TIMESTAMP.json"
    fi
else
    warn "pip-audit not installed — install with: pip install pip-audit"
    echo "  Install: pip install pip-audit" >> "$REPORT_FILE"
fi

# ─────────────────────────────────────────────────────────────
# 3. SAFETY — Dependency Vulnerability Check (alternative)
# ─────────────────────────────────────────────────────────────
header "3. SAFETY — Dependency Vulnerability Check"

if command -v safety &> /dev/null; then
    echo "Running safety check ..."
    SAFETY_OUTPUT=$(safety check --output json 2>/dev/null || true)
    echo "$SAFETY_OUTPUT" > "$REPORT_DIR/safety-$TIMESTAMP.json"

    if echo "$SAFETY_OUTPUT" | grep -q '"vulnerabilities":\[\]' 2>/dev/null; then
        pass "No known vulnerabilities found by safety"
    else
        warn "Safety check completed — review safety-$TIMESTAMP.json"
    fi
else
    warn "safety not installed — install with: pip install safety"
    echo "  Install: pip install safety" >> "$REPORT_FILE"
fi

# ─────────────────────────────────────────────────────────────
# 4. HARDCODED SECRETS SCAN
# ─────────────────────────────────────────────────────────────
header "4. Hardcoded Secrets Scan"

SECRETS_PATTERNS=(
    '(?i)(password|passwd|pwd)\s*[=:]\s*["\x27][^"\x27]{4,}'
    '(?i)(secret|api[_-]?key|apikey)\s*[=:]\s*["\x27][^"\x27]{8,}'
    '(?i)(access[_-]?token|auth[_-]?token)\s*[=:]\s*["\x27][A-Za-z0-9+/=_-]{20,}'
    '(?i)(private[_-]?key)\s*[=:]\s*["\x27]'
    '-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----'
    '(?i)ghp_[A-Za-z0-9]{36}'
    '(?i)sk-[A-Za-z0-9]{32,}'
    '(?i)AKIA[0-9A-Z]{16}'
)

SECRETS_FOUND=0

for pattern in "${SECRETS_PATTERNS[@]}"; do
    MATCHES=$(grep -rniE "$pattern" "$PROJECT_ROOT/backend" \
        --include="*.py" \
        --include="*.toml" \
        --include="*.yaml" \
        --include="*.yml" \
        --include="*.json" \
        --include="*.env" \
        2>/dev/null || true)

    if [ -n "$MATCHES" ]; then
        SECRETS_FOUND=$((SECRETS_FOUND + 1))
        echo "$MATCHES" >> "$REPORT_DIR/secrets-$TIMESTAMP.txt"
        while IFS= read -r line; do
            fail "Potential secret: $line"
        done <<< "$MATCHES"
    fi
done

if [ "$SECRETS_FOUND" = "0" ]; then
    pass "No hardcoded secrets detected"
fi

# Check for .env files that shouldn't be committed
ENV_FILES=$(find "$PROJECT_ROOT" -name ".env" -o -name ".env.*" 2>/dev/null || true)
if [ -n "$ENV_FILES" ]; then
    fail "Found .env file(s) that may contain secrets:"
    echo "$ENV_FILES" | while IFS= read -r f; do
        echo "    $f"
        echo "  FILE: $f" >> "$REPORT_FILE"
    done
else
    pass "No .env files found in project"
fi

# ─────────────────────────────────────────────────────────────
# 5. SQL INJECTION CHECK
# ─────────────────────────────────────────────────────────────
header "5. SQL Injection Surface Scan"

SQL_PATTERNS=(
    'execute\s*\(\s*f["\x27]'
    'execute\s*\(\s*["\x27].*%s'
    '\.format\s*\(.*\).*execute'
    'raw\s*\(\s*f["\x27]'
    'text\s*\(\s*f["\x27]'
)

SQL_FOUND=0

for pattern in "${SQL_PATTERNS[@]}"; do
    MATCHES=$(grep -rniE "$pattern" "$PROJECT_ROOT/backend" \
        --include="*.py" 2>/dev/null || true)
    if [ -n "$MATCHES" ]; then
        SQL_FOUND=$((SQL_FOUND + 1))
        while IFS= read -r line; do
            fail "Potential SQL injection: $line"
        done <<< "$MATCHES"
    fi
done

if [ "$SQL_FOUND" = "0" ]; then
    pass "No SQL injection patterns detected"
fi

# ─────────────────────────────────────────────────────────────
# 6. DANGEROUS FUNCTION CALLS
# ─────────────────────────────────────────────────────────────
header "6. Dangerous Function Call Scan"

DANGEROUS_PATTERNS=(
    '\beval\s*\('
    '\bexec\s*\('
    '\bos\.system\s*\('
    '\bos\.popen\s*\('
    '\bsubprocess\.call\s*\('
    '\bsubprocess\.Popen\s*\('
    '\b__import__\s*\('
    '\bpickle\.loads?\s*\('
    '\byaml\.load\s*\('
)

DANGEROUS_FOUND=0

for pattern in "${DANGEROUS_PATTERNS[@]}"; do
    MATCHES=$(grep -rniE "$pattern" "$PROJECT_ROOT/backend" \
        --include="*.py" 2>/dev/null || true)
    if [ -n "$MATCHES" ]; then
        DANGEROUS_FOUND=$((DANGEROUS_FOUND + 1))
        while IFS= read -r line; do
            warn "Potentially dangerous call: $line"
        done <<< "$MATCHES"
    fi
done

if [ "$DANGEROUS_FOUND" = "0" ]; then
    pass "No dangerous function calls detected"
fi

# ─────────────────────────────────────────────────────────────
# 7. STRUCTURED LOGGING CHECK
# ─────────────────────────────────────────────────────────────
header "7. Logging Hygiene Check"

PRINT_COUNT=$(grep -rn "print(" "$PROJECT_ROOT/backend" --include="*.py" 2>/dev/null | \
    grep -v "__pycache__" | wc -l || echo "0")

if [ "$PRINT_COUNT" -gt 0 ]; then
    warn "Found $PRINT_COUNT print() statements — prefer structlog"
    grep -rn "print(" "$PROJECT_ROOT/backend" --include="*.py" 2>/dev/null | \
        grep -v "__pycache__" | while IFS= read -r line; do
        echo "    $line"
    done
else
    pass "No print() statements — using structured logging"
fi

# ─────────────────────────────────────────────────────────────
# 8. FILE PERMISSION CHECK
# ─────────────────────────────────────────────────────────────
header "8. Sensitive File Permissions"

SENSITIVE_FILES=$(find "$PROJECT_ROOT" \( -name "*.pem" -o -name "*.key" -o -name "*.p12" -o -name "*.pfx" \) 2>/dev/null || true)

if [ -n "$SENSITIVE_FILES" ]; then
    fail "Found sensitive key files in project root"
else
    pass "No sensitive key files found"
fi

# ─────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  SCAN COMPLETE${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"

echo "" >> "$REPORT_FILE"
echo "=== SCAN SUMMARY ===" >> "$REPORT_FILE"
echo "Timestamp: $(date -Iseconds)" >> "$REPORT_FILE"
echo "Issues found: $ISSUES_FOUND" >> "$REPORT_FILE"

if [ "$ISSUES_FOUND" -gt 0 ]; then
    echo -e "  ${RED}Issues found: $ISSUES_FOUND${NC}"
    echo -e "  Report saved to: $REPORT_FILE"
    exit 1
else
    echo -e "  ${GREEN}No issues found — codebase looks clean!${NC}"
    echo -e "  Report saved to: $REPORT_FILE"
    exit 0
fi