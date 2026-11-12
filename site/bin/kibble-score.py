#!/usr/bin/env python3
"""Kibble score snapshot collector.

GET /score?did= is the authoritative kibble score (results*1, useful*6,
etc.) but the read side hangs/times out often. This cron job retries a
few times, persists the last successful snapshot for the scorecard, and
appends a JSONL history so score deltas are attributable in time.

Files:
  /var/lib/flop-ops/kibble-score.json          last good snapshot
  /var/lib/flop-ops/kibble-score-history.jsonl append-only history
  /var/log/kibble-score.log                    attempt log
"""
import json
import os
import time
import urllib.request

DID = "did:key:z6MkgjCi5g5KjEphiSEe7SneiatLqPEPrRgD4chXbMNQNNMt"
URL = "https://kibble.world/api/score?did=" + DID
OUT = "/var/lib/flop-ops/kibble-score.json"
HIST = "/var/lib/flop-ops/kibble-score-history.jsonl"
LOG = "/var/log/kibble-score.log"
ATTEMPTS = 3
TIMEOUT = 20


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    + " " + msg + "\n")
    except OSError:
        pass


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    err = None
    for i in range(ATTEMPTS):
        try:
            req = urllib.request.Request(URL)
            d = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT)
                           .read())
            snap = {"ts": int(time.time()),
                    "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime()),
                    "data": d}
            tmp = OUT + ".tmp"
            with open(tmp, "w") as f:
                json.dump(snap, f)
            os.replace(tmp, OUT)
            try:
                with open(HIST, "a") as f:
                    f.write(json.dumps(snap) + "\n")
            except OSError:
                pass
            sc = d.get("score") if isinstance(d, dict) else d
            log("snapshot ok: score=%s" % sc)
            return
        except Exception as e:
            err = e
            time.sleep(4)
    log("fetch failed x%d: %r" % (ATTEMPTS, err))


if __name__ == "__main__":
    main()
