# Sourced by run_market.sh, run_meta.sh and keep_logs.sh: one copy of each runs at a time.
#
# launchd starts them at boot and a person may start them by hand; two run_market.sh would put two kits in the
# market under one name (week 7: one_result_per_order). wait_turn waits while the copy named in the pid file is
# alive (its command must name the script, so a reused pid does not count), then records this copy.

wait_turn() {
  local pidfile="$1" name="$2" old waited=""
  while old="$(cat "$pidfile" 2>/dev/null)" && [ -n "$old" ] && [ "$old" != "$$" ] \
        && ps -p "$old" -o command= 2>/dev/null | grep -q "$name"; do
    [ -z "$waited" ] && echo "$(date '+%m-%d %H:%M:%S') [$name] another copy runs as pid $old; waiting for it to stop" >&2
    waited=1
    sleep "${WAIT_TURN_EVERY:-30}"
  done
  [ -n "$waited" ] && echo "$(date '+%m-%d %H:%M:%S') [$name] pid $old stopped; taking over as $$" >&2
  echo $$ > "$pidfile"
}
