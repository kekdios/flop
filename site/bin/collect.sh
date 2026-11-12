#!/bin/bash
# Collect miner status from the Vast VM and write status.json for the ops page.
# Config in /etc/flop-ops.env: VM_SSH_HOST VM_SSH_PORT VM_SSH_KEY ENDPOINT
#   SPEND_SINCE (ISO time the GPU started billing) + SPEND_RATE_HR (usd/hr) +
#   SPEND_BASE (any earlier spend) auto-accrue the spend card; if unset,
#   static SPEND_SO_FAR is used instead.
set -u
. /etc/flop-ops.env 2>/dev/null || true
SITE=/var/www/agentchainlab
OUT="$SITE/status.json.tmp"
now=$(date -u +'%Y-%m-%d %H:%M UTC')
SSH="ssh -i ${VM_SSH_KEY:-/root/.ssh/flop_vast} -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new -p ${VM_SSH_PORT:-22} root@${VM_SSH_HOST:-127.0.0.1}"

miner="provisioning"; miner_state="warn"
gpu="—"; gpu_state=""
model="—"; model_state=""
cal="not started"; cal_state=""
sess="—"; sess_state=""
ep="${ENDPOINT:-pending}"; ep_state=""
note=""
# Kibble score comes from the score-collector snapshot (no slow API call here).
kib=$(python3 -c "import json;print(json.load(open('/var/lib/flop-ops/kibble-score.json')).get('data',{}).get('score','—'))" 2>/dev/null || echo "—"); kib_state=""

