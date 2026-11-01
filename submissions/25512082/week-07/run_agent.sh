#!/bin/sh
set -eu

# Use the lab environment when it is present locally. In a clean checkout,
# use the Python 3 environment in which the assignment dependencies were installed.
lab_python="../../../a2a-lab/.venv/bin/python"
if [ -x "$lab_python" ]; then
    exec "$lab_python" "$@"
fi
exec python3 "$@"
