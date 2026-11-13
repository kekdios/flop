#!/usr/bin/env python3
"""Collect FLOP devnet mining status → /var/www/agentchainlab/mining.json

Pulls session/info (auth'd), worker log events, sponsor log, container +
GPU state from the gpu.ai miner box, parses it into the JSON that
/mining.html polls. Runs on cron (*/5). Never writes secrets to output.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ENV_FILE = "/etc/flop-ops.env"
OUT = Path("/var/www/agentchainlab/mining.json")
DRIFT_FILE = Path("/var/lib/flop-ops/last-drift")
WORKSIG_FILE = Path("/var/lib/flop-ops/last-worksig")
NOTIFY = "/usr/local/lib/flop-ops/notify.py"

REMOTE_CMD = r"""
tok=$(grep -m1 '^SESSION_AUTH_TOKEN=' /opt/flop-miner/miner.env 2>/dev/null | cut -d= -f2)
echo "==INFO=="
info=""; for i in 1 2 3; do
  info=$(curl -sf --max-time 8 ${tok:+-H "Authorization: Bearer $tok"} http://127.0.0.1:8645/session/info 2>/dev/null) && break
  info=""; sleep 2
done
echo "$info"
echo
echo "==LOG=="
docker logs -t flop-miner-01-worker-1 --tail 400 2>&1 | tail -250
echo "==SPONSOR=="
tail -40 /var/log/flop-sponsor.log 2>/dev/null
echo "==DOCKER=="
docker ps --format '{{.Names}}|{{.Status}}'
echo "==GPU=="
nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null
echo "==ENV=="
grep -E '^(WORKER_NAME|MODEL_NAME|FLOP_WORKER_IMAGE)=' /opt/flop-miner/miner.env
"""

# Log lines worth surfacing in the activity feed.
KEEP_RE = re.compile(
    r"calibration|capacity rate|session host|chain settle|session[- ]"
    r"serv|endpoint ad|inventory|fenc|enrol|registered|bond|slash|settle|"
    r"verified job|job[s]?\b|error|fail|disconnect|refus", re.I)
DROP_RE = re.compile(
    r"ghost-task/|every 2000|polling https|miner-worker\.mjs|node:internal|"
    r"RPC-CORE|Failed WS Request|^\s+at |subscrib|known hosts|^\s*$",
    re.I)


def load_env():
    d = {}
    try:
        for line in open(ENV_FILE):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return d


def sections(raw):
    out, cur = {}, None
    for line in raw.splitlines():
        m = re.match(r"^==(\w+)==$", line.strip())
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def fetch(env):
    key = env.get("VM_SSH_KEY", "/root/.ssh/flop_vast")
    host = env.get("VM_SSH_HOST", "127.0.0.1")
    port = env.get("VM_SSH_PORT", "22")
    cmd = [
        "ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
        "-o", "StrictHostKeyChecking=accept-new", "-p", port,
        f"root@{host}", "bash", "-s",
    ]
    p = subprocess.run(cmd, input=REMOTE_CMD, capture_output=True,
                       text=True, timeout=90)
    if p.returncode != 0:
        raise RuntimeError(f"ssh rc={p.returncode}: {p.stderr[:200]}")
    return sections(p.stdout)


def parse_sponsor(txt):
    # Settled sponsorship legs from /var/log/flop-sponsor.log
    legs = re.findall(
        r'"kind":\s*"(\w+)"[^}]*?"amountRaw":\s*"(\d+)"[^}]*?'
        r'"txHash":\s*"(0x[0-9a-f]+)"[^}]*?"state":\s*"(\w+)"', txt, re.S)
    out = {"settled": False, "legs": []}
    seen = set()
    for kind, raw, tx, state in legs:
        if tx in seen:                # sponsor log repeats the same tx set
            continue
        seen.add(tx)
        out["legs"].append({
            "kind": kind,
            "flop": round(int(raw) / 1e18, 4),
            "tx": tx, "state": state,
        })
        if state == "settled":
            out["settled"] = True
    return out


def parse_events(log):
    evs, prev = [], ""
    for line in log.splitlines():
        m = re.match(r"^(\S+)\s+(.*)$", line)
        if not m:
            continue
        ts, body = m.groups()
        body = body.strip()
        if not KEEP_RE.search(body) or DROP_RE.search(body):
            continue
        body = re.sub(r"\s+", " ", body)[:300]
        if body == prev:          # collapse consecutive dupes
            continue
        prev = body
        evs.append({"ts": ts.split(".")[0] + "Z" if "T" in ts else ts,
                    "line": body})
    return evs[-40:]


def main():
    env = load_env()
    try:
        s = fetch(env)
    except Exception as e:
        print(f"flop-mining: fetch failed: {e}", file=sys.stderr)
        # Keep last JSON alive but mark stale fetch.
        data = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "error": str(e)}
        if OUT.exists():
            try:
                data = json.loads(OUT.read_text())
                data["updated"] = time.strftime("%Y-%m-%d %H:%M UTC",
                                                time.gmtime())
                data["error"] = str(e)
            except Exception:
                pass
        OUT.write_text(json.dumps(data) + "\n")
        return

    info, env_kv = {}, {}
    try:
        info = json.loads(s.get("INFO", ""))
    except Exception:
        pass
    fresh = bool(info)
    prev = {}
    if not info and OUT.exists():
        # Endpoint fetch failed — keep last-good calibration/serving/identity
        try:
            prev = json.loads(OUT.read_text())
            info = prev.get("_info", {})
        except Exception:
            pass
    for line in s.get("ENV", "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env_kv[k.strip()] = v.strip()

    chain = {}
    if info.get("minerAddress"):
        try:
            chain = json.loads(urllib.request.urlopen(urllib.request.Request(
                "https://explorer.devnet.flop.network/api/miners/"
                + info["minerAddress"],
                headers={"User-Agent": "flop-ops/1.0"}), timeout=10).read())
        except Exception:
            pass
    net = {}
    try:
        net = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://explorer.devnet.flop.network/api/stats",
            headers={"User-Agent": "flop-ops/1.0"}), timeout=10).read())
    except Exception:
        pass

    log = s.get("LOG", "")
    cal_log = re.search(
        r"\[calibration\] initial cap accepted: ([\d.]+) GFLOP/s from "
        r"(\d+) verified jobs \(window from #(\d+)\)", log)
    cap_log = re.search(
        r"capacity rate:\s*(?:set\s*)?([\d.]+) G_n/s for (\d+) sessions",
        log)
    cal = info.get("calibration") or {}
    img = env_kv.get("FLOP_WORKER_IMAGE", "")
    drift = DRIFT_FILE.read_text().strip() if DRIFT_FILE.exists() else "?"
    docker = {}
    for line in s.get("DOCKER", "").splitlines():
        if "|" in line:
            n, st = line.split("|", 1)
            docker[n.strip()] = st.strip()
    sponsor = parse_sponsor(s.get("SPONSOR", ""))

    data = {
        "updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "identity": {
            "miner": info.get("minerAddress", "?"),
            "worker": env_kv.get("WORKER_NAME", "devnet-ts-miner-01"),
            "model": info.get("model", env_kv.get("MODEL_NAME", "?")),
            "model_hash": (info.get("modelIdentity") or {}).get(
                "modelHash", info.get("modelHash", "?")),
            "image": info.get("image", {}).get("version",
                                               img.split(":")[-1].split("@")[0]),
            "drift": drift,
        },
        "calibration": {
            "ready": cal.get("ready"),
            "state": cal.get("state"),
            "cap_gflops": cal.get("capGflopsPerSec",
                                  cal_log.group(1) if cal_log else None),
            "verified_jobs": int(cal_log.group(2)) if cal_log else None,
            "window_start": int(cal_log.group(3)) if cal_log else None,
            "accepted_at": cal.get("acceptedAt"),
            "expires_at": cal.get("expiresAt"),
            "current_block": cal.get("currentBlock"),
            "version": cal.get("calibrationVersion"),
        },
        "serving": {
            "is_ready": info.get("is_ready"),
            "sessions": info.get("sessions", 0),
            "capacity_gn": int(cap_log.group(1)) if cap_log else None,
            "capacity_sessions": int(cap_log.group(2)) if cap_log else None,
            "max_unacked_gn": info.get("maxUnackedGn"),
            "audit_handoff": info.get("audit_handoff_enabled"),
        },
        "chain": {
            "self_stake": int(chain.get("selfStake", "0") or 0) / 1e18,
            "sponsored_stake": int(chain.get("sponsoredStake", "0") or 0)
                / 1e18,
            "delegated_stake": int(chain.get("delegatedStake", "0") or 0)
                / 1e18,
            "is_active": chain.get("isActive"),
            "blacklisted": chain.get("blacklisted"),
            "strikes": chain.get("strikes"),
            "registered_block": chain.get("registeredBlock"),
            "commission": chain.get("commission"),
            "total_rewards": int(chain.get("totalRewards", "0") or 0) / 1e18,
            "total_slashed": int(chain.get("totalSlashed", "0") or 0) / 1e18,
            "gpu_inventory_declared":
                chain.get("declaredGpuInventory") is not None,
            "poui_rank": chain.get("pouiRank"),
            "work": chain.get("work") or {},
        },
        "network": {
            "sessions_htlc": net.get("totalHTLCs"),
            "sessions_active": net.get("activeHTLCs"),
            "proofs_24h": net.get("proofs24h"),
            "latest_block": net.get("latestBlockNumber"),
            "best_block": (net.get("finality") or {}).get("bestBlock"),
            "index_lag": ((net.get("finality") or {}).get("bestBlock") or 0)
                - (net.get("latestBlockNumber") or 0) or None,
            "registered_miners": net.get("registeredMiners"),
            "active_agents": net.get("activeAgents"),
        },
        "sponsorship": sponsor,
        "hardware": {"gpu": s.get("GPU", "")},
        "containers": docker,
        "events": parse_events(log),
        "info_stale": not fresh,
        "_info": info or prev.get("_info", {}),
    }
    OUT.write_text(json.dumps(data, indent=1) + "\n")

    # Email on on-chain work-counter transitions (first run seeds silently).
    w = data["chain"]["work"]
    sig = "work " + "|".join(
        str(w.get(k, 0)) for k in ("settled", "committed", "inFlight"))
    try:
        prev = WORKSIG_FILE.read_text().strip() \
            if WORKSIG_FILE.exists() else None
        WORKSIG_FILE.write_text(sig + "\n")
        if prev is not None and prev != sig:
            subprocess.run([NOTIFY, prev, sig], check=False)
    except Exception:
        pass


if __name__ == "__main__":
    main()
