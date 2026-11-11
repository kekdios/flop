#!/bin/bash
# Daily claim of the 25 FLOP anonymous grant for our kibble bridge address.
# Cron on the droplet (daily). The bridge address rotates, so resolve it
# first via the flop-devnet bridge, then POST the public faucet.
# Log: /var/log/flop-faucet.log. Config: /etc/flop-ops.env (KIBBLE_SEED).
set -u
. /etc/flop-ops.env 2>/dev/null || true
LOG=/var/log/flop-faucet.log
ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

if [ -z "${KIBBLE_SEED:-}" ]; then
  echo "$(ts) no KIBBLE_SEED in /etc/flop-ops.env" >> "$LOG"; exit 1
fi

addr=$(curl -s -m 30 -X POST https://kibble.world/api/flop-devnet \
  -H 'Content-Type: application/json' \
  -d "{\"action\":\"address\",\"seed_hex\":\"$KIBBLE_SEED\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("address",""))' 2>/dev/null)

if [ -z "$addr" ]; then
  echo "$(ts) could not resolve bridge address" >> "$LOG"; exit 1
fi

resp=$(curl -s -m 30 -X POST https://faucet.flop.network/fund \
  -H 'Content-Type: application/json' -d "{\"address\":\"$addr\"}")

# Cooldown race: cron fires at a fixed time but the 24h window slides —
# retry once after the faucet's Retry-After hint.
if ! echo "$resp" | grep -q '"success": *true'; then
  wait=$(echo "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("retry_after",0))' 2>/dev/null || echo 0)
  if [ "${wait:-0}" -gt 0 ] && [ "$wait" -lt 900 ]; then
    sleep "$wait"
    resp=$(curl -s -m 30 -X POST https://faucet.flop.network/fund \
      -H 'Content-Type: application/json' -d "{\"address\":\"$addr\"}")
  fi
fi

echo "$(ts) $addr -> $resp" >> "$LOG"
