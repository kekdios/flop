#!/usr/bin/env python3
# Demand-side heartbeat: spend a little devnet FLOP via the kibble->FLOP
# bridge `chat` action. Exercises the agent-purchases-compute path that
# testnet's agent cohort is scored on, and doubles as a liveness probe
# for the devnet gateway. Funds come from the daily faucet drip into the
# (custodial, rotating) bridge address — spend-it-or-lose-it budget.
# Cron on the droplet (*/4h). Log: /var/log/flop-chat.log
import json
import time
import urllib.request

ENV_PATH = "/etc/flop-ops.env"
LOG = "/var/log/flop-chat.log"
API = "https://kibble.world/api/flop-devnet"
FAUCET = "https://faucet.flop.network/fund"
PROMPTS = [
    "Reply with exactly one word: the capital of France.",
    "Reply with exactly one word: 12 multiplied by 7.",
    "In one short sentence, what is proof of inference?",
    "Reply with exactly one word: the chemical symbol for gold.",
    "In one short sentence, describe a GPU.",
    "Reply with exactly one word: the largest planet.",
]


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    + " " + msg + "\n")
    except OSError:
        pass


def env_key(name="KIBBLE_SEED"):
    for line in open(ENV_PATH):
        line = line.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def call(action, seed, **kw):
    p = {"action": action, "seed_hex": seed}
    p.update(kw)
    req = urllib.request.Request(
        API, data=json.dumps(p).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"ok": False, "error": "http %d" % e.code}
    except Exception as e:
        return {"ok": False, "error": repr(e)[:120]}


def fund(addr):
    req = urllib.request.Request(
        FAUCET, data=json.dumps({"address": addr}).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "flop-ops/1.0"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"success": False, "error": "http %d" % e.code}
    except Exception as e:
        return {"success": False, "error": repr(e)[:120]}


def main():
    seed = env_key()
    if not seed:
        log("no KIBBLE_SEED in env — skipping")
        return
    # Also top up the NEAR-derived multichain address when it's low —
    # rides the heartbeat's 4h cadence instead of the nightly cron.
    mc = env_key("MULTICHAIN_ADDR")
    if mc:
        m = fund(mc)
        if m.get("success"):
            log("multichain %s… funded +25" % mc[:8])
    addr = (call("address", seed) or {}).get("address", "")
    bal = call("balance", seed)
    b = bal.get("balance") or {}
    try:
        free_amt = float(b.get("free") or 0)
    except (TypeError, ValueError):
        free_amt = 0.0
    # Spend-it-or-lose-it: the bridge rotates the custodial address without
    # moving balances, so claim the anonymous 25 FLOP grant onto the CURRENT
    # assignment seconds before spending — claiming hours ahead strands it.
    funded = ""
    if addr and free_amt < 5:
        f = fund(addr)
        if f.get("success"):
            funded = "faucet+25"
            time.sleep(15)
            bal = call("balance", seed)
            b = bal.get("balance") or {}
            try:
                free_amt = float(b.get("free") or 0)
            except (TypeError, ValueError):
                free_amt = 0.0
        else:
            funded = "faucet_fail:" \
                + str(f.get("error") or f.get("message") or "?")[:60]
    free = b.get("free") or "?"
    prompt = PROMPTS[int(time.time() // 14400) % len(PROMPTS)]
    for attempt in (0, 1):
        r = call("chat", seed, prompt=prompt)
        if (r.get("error") == "rate_limited"
                and attempt == 0):
            time.sleep(int(r.get("retry_after_sec", 6)) + 1)
            continue
        break
    ch = r.get("chat") or {}
    content = (ch.get("content") or "")[:120].replace("\n", " ")
    log("addr=…%s free=%s %s chat.status=%s model=%s ok=%s reply=%r err=%s"
        % (addr[-6:] or "??????", free, funded, ch.get("status"),
           ch.get("model"), ch.get("ok"), content, r.get("error")))


if __name__ == "__main__":
    main()
