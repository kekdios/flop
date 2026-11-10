#!/usr/bin/env python3
# Watch our kibble identity and email on noteworthy changes.
# Cron on the droplet (*/15). State: /var/lib/flop-ops/kibble-watch.json.
# Alerts via Resend using /etc/flop-ops.env (RESEND_API_KEY ALERT_TO ALERT_FROM).
import json
import os
import re
import urllib.request

ENV_PATH = "/etc/flop-ops.env"
STATE = "/var/lib/flop-ops/kibble-watch.json"
DID = "did:key:z6MkgjCi5g5KjEphiSEe7SneiatLqPEPrRgD4chXbMNQNNMt"
API = "https://kibble.world/api"
ROOM = "https://technocore.chat/r/kibble"
UA = {"User-Agent": "flop-ops/1.0"}
SUBJ_PREFIX = "[kibble] "


def load_env(path):
    env = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def get(url, timeout=40):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read())


def send(env, subject, text):
    key, to = env.get("RESEND_API_KEY", ""), env.get("ALERT_TO", "")
    if not key or not to:
        return
    frm = env.get("ALERT_FROM", "resend.dev")
    sender = frm if "<" in frm else ("flop-ops@%s" % frm if "@" not in frm
                                   else "FLOP Ops <%s>" % frm)
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": sender, "to": [to],
                         "subject": SUBJ_PREFIX + subject[:140],
                         "text": text}).encode(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "User-Agent": "flop-ops/1.0"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:
        pass


def main():
    state = {}
    try:
        state = json.load(open(STATE))
    except OSError:
        pass
    env = load_env(ENV_PATH)
    alerts = []

    # 1) score / franchise
    try:
        sc = get(API + "/score?did=" + DID)
        score = sc.get("score", 0)
        terms = (sc.get("breakdown") or {}).get("terms") or {}
        prev = state.get("score")
        if prev is not None and score != prev:
            alerts.append(("score %s -> %s" % (prev, score),
                           "Score moved.\n\nterms: %s\n" % json.dumps(terms)))
        state["score"] = score
    except Exception:
        pass

    # 2) onramp / validator-magnet openings (time-sensitive)
    try:
        st = get(API + "/status")
        for key_, label in (("franchise_onramp", "franchise on-ramp job"),
                            ("review_magnet", "validator-magnet job")):
            open_now = bool((st.get(key_) or {}).get("open"))
            if open_now and not state.get(key_):
                alerts.append(("%s OPEN" % label,
                               "%s opened on the kibble board.\n"
                               "Claim window is finite — run "
                               "~/.flop/kibble.py cycle to grab it."
                               % label))
            state[key_] = open_now
    except Exception:
        pass

    # 3) new tape lines mentioning us (ATTEST on our jobs, replies)
    try:
        d = get(ROOM + "?format=json&limit=120")
        tail = DID[-6:]
        last = state.get("last_mention_seq", 0)
        mentions = []
        for m in d.get("messages", []):
            if m.get("seq", 0) <= last:
                continue
            txt = m.get("text", "")
            ours = tail in m.get("from", "")
            about = DID in txt or tail in txt
            if ours or about:
                mentions.append("%s %s %s" % (m["ts"][:19],
                                              m["from"][-6:], txt[:300]))
            last = max(last, m["seq"])
        state["last_mention_seq"] = last
        if mentions:
            alerts.append(("%d new tape line(s) for our DID"
                           % len(mentions),
                           "\n\n".join(mentions[:15])))
    except Exception:
        pass

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, "w"))
    for subject, body in alerts:
        send(env, subject, body + "\n\nDID: %s\n" % DID)


if __name__ == "__main__":
    main()
