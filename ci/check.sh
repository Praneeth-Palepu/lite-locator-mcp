#!/usr/bin/env bash
# Release checks for lite-locator-mcp. Every step must pass before ci/publish.sh will upload.
# Writes ci/reports/<version>.md (what ran, results, commit, model checksums).
#   ci/check.sh            all steps
#   QUICK=1 ci/check.sh    skip the slow ranking quality gate (for iteration; publish.sh refuses QUICK reports)
set -u
cd "$(dirname "$0")/.."
REPO=$PWD
VER=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
WORK=${ELEM_CI_DIR:-$HOME/.cache/elem_rank_ci}
REPORT=ci/reports/$VER.md
mkdir -p "$WORK" ci/reports
PY=${PYTHON:-python3}
FAIL=0
LOG=$WORK/log.txt; : > "$LOG"
rows=()

step() {  # step "name" command...
  local name=$1; shift
  local t0=$SECONDS out
  out=$("$@" 2>&1); local rc=$?
  printf '\n===== %s (rc=%s)\n%s\n' "$name" "$rc" "$out" >> "$LOG"
  local last; last=$(printf '%s' "$out" | grep -v '^\s*$' | tail -1 | cut -c1-160 | tr '|' '/')
  if [ $rc -eq 0 ]; then rows+=("| $name | ✅ pass | $((SECONDS - t0))s | $last |"); echo "✅ $name"
  else rows+=("| $name | ❌ FAIL | $((SECONDS - t0))s | $last |"); echo "❌ $name"; FAIL=1; fi
}

venv() {  # fresh venv at $1
  rm -rf "$1" && $PY -m venv "$1" && "$1/bin/pip" install -q --upgrade pip
}

# tools
[ -x "$WORK/tools/bin/ruff" ] || { venv "$WORK/tools"; "$WORK/tools/bin/pip" install -q ruff bandit pip-audit build twine; }
T=$WORK/tools/bin

step "git: working tree committed" bash -c 'test -z "$(git status --porcelain -- . ":!ci/reports")" && git log -1 --format="%h %s"'
step "lint (ruff)" $T/ruff check elem_rank lite_locator tests examples ci
step "security scan (bandit, medium+)" $T/bandit -q -r elem_rank lite_locator -ll -c pyproject.toml
step "secrets scan" bash -c '! git grep -nIE "pypi-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{30,}" -- . ":!ci/check.sh" && echo "no secrets in tracked files"'
step "build wheel + sdist" bash -c "rm -rf build dist/*$VER* && $T/python -m build -q . && ls dist/*$VER*"
WHEEL=$(ls dist/lite_locator_mcp-$VER-py3-none-any.whl 2>/dev/null)
step "package metadata (twine check)" $T/twine check dist/*$VER*
step "wheel contents (models, no data/secrets)" bash -c "unzip -l $WHEEL | grep -q models-lite/bi.onnx && unzip -l $WHEEL | grep -q models-lite/ce.onnx && ! unzip -l $WHEEL | grep -qE '\.env|\.key|data/|\.pt\$' && echo ok"

# 1) proxy environment: latest dependencies + the [mcp] extra (mcp 2.x)
venv "$WORK/main"; M=$WORK/main/bin
step "install wheel [mcp] in a fresh venv" bash -c "$M/pip install -q '$WHEEL[mcp]' pytest playwright langchain-core && $M/python -m playwright install chromium >/dev/null && $M/pip check"
step "dependency vulnerabilities, proxy env (pip-audit)" $T/pip-audit --path "$($M/python -c 'import site;print(site.getsitepackages()[0])')" --progress-spinner off
step "tests: unit + browser + proxy end to end (mcp 2.x)" bash -c "cd '$WORK' && $M/pytest -q -p no:cacheprovider --rootdir '$REPO/tests' '$REPO/tests'"

# 2) LangChain environment: the library next to the latest langchain-mcp-adapters (which needs mcp 1.x)
venv "$WORK/lc"; L=$WORK/lc/bin
step "install wheel next to latest langchain-mcp-adapters" bash -c "$L/pip install -q '$WHEEL' 'mcp<2' langchain-core langchain-mcp-adapters pytest playwright && $L/pip check"
step "dependency vulnerabilities, LangChain env (pip-audit)" $T/pip-audit --path "$($L/python -c 'import site;print(site.getsitepackages()[0])')" --progress-spinner off
step "tests: LangChain wrapper + real Playwright MCP" bash -c "cd '$WORK' && $L/pytest -q -p no:cacheprovider --rootdir '$REPO/tests' '$REPO/tests' "

# 3) compatibility: an office-like environment that must keep mcp 1.3 untouched
venv "$WORK/compat"; C=$WORK/compat/bin
step "compat: install next to mcp 1.3 (unchanged)" bash -c "$C/pip install -q 'mcp==1.3.0' 'pydantic<2.11' 'langchain-core<0.4' 'langchain-mcp-adapters==0.0.4' pytest playwright && $C/pip install -q '$WHEEL' && $C/pip show mcp | grep -q 'Version: 1.3.0' && $C/pip check && echo 'mcp 1.3.0 kept'"
step "compat: tests (mcp 1.3 + Playwright MCP 0.0.29)" bash -c "cd '$WORK' && TEST_PW_MCP=@playwright/mcp@0.0.29 $C/pytest -q -p no:cacheprovider --rootdir '$REPO/tests' '$REPO/tests' "

if [ "${QUICK:-0}" = "1" ]; then rows+=("| ranking quality gate | ⏭ skipped (QUICK) | | |"); FAIL=1
else step "ranking quality gate (hard held-out steps)" $M/python ci/quality_gate.py; fi

{
  echo "# Release checks: lite-locator-mcp $VER"
  echo
  echo "- Date: $(date '+%Y-%m-%d %H:%M %Z')"
  echo "- Commit: $(git log -1 --format='%h %s')"
  echo "- Python: $($PY --version 2>&1); platform: $(uname -sm)"
  echo "- Result: $([ $FAIL -eq 0 ] && echo '**ALL GREEN**' || echo '**NOT GREEN**: do not publish')"
  echo
  echo "| Check | Result | Time | Last line of output |"
  echo "|---|---|---|---|"
  printf '%s\n' "${rows[@]}"
  echo
  echo "## Model files (sha256)"
  (cd elem_rank/models-lite && shasum -a 256 *.onnx *.npz lite.json | sed 's/^/    /')
  echo
  echo "Full output: kept locally (ci/reports/$VER.log, not published)."
} > "$REPORT"
cp "$LOG" "ci/reports/$VER.log"
echo; echo "report: $REPORT"; [ $FAIL -eq 0 ] && echo "ALL GREEN" || echo "NOT GREEN"
exit $FAIL
