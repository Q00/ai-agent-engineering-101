#!/usr/bin/env bash
# Writes .market/supabase.env (gitignored, chmod 600) for ops/supabase_sync.py. The password is read without echo,
# so it never lands in shell history or a chat transcript. Run on the server:  bash ops/set_supabase_password.sh
set -euo pipefail
W8="$(cd "$(dirname "$0")/.." && pwd)"
HOST="aws-0-ap-northeast-1.pooler.supabase.com"; REF="dmlyneezvnagwymutkwn"
read -rsp "Supabase DB password: " PW; echo
ENC="$(PW="$PW" python3 -c 'import os,urllib.parse;print(urllib.parse.quote(os.environ["PW"],safe=""))')"
umask 077
printf 'SUPABASE_DB_URL=postgresql://postgres.%s:%s@%s:6543/postgres\n' "$REF" "$ENC" "$HOST" > "$W8/.market/supabase.env"
chmod 600 "$W8/.market/supabase.env"
"$HOME/.venvs/week08-qa/bin/python" - "$W8/.market/supabase.env" <<'PY'
import sys, psycopg
url = open(sys.argv[1]).read().split("=", 1)[1].strip()
with psycopg.connect(url, connect_timeout=15, prepare_threshold=None) as c:
    print("connected:", c.execute("select current_user").fetchone()[0])
PY
