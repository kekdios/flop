#!/bin/bash
# Collect miner status from the Vast VM and write status.json for the ops page.
# Config in /etc/flop-ops.env: VM_SSH_HOST VM_SSH_PORT VM_SSH_KEY SPEND_SO_FAR ENDPOINT
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

if REMOTE=$($SSH 'bash -s' <<'EOS' 2>/dev/null
gpu=$(nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null | head -1)
dock=$(docker ps --format '{{.Names}}:{{.Status}}' 2>/dev/null | tr '\n' ';')
info=$(curl -s --max-time 5 http://127.0.0.1:8645/session/info 2>/dev/null || true)
disk=$(df -h / | awk 'NR==2{print $4" free"}')
model=$(basename "$(grep -m1 '^MODEL_NAME=' /opt/flop-miner/miner.env 2>/dev/null | cut -d= -f2-)" 2>/dev/null)
echo "GPU=$gpu"
echo "DOCKER=$dock"
echo "DISK=$disk"
echo "MODEL=$model"
echo "SESSION_INFO<<$info>>"
EOS
); then
  gpu=$(echo "$REMOTE" | sed -n 's/^GPU=//p'); [ -n "$gpu" ] && gpu_state="ok" || gpu_state="bad"
  miner=$(echo "$REMOTE" | sed -n 's/^DOCKER=//p')
  if [ -n "$miner" ]; then miner_state="ok"; else miner="docker down"; miner_state="bad"; fi
  info=$(echo "$REMOTE" | sed -n 's/^SESSION_INFO<<//;s/>>$//p')
  if echo "$info" | grep -q '"is_ready"[ :]*true'; then cal="ready"; cal_state="ok";
  elif echo "$info" | grep -q calibration; then cal="in progress"; cal_state="warn";
  else cal="not calibrated"; fi
  sess=$(echo "$info" | grep -oE '"sessions"[ :]*[0-9]+' | grep -oE '[0-9]+' | head -1); sess=${sess:-0}
  note=$(echo "$REMOTE" | sed -n 's/^DISK=//p')
  model=$(echo "$REMOTE" | sed -n 's/^MODEL=//p'); [ -n "$model" ] && model_state="ok" || model="—"
else
  miner="unreachable"; miner_state="bad"; ep_state="bad"; note="ssh to VM failed"
fi

cat > "$OUT" <<JSON
{"updated":"$now","miner":"$miner","miner_state":"$miner_state",
"gpu":"$gpu","gpu_state":"$gpu_state",
"model":"$model","model_state":"$model_state",
"calibration":"$cal","calibration_state":"$cal_state",
"sessions":"$sess","sessions_state":"$sess_state",
"spend":"${SPEND_SO_FAR:-unknown}","spend_state":"",
"endpoint":"$ep","endpoint_state":"$ep_state","note":"$note"}
JSON
mv "$OUT" "$SITE/status.json"

# Email alert when the state signature changes (miner|gpu|calibration|sessions).
# Signature stored in /var/lib/flop-ops/last-sig; first run seeds it silently.
STATE_DIR=/var/lib/flop-ops
case "$sess" in *[1-9]*) sess_bucket=live;; *) sess_bucket=idle;; esac
sig="$miner_state|$gpu_state|$cal|$sess_bucket"
mkdir -p "$STATE_DIR"
prev=$(cat "$STATE_DIR/last-sig" 2>/dev/null || true)
if [ "$sig" != "$prev" ]; then
  echo "$sig" > "$STATE_DIR/last-sig"
  [ -n "$prev" ] && /usr/local/lib/flop-ops/notify.py "$prev" "$sig" >>/var/log/flop-ops-notify.log 2>&1 &
fi
