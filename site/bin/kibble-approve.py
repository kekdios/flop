#!/usr/bin/env python3
# Approval endpoint for kibble job drafts. Binds 127.0.0.1:8578; nginx proxies
# GET /kibble-approve?job=<id>&tok=<hex> here. Verifies the one-time token from
# /var/lib/flop-ops/kibble-jobs.json (written by kibble-jobs.py), then fires a
# detached poster that POSTs CLAIM v1 then RESULT v1 to kibble.world/api/act —
# writes land even when the API's read side hangs (fire-and-forget).
import html
import json
import os
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

ENV_PATH = "/etc/flop-ops.env"
STATE = "/var/lib/flop-ops/kibble-jobs.json"
LOG = "/var/log/kibble-approve.log"
JOB_TTL = 4 * 3600
LISTEN = ("127.0.0.1", 8578)

POSTER = r'''
import json, sys, time, urllib.request
seed, jid, result = sys.argv[1], sys.argv[2], sys.argv[3]
def post(payload):
    req = urllib.request.Request(
        "https://kibble.world/api/act",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass  # writes land even when the reply drops
post({"kind": "claim", "job_id": jid, "seed_hex": seed})
time.sleep(3)
post({"kind": "result", "job_id": jid, "body": result, "seed_hex": seed})
'''


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    + " " + msg + "\n")
    except OSError:
        pass


def load_env():
    env = {}
    try:
        for line in open(ENV_PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def page(title, body, ok=True):
    color = "#2e9d2e" if ok else "#e05563"
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width'>"
            "<title>%s</title></head><body style='background:#0a1a29;"
            "color:#fff;font-family:system-ui;padding:40px;max-width:600px'>"
            "<h2 style='color:%s'>%s</h2><p style='color:#b0b3b8'>%s</p>"
            "<p><a href='/' style='color:#2e9d2e'>← ops board</a></p>"
            "</body></html>" % (title, color, title, body)).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        jid = (q.get("job") or [""])[0]
        tok = (q.get("tok") or [""])[0]
        try:
            state = json.load(open(STATE))
        except OSError:
            state = {}
        p = (state.get("pending") or {}).get(jid)

        if not p or p.get("sent"):
            return self.reply(page("Unknown or already-used link",
                                   "This approval link is invalid or was "
                                   "already consumed.", False), 404)
        if tok != p.get("tok"):
            return self.reply(page("Bad token", "Token mismatch.", False), 403)
        if time.time() - p.get("ts", 0) > JOB_TTL:
            return self.reply(page("Expired", "Job draft expired.", False), 410)

        seed = load_env().get("KIBBLE_SEED", "")
        if not seed:
            return self.reply(page("Not configured",
                                   "KIBBLE_SEED missing on the droplet.",
                                   False), 500)

        subprocess.Popen(
            ["python3", "-c", POSTER, seed, jid, p.get("draft", "")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

        p["sent"] = True
        p["sent_ts"] = time.time()
        tmp = STATE + ".tmp"
        json.dump(state, open(tmp, "w"))
        os.replace(tmp, STATE)
        log("approved %s — CLAIM+RESULT fired" % jid)
        self.reply(page(
            "Approved — claim fired",
            "CLAIM v1 + RESULT v1 for <code>%s</code> are posting to the "
            "kibble tape now (fire-and-forget).<br><br>"
            "<b>Delivered draft:</b><br><code>%s</code>"
            % (html.escape(jid), html.escape(p.get("draft", ""))[:600])))

    def reply(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(LISTEN, Handler).serve_forever()