if REMOTE=$($SSH 'bash -s' <<'EOS' 2>/dev/null
gpu=$(nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null | head -1)
dock=$(docker ps --format '{{.Names}}:{{.Status}}' 2>/dev/null | tr '\n' ';')
tok=$(grep -m1 '^SESSION_AUTH_TOKEN=' /opt/flop-miner/miner.env 2>/dev/null | cut -d= -f2)
info=""; for i in 1 2 3; do
  info=$(curl -sf --max-time 8 ${tok:+-H "Authorization: Bearer $tok"} http://127.0.0.1:8645/session/info 2>/dev/null) && break
  info=""; sleep 2
done
disk=$(df -h / | awk 'NR==2{print $4" free"}')
model=$(basename "$(grep -m1 '^MODEL_NAME=' /opt/flop-miner/miner.env 2>/dev/null | cut -d= -f2-)" 2>/dev/null)
wimg=$(grep -m1 '^FLOP_WORKER_IMAGE=' /opt/flop-miner/miner.env 2>/dev/null | cut -d= -f2-)
echo "GPU=$gpu"
echo "DOCKER=$dock"
echo "DISK=$disk"
echo "MODEL=$model"
echo "WORKER_IMG=$wimg"
echo "SESSION_INFO<<$info>>"
EOS
); then
  gpu=$(echo "$REMOTE" | sed -n 's/^GPU=//p'); [ -n "$gpu" ] && gpu_state="ok" || gpu_state="bad"
  miner=$(echo "$REMOTE" | sed -n 's/^DOCKER=//p')
  if [ -n "$miner" ]; then miner_state="ok"; else miner="docker down"; miner_state="bad"; fi
  info=$(echo "$REMOTE" | sed -n 's/^SESSION_INFO<<//;s/>>$//p')
  if echo "$info" | grep -q '"ready"[ :]*true'; then
    cap=$(echo "$info" | grep -oE '"capGflopsPerSec"[ :]*"?[0-9]+' | grep -oE '[0-9]+$' | head -1)
    cal="active (cap ${cap:-?} GFLOP/s)"; cal_state="ok"
  elif echo "$info" | grep -q calibration; then cal="in progress"; cal_state="warn";
  elif [ -n "$info" ]; then cal="not calibrated"; cal_state="";
  else cal="__KEEP__"; fi
  # Endpoint fetch failed → keep last known calibration instead of flapping.
  if [ "$cal" = "__KEEP__" ]; then
    cal=$(cut -d'|' -f1 /var/lib/flop-ops/last-cal 2>/dev/null)
    cal_state=$(cut -d'|' -f2 /var/lib/flop-ops/last-cal 2>/dev/null)
    [ -z "$cal" ] && { cal="unreachable"; cal_state="warn"; }
  else
    mkdir -p /var/lib/flop-ops; echo "$cal|$cal_state" > /var/lib/flop-ops/last-cal
  fi
  sess=$(echo "$info" | grep -oE '"sessions"[ :]*[0-9]+' | grep -oE '[0-9]+' | head -1); sess=${sess:-0}
  note=$(echo "$REMOTE" | sed -n 's/^DISK=//p')
  model=$(echo "$REMOTE" | sed -n 's/^MODEL=//p'); [ -n "$model" ] && model_state="ok" || model="—"
else
  miner="unreachable"; miner_state="bad"; ep_state="bad"; note="ssh to VM failed"
fi

# Worker image drift: compare pinned digest on the box against releases.json.
# Stale worker images get fenced by runtime upgrades — alert via the sig path.
# Also track cli.latest: a new CLI build may support the current runtime spec
# (unblocks self-custodial spend + declare-gpu) — sig change emails on bump.
drift=$(cat /var/lib/flop-ops/last-drift 2>/dev/null || echo "?")
cliver=$(cat /var/lib/flop-ops/last-cliver 2>/dev/null || echo "?")
wimg=$(echo "${REMOTE:-}" | sed -n 's/^WORKER_IMG=//p')
rel=$(curl -fsm10 https://flop.network/releases.json 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("miner-worker",{}).get("image","")+"\t"+d.get("cli",{}).get("latest",""))' 2>/dev/null || true)
rel_img=$(echo "$rel" | cut -f1); cli_new=$(echo "$rel" | cut -f2)
if [ -n "$rel_img" ] && [ -n "$wimg" ]; then
  if [ "$wimg" = "$rel_img" ]; then drift="current"; else drift="STALE"; fi
  mkdir -p /var/lib/flop-ops; echo "$drift" > /var/lib/flop-ops/last-drift
fi
if [ -n "$cli_new" ] && [ "$cli_new" != "$cliver" ]; then
  cliver="$cli_new"
  mkdir -p /var/lib/flop-ops; echo "$cliver" > /var/lib/flop-ops/last-cliver
fi
[ "$drift" = "STALE" ] && note="${note:+$note; }worker image stale — upgrade to releases.json pin"

# Spend accrues continuously: base + rate * elapsed since SPEND_SINCE.
if [ -n "${SPEND_SINCE:-}" ] && [ -n "${SPEND_RATE_HR:-}" ]; then
  spend=$(awk -v iso="$SPEND_SINCE" -v r="$SPEND_RATE_HR" -v b="${SPEND_BASE:-0}" 'BEGIN{
    c="date -d \"" iso "\" +%s"; c|getline t0; close(c);
    printf "$%.2f", b + r*(systime()-t0)/3600 }')
else
  spend="${SPEND_SO_FAR:-unknown}"
fi

cat > "$OUT" <<JSON
{"updated":"$now","miner":"$miner","miner_state":"$miner_state",
"gpu":"$gpu","gpu_state":"$gpu_state",
"model":"$model","model_state":"$model_state",
"calibration":"$cal","calibration_state":"$cal_state",
"sessions":"$sess","sessions_state":"$sess_state",
"spend":"$spend","spend_state":"",
"kibble":"$kib","kibble_state":"$kib_state",
"endpoint":"$ep","endpoint_state":"$ep_state","drift":"$drift","cli":"$cliver","note":"$note"}
JSON
mv "$OUT" "$SITE/status.json"

# Email alert when the state signature changes (miner|gpu|calibration|sessions).
# Signature stored in /var/lib/flop-ops/last-sig; first run seeds it silently.
STATE_DIR=/var/lib/flop-ops
case "$sess" in *[1-9]*) sess_bucket=live;; *) sess_bucket=idle;; esac
sig="$miner_state|$gpu_state|$cal|$sess_bucket|$drift|cli:$cliver"
mkdir -p "$STATE_DIR"
prev=$(cat "$STATE_DIR/last-sig" 2>/dev/null || true)
if [ "$sig" != "$prev" ]; then
  echo "$sig" > "$STATE_DIR/last-sig"
  [ -n "$prev" ] && /usr/local/lib/flop-ops/notify.py "$prev" "$sig" >>/var/log/flop-ops-notify.log 2>&1 &
fi
