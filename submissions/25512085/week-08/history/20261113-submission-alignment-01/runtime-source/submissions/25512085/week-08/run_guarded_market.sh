#!/usr/bin/env bash
# Current process only: retain the original activation, auth exports and loop.
# No auth values are printed, re-saved, or passed as command-line arguments.
BASE=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || exit 1
REPO=$(CDPATH= cd -- "$BASE/../../.." && pwd) || exit 1
PYTHON=${MARKET_PYTHON:-"$HOME/venv/bin/python"}
ORIGINAL="$HOME/run_kit.sh"
readonly BASE REPO PYTHON ORIGINAL
if ! bash -n "$ORIGINAL" >/dev/null 2>&1; then
    printf '%s\n' '[visibility-launcher] original_syntax_rejected' >&2
    exit 1
fi
if ! command "$PYTHON" "$BASE/guarded_market_kit.py" --validate-launcher "$ORIGINAL"; then
    printf '%s\n' '[visibility-launcher] original_structure_rejected' >&2
    exit 1
fi
python() {
    if [[ $# -gt 0 && ( "$1" == scripts/market_kit.py || "$1" == "$REPO/scripts/market_kit.py" ) ]]; then
        printf '%s\n' '[visibility-launcher] guarded_market_kit_intercept' >&2
        command "$PYTHON" "$BASE/guarded_market_kit.py" "${@:2}"
    else
        command "$PYTHON" "$@"
    fi
}
readonly -f python
printf '%s\n' '[visibility-launcher] validated_source_current_process' >&2
# Do not enable errexit: the original loop deliberately retries nonzero exits.
source "$ORIGINAL"
