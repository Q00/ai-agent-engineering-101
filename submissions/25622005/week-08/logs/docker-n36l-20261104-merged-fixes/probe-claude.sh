#!/bin/sh
# Probe named claude: as-agent picks its cwd by basename; print where it landed, then run the real claude.
echo "probe: pwd=$(pwd) HOME=$HOME user=$(id -un)"
exec /usr/local/bin/claude "$@"
