#!/usr/bin/env bash
# One-time push of this repository to GitHub.
#
# Why this script exists: the Claude session that built this repo is sandboxed away from
# github.com, and the safety classifier (correctly) refuses to let an assistant hold your
# access token. So the push runs here, in your own shell, with the token never leaving it.
#
# The token is read into a shell variable, used once, and never written to disk, never echoed,
# and never added to your git config or credential store.
#
# Usage:  bash scripts/push_to_github.sh

set -euo pipefail

REPO="amercado19/ai-income-control-center"
BRANCH="main"

cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
  echo "ERROR: not a git repository. Run this from inside ai-income-control-center." >&2
  exit 1
fi

echo "Repository : $(pwd)"
echo "Commits    : $(git rev-list --count HEAD)"
echo "Files      : $(git ls-files | wc -l | tr -d ' ')"
echo "Target     : https://github.com/${REPO}"
echo
echo "Paste the fine-grained token (github_pat_...). It will not be displayed or saved."
read -rsp "Token: " GH_TOKEN
echo
echo

if [ -z "${GH_TOKEN}" ]; then
  echo "ERROR: no token entered." >&2
  exit 1
fi

git branch -M "${BRANCH}"
git remote remove origin 2>/dev/null || true
# The token is passed only in this process's memory, via a credential helper that prints it
# once. It is never persisted to .git/config or to a credential store.
git remote add origin "https://github.com/${REPO}.git"

echo "Pushing ${BRANCH} with full history..."
if git -c credential.helper="!f() { echo username=x-access-token; echo password=${GH_TOKEN}; }; f" \
       push -u origin "${BRANCH}"; then
  echo
  echo "PUSHED. https://github.com/${REPO}"
  echo
  echo "Next, in the browser Claude already has open:"
  echo "  1. Settings -> Pages -> Source: GitHub Actions"
  echo "  2. Revoke the token at https://github.com/settings/personal-access-tokens"
  echo "     (it is scoped to this one repo and expires 18 Sep 2026 regardless)"
else
  echo
  echo "PUSH FAILED. Common causes:" >&2
  echo "  - token lacks Contents: read and write on ${REPO}" >&2
  echo "  - token lacks Workflows: read and write (needed for .github/workflows/)" >&2
  echo "  - token expired or was revoked" >&2
  exit 1
fi

unset GH_TOKEN
