#!/usr/bin/env bash
# One-time push of this repository to GitHub.
#
# Why this script exists and why you are running it rather than Claude: pushing needs a
# credential, and an assistant should not hold yours. Claude's bridge into this machine runs in
# a Linux VM that can reach github.com but has no keychain, and macOS only grants assistants
# click-level access to Terminal - no typing. So this one command is yours to run.
#
# It tries your existing credentials FIRST. You already push nfl-pipeline, mlb-pipeline and
# mlb-dashboard from this machine over HTTPS, so macOS almost certainly has a GitHub credential
# in the keychain and no token will be needed at all. A token is asked for only if that fails,
# and it is then held in one shell variable, used once, never displayed, never written to disk,
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

git branch -M "${BRANCH}"
if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "https://github.com/${REPO}.git"
fi

finish() {
  echo
  echo "PUSHED. https://github.com/${REPO}"
  echo
  echo "Tell Claude it is pushed - it will configure Pages and verify the deployment."
  exit 0
}

echo "Trying your existing GitHub credentials..."
if git push -u origin "${BRANCH}"; then
  finish
fi

echo
echo "Existing credentials did not work, so a token is needed after all."
echo "Create a fine-grained token at https://github.com/settings/personal-access-tokens"
echo "scoped to ${REPO} only, with:"
echo "    Contents  : Read and write"
echo "    Workflows : Read and write   (this repo ships .github/workflows/)"
echo
echo "Paste it below. It will not be displayed or saved."
read -rsp "Token: " GH_TOKEN
echo
echo

if [ -z "${GH_TOKEN}" ]; then
  echo "ERROR: no token entered." >&2
  exit 1
fi

# The token is passed only in this process's memory, via a credential helper that prints it
# once. It is never persisted to .git/config or to a credential store.
if git -c credential.helper="!f() { echo username=x-access-token; echo password=${GH_TOKEN}; }; f" \
       push -u origin "${BRANCH}"; then
  unset GH_TOKEN
  echo
  echo "Revoke the token now at https://github.com/settings/personal-access-tokens -"
  echo "it has done its job and this repo does not need it again."
  finish
fi

unset GH_TOKEN
echo
echo "PUSH FAILED. Common causes:" >&2
echo "  - token lacks Contents: read and write on ${REPO}" >&2
echo "  - token lacks Workflows: read and write (needed for .github/workflows/)" >&2
echo "  - token expired or was revoked" >&2
exit 1
