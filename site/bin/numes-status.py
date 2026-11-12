#!/usr/bin/env python3
# Collect Numes provider-probe status and write numes.json for /numes.html.
# Runs on the ops droplet (cron). Sources:
#   - SSH to the vast.ai probe box: `numes provider status`, vLLM /v1/models,
#     nvidia-smi, daemon liveness
#   - api.numes.ai /models with the BTC inference key (catalogue is
#     currency-scoped — BTC keys only see BTC-offered models)
# Config in /etc/flop-ops.env:
#   NUMES_SSH_HOST NUMES_SSH_PORT NUMES_SSH_KEY NUMES_BTC_KEY
import json
import os
import subprocess
import time
import urllib.request

OUT_TMP = "/var/www/agentchainlab/numes.json.tmp"
OUT = "/var/www/agentchainlab/numes.json"
API = "https://api.numes.ai/api/v1"
OUR_MODEL = "openai/gpt-oss-20b"


def env():
    cfg = {}
    try:
        for line in open("/etc/flop-ops.env"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return cfg


def ssh(cfg, script, timeout=25):
    return subprocess.run(
        ["ssh", "-i", cfg["NUMES_SSH_KEY"], "-o", "BatchMode=yes",
         "-o", "ConnectTimeout=12", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         "-p", cfg["NUMES_SSH_PORT"],
         "root@%s" % cfg["NUMES_SSH_HOST"], "bash", "-s"],
        input=script.encode(), capture_output=True, timeout=timeout)


def api(path, key, timeout=15):
    req = urllib.request.Request(API + path,
                                 headers={"Authorization": "Bearer " + key})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def main():
    cfg = env()
    out = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "model": OUR_MODEL, "ssh_ok": False, "catalogue_btc": None}

    # --- probe box over ssh ---
    if all(cfg.get(k) for k in ("NUMES_SSH_HOST", "NUMES_SSH_PORT",
                                "NUMES_SSH_KEY")):
        try:
            r = ssh(cfg, r'''
export PATH=$HOME/.local/bin:$PATH
echo "STATUS<<"
numes provider status --profile gpt-oss-a4000 2>/dev/null
echo ">>"
echo "GPU=$(nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader 2>/dev/null | head -1)"
echo "VLLM=$(curl -s --max-time 5 http://127.0.0.1:8000/v1/models | python3 -c 'import json,sys;d=json.load(sys.stdin);print(len(d.get("data",[])))' 2>/dev/null)"
echo "DAEMON=$(pgrep -fc 'numes provider daemon' 2>/dev/null)"
echo "RECONNECTS=$(grep -c 'reconnect' /root/vllm.log 2>/dev/null)"
''')
            txt = r.stdout.decode()
            if r.returncode == 0 and "STATUS<<" in txt:
                out["ssh_ok"] = True
                blob = txt.split("STATUS<<", 1)[1].split(">>", 1)[0]
                try:
                    st = json.loads(blob)
                    out["provider_state"] = st.get("state")
                    out["next_action"] = st.get("next_action")
                    rt = st.get("runtime") or {}
                    out["runtime"] = {
                        "phase": rt.get("phase"),
                        "session": rt.get("sessionState"),
                        "source_health": rt.get("sourceHealth"),
                        "reconnects": rt.get("reconnects"),
                        "last_failure": rt.get("lastFailure"),
                    }
                    plat = st.get("platform") or {}
                    out["platform"] = {
                        "endpoint": (plat.get("endpoint") or {}).get("status"),
                        "listings": [{ "model": l.get("model"),
                                       "status": l.get("status")}
                                     for l in plat.get("listings") or []],
                        "offers": plat.get("offers"),
                    }
                except (ValueError, AttributeError):
                    out["provider_state"] = "status_parse_error"
                for line in txt.splitlines():
                    if line.startswith("GPU="):
                        parts = [p.strip() for p in line[4:].split(",")]
                        if len(parts) >= 5:
                            out["gpu"] = {"name": parts[0], "util": parts[1],
                                          "mem_used": parts[2],
                                          "mem_total": parts[3],
                                          "temp": parts[4]}
                    elif line.startswith("VLLM="):
                        out["vllm_models"] = line[5:].strip()
                    elif line.startswith("DAEMON="):
                        out["daemon_running"] = line[7:].strip() not in ("", "0")
        except Exception as e:
            out["ssh_error"] = str(e)[:200]

    # --- BTC-scoped catalogue ---
    if cfg.get("NUMES_BTC_KEY"):
        try:
            models = api("/models", cfg["NUMES_BTC_KEY"]).get("data", [])
            cat = {"count": len(models), "our_model_listed": False,
                   "models": []}
            for m in models:
                usd = (m.get("pricing") or {}).get("usd_rates") or {}
                cat["models"].append({
                    "id": m.get("id"),
                    "ctx": m.get("context_length"),
                    "usd_prompt": usd.get("prompt"),
                    "usd_completion": usd.get("completion")})
                if m.get("id") == OUR_MODEL:
                    cat["our_model_listed"] = True
            out["catalogue_btc"] = cat
        except Exception as e:
            out["catalogue_error"] = str(e)[:200]

    tmp = json.dumps(out)
    open(OUT_TMP, "w").write(tmp)
    os.replace(OUT_TMP, OUT)
    print(tmp)


if __name__ == "__main__":
    main()
