#!/usr/bin/env bash
# Upload to PyPI only if ci/reports/<version>.md is ALL GREEN for the current commit, then tag the release.
set -eu
cd "$(dirname "$0")/.."
VER=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
R=ci/reports/$VER.md
[ -f "$R" ] || { echo "no report $R: run ci/check.sh"; exit 1; }
grep -q '\*\*ALL GREEN\*\*' "$R" || { echo "$R is not green"; exit 1; }
HEAD=$(git log -1 --format=%h)
grep -q "Commit: $HEAD" "$R" || { echo "$R was made for another commit (HEAD is $HEAD): re-run ci/check.sh"; exit 1; }
grep -q "^## $VER" CHANGELOG.md || { echo "CHANGELOG.md has no entry for $VER"; exit 1; }
.venv/bin/twine upload --non-interactive -u __token__ dist/lite_locator_mcp-$VER-py3-none-any.whl dist/lite_locator_mcp-$VER.tar.gz
git tag -a "v$VER" -m "lite-locator-mcp $VER (checks: $R)"
echo "published $VER and tagged v$VER"
