#!/usr/bin/env python3
# Collect Numes provider status and write numes.json for /numes.html.
# Runs on the ops droplet (cron). The or-tail OpenRouter reseller daemon
# lives ON the droplet (systemd numes-or-tail.service) since the vast.ai
# GPU box was retired 2026-11-14 — gpt-oss-a4000 self-hosted offer was
# cleanly delisted (`profile remove`, remote_state deleted).
# Sources:
#   - local `numes provider status --profile or-tail --local`
#   - local `numes auth status` (mgmt credential freshness)
#   - api.numes.ai /models with the BTC inference key (catalogue is
#     currency-scoped — BTC keys only see BTC-offered models)
# Config in /etc/flop-ops.env: NUMES_BTC_KEY
import json
import os
import subprocess
import time
import urllib.request

OUT_TMP = "/var/www/agentchainlab/numes.json.tmp"
OUT = "/var/www/agentchainlab/numes.json"
API = "https://api.numes.ai/api/v1"
OUR_MODEL = "thedrummer/* (or-tail reseller)"
NUMES = "/root/.local/bin/numes"
OUR_MODELS = {"thedrummer/unslopnemo-12b", "thedrummer/cydonia-24b-v4.1",
              "thedrummer/skyfall-36b-v2"}


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


def run(cmd, timeout=25):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def api(path, key, timeout=15):
    req = urllib.request.Request(API + path,
                                 headers={"Authorization": "Bearer " + key,
                                          "User-Agent": "flop-ops-collector/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def main():
    cfg = env()
    out = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "model": OUR_MODEL, "probe_ok": False, "catalogue_btc": None,
           "note": "gpu box retired 2026-11-14; or-tail daemon on droplet"}

    # --- local or-tail daemon (OpenRouter reseller) ---
    try:
        r = run([NUMES, "provider", "status", "--profile", "or-tail",
                 "--local"], timeout=25)
        st = json.loads(r.stdout.decode())
        out["probe_ok"] = True
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
        lc = st.get("lifecycle") or {}
        out["profiles"] = {"or-tail": {
            "session": rt.get("sessionState"),
            "source_health": rt.get("sourceHealth"),
            "endpoint": ((st.get("endpoint") or {}).get("state")
                         or (lc.get("endpoint") or {}).get("status")),
            "listings": [{"model": l.get("model"), "status": l.get("status")}
                         for l in (lc.get("listings") or [])],
        }}
    except Exception as e:
        out["probe_error"] = str(e)[:200]

    try:
        a = json.loads(run([NUMES, "auth", "status"], timeout=10)
                       .stdout.decode())
        methods = a.get("signed_in_methods") or []
        out["mgmt_auth"] = ("signed_in:" + ",".join(methods)
                            if methods else "expired")
    except Exception:
        pass

    try:
        p = run(["pgrep", "-fc", "numes provider daemon"], timeout=10)
        out["daemon_running"] = p.stdout.decode().strip() not in ("", "0")
    except Exception:
        pass

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
                if m.get("id") in OUR_MODELS:
                    cat["our_model_listed"] = True
                    cat.setdefault("our_listed", []).append(m.get("id"))
            out["catalogue_btc"] = cat
        except Exception as e:
            out["catalogue_error"] = str(e)[:200]

    tmp = json.dumps(out)
    open(OUT_TMP, "w").write(tmp)
    os.replace(OUT_TMP, OUT)
    print(tmp)


if __name__ == "__main__":
    main()
