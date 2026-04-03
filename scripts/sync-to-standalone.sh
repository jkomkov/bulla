#!/bin/bash
set -euo pipefail

MONO="/Users/jkomkov/Documents/GitHub/res-agentica/bulla"
STANDALONE="/Users/jkomkov/Documents/GitHub/bulla"

RSYNC_EXCLUDES=(
  --exclude='.git'
  --exclude='__pycache__'
  --exclude='*.egg-info'
  --exclude='dist'
  --exclude='.DS_Store'
  --exclude='.pytest_cache'
  --exclude='*.pyc'
)

echo "=== Pre-flight ==="
if git -C "$STANDALONE" status --porcelain | grep -q .; then
  echo "ERROR: standalone repo is dirty. Commit or stash first."
  exit 1
fi
BRANCH=$(git -C "$STANDALONE" branch --show-current)
if [ "$BRANCH" != "main" ]; then
  echo "ERROR: standalone is on branch '$BRANCH', expected 'main'."
  exit 1
fi
git -C "$STANDALONE" fetch origin
echo "Standalone: clean, on main, fetched."

VERSION=$(python3 -c "
import re, pathlib
text = pathlib.Path('$MONO/src/bulla/__init__.py').read_text()
print(re.search(r'__version__\s*=\s*\"(.+?)\"', text).group(1))
")
echo "Monorepo version: $VERSION"

echo ""
echo "=== Dry-run (checking for deletions) ==="
DELETIONS=$(rsync -avn --delete "${RSYNC_EXCLUDES[@]}" "$MONO/" "$STANDALONE/" 2>&1 | grep "^deleting" || true)

if [ -n "$DELETIONS" ]; then
  echo "WARNING: rsync would delete these files:"
  echo "$DELETIONS"
  read -p "Continue? [y/N] " -n 1 -r
  echo
  [[ $REPLY =~ ^[Yy]$ ]] || exit 1
else
  echo "No deletions. Safe to proceed."
fi

echo ""
echo "=== Syncing ==="
rsync -av --delete "${RSYNC_EXCLUDES[@]}" "$MONO/" "$STANDALONE/"

SYNCED_VERSION=$(python3 -c "
import re, pathlib
text = pathlib.Path('$STANDALONE/src/bulla/__init__.py').read_text()
print(re.search(r'__version__\s*=\s*\"(.+?)\"', text).group(1))
")
echo "Synced version: $SYNCED_VERSION"

echo ""
echo "=== Installing and testing ==="
cd "$STANDALONE"
pip install -e . -q
python -m pytest tests/ -v

echo ""
echo "=== Summary ==="
echo "Sync complete. Version: $SYNCED_VERSION"
echo ""
echo "Next steps (manual):"
echo "  cd $STANDALONE"
echo "  git add -A && git diff --cached --stat"
echo "  git commit -m 'Sync with res-agentica monorepo: v$SYNCED_VERSION'"
echo "  git push origin main"
echo "  git tag -a v$SYNCED_VERSION -m 'v$SYNCED_VERSION'"
echo "  git push origin v$SYNCED_VERSION"
echo "  rm -rf dist/ && python -m build && twine check dist/* && twine upload dist/*"
