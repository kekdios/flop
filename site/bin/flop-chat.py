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


def env_key():
    for line in open(ENV_PATH):
        line = line.strip()
        if line.startswith("KIBBLE_SEED="):
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


def main():
    seed = env_key()
    if not seed:
        log("no KIBBLE_SEED in env — skipping")
        return
    bal = call("balance", seed)
    free = ((bal.get("balance") or {}).get("free")) or "?"
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
    log("free=%s chat.status=%s model=%s ok=%s reply=%r err=%s"
        % (free, ch.get("status"), ch.get("model"), ch.get("ok"),
           content, r.get("error")))


if __name__ == "__main__":
    main()
