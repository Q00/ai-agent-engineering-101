#!/usr/bin/env bash
# Run on the Azure VM (DEPLOY.md step 2), by the user: asks for the OpenAI API key and the market's
# claim code without showing them, and writes ~/market/openai.env and ~/market/owner.env, readable
# by this user only. Press Enter on a question to keep the value already saved. Prints no secret.
#
# A paste through the Windows terminal and ssh can lose its first character (seen 11/2: the key
# was saved as "k-proj-..."). So the key gets its "s" back when that is all it lacks, and is saved
# only after OpenAI accepts it (one call to /v1/models, the key sent as a header on stdin, not on
# the command line). The claim code cannot be checked here, so the user compares its length and
# first and last two characters with what was copied.
set -uo pipefail
M=/home/azureuser/market
umask 077

while true; do
  read -r -s -p "OpenAI API key (hidden; Enter keeps the saved one): " K; echo
  [ -z "$K" ] && { echo "kept the saved key"; break; }
  case "$K" in k-proj-*|k-svcacct-*|k-admin-*) K="s$K"; echo "(the paste lost the first letter s; put it back)";; esac
  case "$K" in sk-*) ;; *) echo "That does not look like an OpenAI key (it starts with sk-). Paste it again."; continue;; esac
  code=$(printf 'Authorization: Bearer %s\n' "$K" | curl -s -o /dev/null -w '%{http_code}' -H @- https://api.openai.com/v1/models)
  if [ "$code" = "200" ]; then
    printf 'OPENAI_API_KEY=%s\n' "$K" > "$M/openai.env"
    echo "OpenAI accepted the key: saved openai.env"
    break
  fi
  echo "OpenAI refused this key (HTTP $code). Paste it again, or press Enter to keep the saved one."
done
unset K

while true; do
  read -r -s -p "Market claim code (hidden; Enter keeps the saved one): " C; echo
  [ -z "$C" ] && { echo "kept the saved claim code"; break; }
  echo "You pasted ${#C} characters, starting '${C:0:2}' and ending '${C: -2}'."
  read -r -p "Is that the code you copied? (y = save, n = paste again): " ok
  if [ "$ok" = "y" ]; then printf 'MARKET_OWNER=%s\n' "$C" > "$M/owner.env"; echo "saved owner.env"; break; fi
done
unset C

for f in openai.env owner.env; do
  if [ -s "$M/$f" ]; then echo "$f: present, $(stat -c %A "$M/$f")"; else echo "$f: MISSING"; fi
done
