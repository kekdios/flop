#!/usr/bin/env python3
# Send a FLOP miner state-change alert via the Resend API.
# Usage: notify.py <prev-signature> <new-signature> [field1,field2,...]
# Signature: "<miner_state>|<gpu_state>|<calibration>|<sessions-bucket>"
# Optional 3rd arg overrides slot labels (e.g. work counters:
# "settled,committed,inFlight").
# Config: /etc/flop-ops.env (RESEND_API_KEY, ALERT_TO, ALERT_FROM)
import json
import sys
import urllib.request

ENV_PATH = "/etc/flop-ops.env"
STATUS_PATH = "/var/www/agentchainlab/status.json"
FIELDS = ["miner", "gpu", "calibration", "sessions"]


def load_env(path):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def main():
    prev, cur = sys.argv[1], sys.argv[2]
    env = load_env(ENV_PATH)
    key, to = env.get("RESEND_API_KEY", ""), env.get("ALERT_TO", "")
    if not key or not to:
        print("RESEND_API_KEY or ALERT_TO missing from " + ENV_PATH, file=sys.stderr)
        return 1

    frm = env.get("ALERT_FROM", "resend.dev")
    if "<" in frm:
        sender = frm  # already a full "Name <addr>" value
    elif "@" in frm:
        sender = "FLOP Ops <%s>" % frm
    else:
        sender = "FLOP Ops <flop-ops@%s>" % frm

    labels = sys.argv[3].split(",") if len(sys.argv) > 3 else FIELDS
    p, c = prev.split("|"), cur.split("|")
    changes = "; ".join(
        "%s: %s -> %s" % (n, a, b)
        for n, a, b in zip(labels, p, c)
        if a != b
    ) or ("%s -> %s" % (prev, cur))

    try:
        with open(STATUS_PATH) as f:
            status = f.read().strip()
    except OSError:
        status = "(status.json unavailable)"

    payload = {
        "from": sender,
        "to": [to],
        "subject": "[flop-ops] " + changes[:140],
        "text": "FLOP miner state changed.\n\n%s\n\nstatus.json:\n%s\n"
                % (changes, status),
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "flop-ops-notify/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("sent: %s -> %s (%d)" % (changes, to, r.status))
            return 0
    except urllib.error.HTTPError as e:
        print("resend error %d: %s" % (e.code, e.read()[:400]), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
