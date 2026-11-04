# v121b labpc rollback (prepared, NOT RUN). Does NOT reuse the r3d ROLL_ONLY path (CoS 결정 18:02).
# Route: ssh labpc "wsl bash -l -s" < call_v121_rollback.sh > rb_out.txt
# Steps: 1 stop runner -> 2 move rlpo_v121 aside (gates live there; no delete) -> 3 restore the pre_v121 tgz written by
#        call_v121_go.sh (sha checked, seller.py must be aa1d864a), new files moved aside -> 4 est=0 x3 + seller-only
#        restart (restart_seller_v121.sh) -> 5 summary. Kit, tmux, buyer, autostart, config files, state/ untouched.
set -u
W=/home/sungsoo/ai-agent-engineering-101/submissions/25520013/week-09
X="$W/.r3_v121"; cd "$W" || { echo "STOP no W"; exit 1; }
TS=$(date +%Y%m%dT%H%M%S)_$$; h(){ sha256sum "$1" 2>/dev/null | cut -d" " -f1; }
echo "CALL v121b_rollback START $(date "+%F %T %Z") epoch $(date +%s)"
E="$X/last_go_v121.env"
PRE_TGZ="${PRE_TGZ-$(grep "^PRE_TGZ=" "$E" 2>/dev/null | cut -d= -f2-)}"; PRE_SHA="${PRE_SHA-$(grep "^PRE_SHA=" "$E" 2>/dev/null | cut -d= -f2-)}"
NEW_LIST="${NEW_LIST-$(grep "^NEW_LIST=" "$E" 2>/dev/null | cut -d= -f2-)}"
case "$PRE_TGZ" in "$W"/.r3_rollback/pre_v121_*.tgz) ;; *) echo "STOP PRE_TGZ is not a call_v121_go.sh tgz: [$PRE_TGZ]"; exit 1;; esac
case "$PRE_TGZ" in *..*|*/.r3/*) echo "STOP PRE_TGZ path rejected"; exit 1;; esac
[ "$(h "$PRE_TGZ")" = "$PRE_SHA" ] && [ -n "$PRE_SHA" ] || { echo "STOP PRE_TGZ sha mismatch"; exit 1; }
[ "$(tar xzOf "$PRE_TGZ" hooks/seller.py 2>/dev/null | sha256sum | cut -d" " -f1)" = aa1d864a71c3108c57326facd55c809eb16fd358e693770f8291a594a1cde599 ] || { echo "STOP tgz seller.py != aa1d864a"; exit 1; }
tar tzf "$PRE_TGZ" | grep -qE "^(\./)?state(/|$)" && { echo "STOP tgz contains state/"; exit 1; }
[ -f "$X/restart_seller_v121.sh" ] && [ "$(h "$X/restart_seller_v121.sh")" = "d8ce4da9eb4176ff5a25ac257bb92a232480173b94c2b105d290e4faf43978e0" ] || { echo "STOP restart_seller_v121.sh missing / sha"; exit 1; }
echo "PRE tgz=$PRE_TGZ sha=$(echo $PRE_SHA | cut -c1-8) seller_in_tgz=aa1d864a"
# 1 runner
bash "$X/call_v121_stop_runner.sh" | sed "s/^/RUNNER /"; [ "${PIPESTATUS[0]}" = 0 ] || { echo "STOP runner still running; nothing restored"; exit 5; }
# 2 rlpo_v121 aside (config.py / l_lock.json / gate step backups stay inside it)
[ -e "$W/rlpo_v121" ] && mv -n "$W/rlpo_v121" "$W/rlpo_v121.off_$TS" && echo "ACT moved rlpo_v121 -> rlpo_v121.off_$TS"
# 3 files
tar xzf "$PRE_TGZ" -C "$W" --exclude=state || { echo "STOP untar"; exit 8; }
for f in $(cat "$NEW_LIST" 2>/dev/null); do [ -e "$W/$f" ] && mv -n "$W/$f" "$W/$f.off_v121_$TS" && echo "ACT moved $f aside"; done
FAIL=0
for e in 04997170ceb81d416cfc7a880ff730943634fa3a1d6d9fe177d3ea457895b840:llm/caps_v4.py 363f5e882fa83dcb997a2858f7f52d5229471a451f5ddc5c734e1461dab59e1f:llm/codex_client_v4.py 22132343994c55b48dc9f704b9c87d19b4d51ad8a2c18060766a3bd6402ad23b:llm/live_v120c.py absent:llm/ops_llm.py absent:pii_mask.py 1beadadd5b344c4c13aa6f757f0112badff202e80b4e576cbdf06d445503bc2c:llm/harness2_propose.py absent:memory/llm_step.py ee0d76e8a75046ff08d8ce26c638e8dce1fd4fc3a2faacf8894179fe549d6449:memory/run.py aa1d864a71c3108c57326facd55c809eb16fd358e693770f8291a594a1cde599:hooks/seller.py b17d22a5a8e475e5559c6c2eee47d5f037d12fbbb4bfe4455261e4e3a9e1af44:hooks/buyer_log.py absent:tools/runner_v121.py; do WANT=${e%%:*}; REL=${e#*:}; [ "$WANT" = absent ] && { [ -e "$W/$REL" ] && FAIL=1; continue; }; [ "$(h "$W/$REL")" = "$WANT" ] || { echo "BAD $REL"; FAIL=1; }; done
for c in "llm_config.json de7154cf" "llm_caps_config_v4.json e9274a30" "policy.json 3d7654e6"; do set -- $c; G=$(h "$1" | cut -c1-8); [ "$G" = "$2" ] || echo "WARN cfg $1 now $G (was $2 at go; rollback does not write config files)"; done
echo "FILES restored ok=$([ $FAIL = 0 ] && echo yes || echo NO) seller_py=$(h hooks/seller.py | cut -c1-8)"
[ "$FAIL" = 0 ] || { echo "STOP restored files differ from the pre-v121 shas; no restart"; exit 8; }
# 4 est=0 x3 + seller-only restart (activates the restored aa1d864a code)
PRE_TGZ="$PRE_TGZ" PRE_SHA="$PRE_SHA" MODE=apply bash "$X/restart_seller_v121.sh" > "$X/rollback_restart_$TS.txt" 2>&1; RC=$?
grep -E "^(INFO N1|INFO prekill|INFO restart_seconds|ACT|VERIFY|RESULT|WARN)" "$X/rollback_restart_$TS.txt" | sed "s/^/RST /"
[ "$RC" = 0 ] || echo "WARN restart rc=$RC (rc 4 = est not 0 for 600 s, nothing killed: files are restored on disk, rerun this script later or the kit respawn picks them up)"
# 5 summary
P=$(ss -ltnpH "sport = :52605" 2>/dev/null | grep -o "pid=[0-9]*" | head -1 | cut -d= -f2)
echo "SUM seller=$P etimes=$(ps -o etimes= -p ${P:-0} | tr -d " ") seller_py=$(h hooks/seller.py | cut -c1-8) runner=$(pgrep -f tools/runner_v121.py | wc -l) tmux=$(tmux has-session -t market 2>/dev/null && echo alive) restart_rc=$RC"
echo "CALL END $(date "+%F %T %Z")"
exit "$RC"
