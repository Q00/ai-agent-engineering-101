#!/usr/bin/env bash
# Starts run_market.sh, run_meta.sh and keep_logs.sh again after the Mac mini restarts (launchd, one job each).
# Run it as the market account, from anywhere:
#
#   submissions/26622007/week-08/install_launchd.sh agent    LaunchAgents in ~/Library/LaunchAgents: start when the
#                                                            market account logs in (turn on its automatic login)
#   submissions/26622007/week-08/install_launchd.sh daemon   LaunchDaemons that start at boot without a login; writes
#                                                            the files and prints the sudo commands, runs none of them
#   submissions/26622007/week-08/install_launchd.sh status   what launchd has loaded
#   submissions/26622007/week-08/install_launchd.sh remove   unloads and deletes the LaunchAgents
#   submissions/26622007/week-08/install_launchd.sh plan DIR only writes the three plists to DIR (for checking)
#
# Copies already started by hand keep running: each script waits for the running copy (lib_single.sh) and takes
# over when it stops, so the market never sees two kits under one name. launchd restarts a job that exits.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
JOBS="market:run_market.sh meta:run_meta.sh keeplogs:keep_logs.sh"
PREFIX="kr.nocha12"
AGENTS="$HOME/Library/LaunchAgents"
PATH_FOR_JOBS="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

plist() {   # plist <job> <script> <user or empty>
  local user=""
  [ -n "$3" ] && user="    <key>UserName</key><string>$3</string>
"
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$PREFIX.$1</string>
${user}    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>$HERE/$2</string></array>
    <key>WorkingDirectory</key><string>$ROOT</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key><string>$HOME</string>
        <key>PATH</key><string>$PATH_FOR_JOBS</string>
        <key>LANG</key><string>en_US.UTF-8</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>60</integer>
    <key>StandardOutPath</key><string>$HERE/logs/launchd-$1.log</string>
    <key>StandardErrorPath</key><string>$HERE/logs/launchd-$1.log</string>
</dict>
</plist>
EOF
}

write_all() {   # write_all <dir> <user or empty>
  mkdir -p "$1" "$HERE/logs"
  for j in $JOBS; do
    plist "${j%%:*}" "${j#*:}" "$2" > "$1/$PREFIX.${j%%:*}.plist"
    plutil -lint "$1/$PREFIX.${j%%:*}.plist" > /dev/null
  done
}

case "${1:-}" in
  agent)
    write_all "$AGENTS" ""
    for j in $JOBS; do
      label="$PREFIX.${j%%:*}"
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
      echo "loaded $label (log: ${HERE#$ROOT/}/logs/launchd-${j%%:*}.log)"
    done
    echo "These start when $(id -un) logs in. For a restart without anyone at the keyboard, turn on automatic login"
    echo "for $(id -un) (System Settings > Users & Groups), or use '$0 daemon' instead."
    ;;
  daemon)
    out="$HERE/.market/launchd"
    write_all "$out" "$(id -un)"
    echo "Wrote $out. Run these yourself with an administrator account (this script runs no sudo):"
    for j in $JOBS; do
      label="$PREFIX.${j%%:*}"
      echo "  sudo cp '$out/$label.plist' /Library/LaunchDaemons/ && sudo chown root:wheel /Library/LaunchDaemons/$label.plist && sudo launchctl bootstrap system /Library/LaunchDaemons/$label.plist"
    done
    echo "Do not install both: use 'agent' or 'daemon'."
    ;;
  status)
    launchctl list | grep -F "$PREFIX." || echo "no $PREFIX jobs in this account's launchd (daemons: sudo launchctl list | grep $PREFIX)"
    ;;
  remove)
    for j in $JOBS; do
      label="$PREFIX.${j%%:*}"
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      rm -f "$AGENTS/$label.plist"
      echo "removed $label"
    done
    ;;
  plan)
    write_all "${2:?a folder}" "${3:-}"
    ls "$2"
    ;;
  *)
    sed -n '2,15p' "$0"; exit 1 ;;
esac
