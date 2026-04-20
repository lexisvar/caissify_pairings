#!/usr/bin/env bash
# Build and upload caissify-pairings to PyPI.
#
# Usage:
#   scripts/release.sh            # upload to PyPI (production)
#   scripts/release.sh --test     # upload to TestPyPI
#   scripts/release.sh --dry-run  # build + twine check only, no upload
#
# Requires a `.env` file in the repo root containing:
#   PYPI_API_TOKEN=pypi-...
#   TESTPYPI_API_TOKEN=pypi-...   (optional, only needed for --test)
#
# `.env` is git-ignored. Never commit it. Rotate the token regularly.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- parse flags -------------------------------------------------------
TARGET="pypi"
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --test)    TARGET="testpypi" ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

# --- load .env ---------------------------------------------------------
if [[ ! -f .env ]]; then
    echo "ERROR: .env not found in $REPO_ROOT" >&2
    echo "       Copy .env.example to .env and fill in your PyPI token." >&2
    exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

# --- pick token --------------------------------------------------------
if [[ "$TARGET" == "testpypi" ]]; then
    TOKEN="${TESTPYPI_API_TOKEN:-}"
    REPO_URL="https://test.pypi.org/legacy/"
    REPO_LABEL="TestPyPI"
    INSTALL_INDEX="https://test.pypi.org/simple/"
else
    TOKEN="${PYPI_API_TOKEN:-}"
    REPO_URL="https://upload.pypi.org/legacy/"
    REPO_LABEL="PyPI"
    INSTALL_INDEX="https://pypi.org/simple/"
fi

if [[ $DRY_RUN -eq 0 && ( -z "$TOKEN" || "$TOKEN" == "pypi-REPLACE_ME" ) ]]; then
    echo "ERROR: no valid token found for $REPO_LABEL in .env" >&2
    echo "       Set PYPI_API_TOKEN (or TESTPYPI_API_TOKEN for --test)." >&2
    exit 1
fi

# --- read version from pyproject.toml ---------------------------------
VERSION="$(
    python3 -c "
import re, pathlib
text = pathlib.Path('pyproject.toml').read_text()
m = re.search(r'^version\s*=\s*\"([^\"]+)\"', text, re.M)
print(m.group(1) if m else '')
"
)"
if [[ -z "$VERSION" ]]; then
    echo "ERROR: could not read version from pyproject.toml" >&2
    exit 1
fi
echo "==> Releasing caissify-pairings v$VERSION to $REPO_LABEL"

# --- sanity checks -----------------------------------------------------
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "WARNING: working tree has uncommitted changes." >&2
    read -r -p "Continue anyway? [y/N] " reply
    [[ "$reply" == "y" || "$reply" == "Y" ]] || exit 1
fi

if [[ $DRY_RUN -eq 0 && "$TARGET" == "pypi" ]]; then
    if ! git rev-parse "v$VERSION" >/dev/null 2>&1; then
        echo "WARNING: no local tag 'v$VERSION' found." >&2
        read -r -p "Continue without a tag? [y/N] " reply
        [[ "$reply" == "y" || "$reply" == "Y" ]] || exit 1
    fi
fi

# --- isolated build venv -----------------------------------------------
BUILD_VENV="$(mktemp -d -t caissify-build.XXXXXX)/venv"
trap 'rm -rf "$(dirname "$BUILD_VENV")"' EXIT

echo "==> Creating isolated build venv at $BUILD_VENV"
python3 -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/pip" install --quiet --upgrade pip build twine

# --- build + check -----------------------------------------------------
echo "==> Cleaning dist/"
rm -rf dist/ build/

echo "==> Building sdist + wheel"
"$BUILD_VENV/bin/python" -m build

echo "==> twine check"
"$BUILD_VENV/bin/twine" check dist/*

if [[ $DRY_RUN -eq 1 ]]; then
    echo
    echo "Dry run OK. Artifacts in dist/:"
    ls -la dist/
    exit 0
fi

# --- upload ------------------------------------------------------------
echo "==> Uploading to $REPO_LABEL"
TWINE_USERNAME="__token__" \
TWINE_PASSWORD="$TOKEN" \
    "$BUILD_VENV/bin/twine" upload --repository-url "$REPO_URL" dist/*

# --- verify ------------------------------------------------------------
echo "==> Verifying install from $REPO_LABEL (in a throwaway venv)"
VERIFY_VENV="$(mktemp -d -t caissify-verify.XXXXXX)/venv"
python3 -m venv "$VERIFY_VENV"
# Retry a few times — PyPI's CDN can lag by 30-60 seconds after upload.
for attempt in 1 2 3 4 5 6; do
    if "$VERIFY_VENV/bin/pip" install --quiet \
        --index-url "$INSTALL_INDEX" \
        --extra-index-url "https://pypi.org/simple/" \
        "caissify-pairings==$VERSION" 2>/dev/null; then
        break
    fi
    echo "   ...not yet available, retrying in 10s (attempt $attempt/6)"
    sleep 10
done

"$VERIFY_VENV/bin/python" - <<PYEOF
from caissify_pairings import generate_pairings
from caissify_pairings.engines import available_systems
print("   Engines available:", available_systems())
print("   Install verified OK.")
PYEOF
rm -rf "$(dirname "$VERIFY_VENV")"

echo
echo "==> Released caissify-pairings==$VERSION to $REPO_LABEL"
echo "    https://pypi.org/project/caissify-pairings/$VERSION/"
