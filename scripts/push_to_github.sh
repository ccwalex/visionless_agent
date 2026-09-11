#!/usr/bin/env bash
# Run on your Mac after: gh auth login
# Creates github.com/<you>/visionless_agent and pushes this project.
set -euo pipefail

REPO_NAME="${1:-visionless_agent}"
GITHUB_USER="${2:-$(gh api user --jq .login)}"
DESCRIPTION="${3:-Chrome affordance inspect for visionless agents — no screenshots, CDP handover}"

if ! gh auth status >/dev/null 2>&1; then
  echo "Run: gh auth login"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if gh repo view "${GITHUB_USER}/${REPO_NAME}" >/dev/null 2>&1; then
  echo "Repo already exists: https://github.com/${GITHUB_USER}/${REPO_NAME}"
else
  gh repo create "${REPO_NAME}" \
    --public \
    --description "${DESCRIPTION}" \
    --source=. \
    --remote=github \
    --push
  echo "Created and pushed: https://github.com/${GITHUB_USER}/${REPO_NAME}"
  exit 0
fi

# Repo exists — add remote if missing and push
if git remote get-url github >/dev/null 2>&1; then
  :
else
  git remote add github "git@github.com:${GITHUB_USER}/${REPO_NAME}.git"
fi
git push -u github main
echo "Pushed: https://github.com/${GITHUB_USER}/${REPO_NAME}"
