#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ "$(uname -s)" = Darwin ]; then
    export LC_ALL=en_US.UTF-8
    export LANG=en_US.UTF-8
fi

if [ -x "$ROOT/.venv/bin/holderpro-gui" ]; then
    exec "$ROOT/.venv/bin/holderpro-gui" "$@"
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(not ((3, 11) <= sys.version_info < (3, 15)))' 2>/dev/null; then
        exec "$candidate" -m holderpro.ui "$@"
    fi
done

echo "Python 3.11 through 3.14 is required. Install 'holderpro[gui]' in .venv first." >&2
exit 1
