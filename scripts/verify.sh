#!/usr/bin/env bash
# Run every check CI runs, in CI's order, before pushing.
#
# This exists because of nine consecutive red CI runs whose cause was not a bug in the code. The
# checks were being assembled by hand each time - pytest, ruff, mypy, the self-test - and the one
# that was never in the hand-assembled list was the one that failed. Locally green, remotely red,
# for nine commits, over a test fixture that looked like a credential.
#
# So the fix is not "remember the secret scan". It is to stop having two lists. This script is the
# single local entrypoint, and `test_ci_and_verify_run_the_same_checks` in tests/test_safety.py
# asserts that every command in ci.yml's `test` job appears here - so adding a step to CI without
# adding it here fails the suite, which is the only thing that keeps two lists in step.
#
# Usage: bash scripts/verify.sh
#
# Invoked through `bash` rather than `./` because this repository is pushed through GitHub's web
# upload form, which does not carry file modes - so the executable bit does not survive the trip
# and cannot be relied on in a fresh clone.
#
# Deliberately NOT what CI does in one respect: nothing here writes to the repository's data/.
# The self-test and the demo lifecycle both mutate operational state, and CI commits that state
# on purpose - but a local pre-push check is a diagnostic, and a diagnostic that mutates shared
# state is the defect this repository already fixed once (see worker_proof.LOCAL_PROOF_FILE).
#
# So data/ is COPIED to a temp directory and AICC_DATA_DIR points there. A copy rather than an
# empty directory, because the steps have to see what CI sees: the dashboard build reads real
# opportunities and jobs, and building against an empty store would pass while proving nothing.
# The copy is thrown away on exit, however the script ends.
set -u -o pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH=src

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT INT TERM
cp -R data "$scratch/data"
export AICC_DATA_DIR="$scratch/data"
export AICC_WORKSPACE_ROOT="$scratch/workspaces"

failed=()
step() {
  local name="$1"
  shift
  printf '\n\033[1m== %s\033[0m\n' "$name"
  if "$@"; then
    printf '   \033[32mPASS\033[0m %s\n' "$name"
  else
    printf '   \033[31mFAIL\033[0m %s\n' "$name"
    failed+=("$name")
  fi
}

step "Format check" ruff format --check src tests scripts
step "Lint" ruff check src tests scripts
step "Type check" mypy src
step "Tests" pytest -q
step "Workflow validation" python scripts/validate_workflows.py
step "Secret scan" python scripts/secret_scan.py

# CI treats pip-audit findings as a warning rather than a failure, and so does this.
printf '\n\033[1m== Dependency audit\033[0m\n'
pip-audit -r requirements-dev.txt || printf '   \033[33mWARN\033[0m pip-audit reported findings\n'

step "Assert the cost ceiling is still zero" python - <<'PY'
from aicc.config import MAX_NEW_MONTHLY_CASH_SPEND

assert MAX_NEW_MONTHLY_CASH_SPEND == 0.0, (
    f"MAX_NEW_MONTHLY_CASH_SPEND is {MAX_NEW_MONTHLY_CASH_SPEND}, not 0.00. "
    "Raising it is a deliberate decision that needs Andres's explicit approval."
)
print("Cost ceiling: $0.00")
PY

step "Safety self-test" python -m aicc selftest --ci
step "Demo lifecycle (spec section 52 acceptance test)" python -m aicc demo
step "Build and verify the dashboard" bash -c 'python -m aicc build --out site && python -m aicc verify-site --site site'

printf '\n'
if [ ${#failed[@]} -gt 0 ]; then
  printf '\033[31mVERIFY FAILED\033[0m - %d check(s): %s\n' "${#failed[@]}" "${failed[*]}"
  printf 'CI will be red. Fix these before pushing.\n'
  exit 1
fi

printf '\033[32mVERIFY CLEAN\033[0m - everything CI runs passed locally.\n'
printf 'A clean working tree is not a pushed commit: check `git diff --name-only origin/main`.\n'
