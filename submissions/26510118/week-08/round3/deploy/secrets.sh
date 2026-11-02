#!/usr/bin/env bash
# Run on the Azure VM (DEPLOY.md step 2), by the user: asks for the OpenAI API key and the market's
# claim code without showing them, and writes ~/market/openai.env and ~/market/owner.env, readable
# by this user only. Press Enter on a question to keep the value already saved. Prints no secret.
set -euo pipefail
M=/home/azureuser/market
umask 077
read -r -s -p "OpenAI API key (hidden; Enter keeps the saved one): " K; echo
if [ -n "$K" ]; then printf 'OPENAI_API_KEY=%s\n' "$K" > "$M/openai.env"; echo "saved openai.env"; fi
unset K
read -r -s -p "Market claim code (hidden; Enter keeps the saved one): " C; echo
if [ -n "$C" ]; then printf 'MARKET_OWNER=%s\n' "$C" > "$M/owner.env"; echo "saved owner.env"; fi
unset C
for f in openai.env owner.env; do
  if [ -s "$M/$f" ]; then echo "$f: present, $(stat -c %A "$M/$f")"; else echo "$f: MISSING"; fi
done
