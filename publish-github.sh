#!/usr/bin/env bash
set -euo pipefail

# Publish helper for the sanitized repo.
# Usage:
#   GITHUB_USER=yourname REPO_NAME=base-token-alert-bot ./publish-github.sh
# Optional:
#   GITHUB_TOKEN=ghp_xxx   # required only if creating the repo via GitHub API
#   VISIBILITY=public      # public (default) or private

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

GITHUB_USER="${GITHUB_USER:-}"
REPO_NAME="${REPO_NAME:-base-token-alert-bot}"
VISIBILITY="${VISIBILITY:-public}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

if [[ -z "$GITHUB_USER" ]]; then
  echo "Set GITHUB_USER first, e.g. GITHUB_USER=yourname $0" >&2
  exit 1
fi

REMOTE_URL="https://github.com/${GITHUB_USER}/${REPO_NAME}.git"

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

# Create the GitHub repo automatically if a token is available.
if [[ -n "$GITHUB_TOKEN" ]]; then
  PRIVATE_FLAG="false"
  if [[ "$VISIBILITY" == "private" ]]; then
    PRIVATE_FLAG="true"
  fi

  PRIVATE_FLAG="$PRIVATE_FLAG" python3 - <<'PY'
import json, os, sys, urllib.request, urllib.error
repo = os.environ["REPO_NAME"]
private = os.environ["PRIVATE_FLAG"] == "true"
token = os.environ["GITHUB_TOKEN"]
url = "https://api.github.com/user/repos"
payload = json.dumps({
    "name": repo,
    "private": private,
    "auto_init": False,
    "description": "Alert-only Base token screener (sanitized public release)"
}).encode()
req = urllib.request.Request(url, data=payload, method="POST")
req.add_header("Authorization", f"token {token}")
req.add_header("Accept", "application/vnd.github+json")
req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req) as resp:
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    body = e.read().decode(errors="replace")
    if e.code == 422 and "name already exists" in body.lower():
        print("Repository already exists, continuing...", file=sys.stderr)
    else:
        print(body, file=sys.stderr)
        raise
PY
fi

git branch -M main
git push -u origin main

echo "Done. Repo URL: ${REMOTE_URL}"
