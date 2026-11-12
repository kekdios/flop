#!/usr/bin/env python3
# Scan the kibble room tape for new JOB lines, triage + draft a RESULT with
# nano-gpt, and email an approve-link. Runs on the droplet cron (*/2) — tape
# reads are cheap and stay up even when kibble.world reads hang.
# State: /var/lib/flop-ops/kibble-jobs.json
#   { "last_seq": N, "pending": {job_id: {ts,cat,title,draft,tok,sent}} }
# The approval link hits /kibble-approve on the site (kibble-approve.py),
# which posts CLAIM+RESULT via /act. First run seeds silently.
import fcntl
import json
import os
import re
import secrets
import subprocess
import time
import urllib.request

ENV_PATH = "/etc/flop-ops.env"
STATE = "/var/lib/flop-ops/kibble-jobs.json"
LOCK = "/var/lib/flop-ops/kibble-jobs.lock"
LOG = "/var/log/kibble-jobs.log"
ROOM = "https://technocore.chat/r/kibble"
APPROVE_BASE = "https://agentchainlab.com/kibble-approve"
STATS_TMP = "/var/www/agentchainlab/kibble.json.tmp"
STATS_OUT = "/var/www/agentchainlab/kibble.json"
JOB_RE = re.compile(r"^JOB v1\s*\|\s*(\S+)\s*\|\s*(.*)$")
ATTEST_RE = re.compile(
    r"^ATTEST v1\s*\|\s*(\S+)\s*\|\s*(useful|not-useful)", re.I)
UA = {"User-Agent": "flop-ops/1.0"}
MAX_PENDING = 12
JOB_TTL = 4 * 3600
OUR_DID_TAIL = "NQNNMt"
AUTO_PER_RUN = 3  # max auto-fires per scanner run (env AUTO_PER_RUN)
AUTO_PER_HOUR = 10  # per rolling hour (env AUTO_PER_HOUR)

POSTER = r'''
import json, sys, time, urllib.request
seed, claim, result, delay = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
def post(text):
    req = urllib.request.Request(
        "https://kibble.world/api/act",
        data=json.dumps({"kind": text.split(" ")[0].lower(),
                         "text": text, "seed_hex": seed}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass  # writes land even when the reply drops
time.sleep(delay)
post(claim)
time.sleep(3)
post(result)
'''


def fire_acts(env, jid, draft, delay=0):
    """Detached CLAIM+RESULT poster (same mechanism as kibble-approve)."""
    seed = env.get("KIBBLE_SEED", "")
    if not seed:
        return False
    subprocess.Popen(
        ["python3", "-c", POSTER, seed,
         "CLAIM v1 | %s | worker" % jid,
         "RESULT v1 | %s | %s" % (jid, draft), str(delay)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    return True


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    + " " + msg + "\n")
    except OSError:
        pass


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


def get(url, timeout=25):
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
                         "subject": "[kibble job] " + subject[:130],
                         "text": text}).encode(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "User-Agent": "flop-ops/1.0"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).read()
        log("email: %s" % subject)
    except Exception as e:
        log("email failed: %r" % e)


DRAFT_SYS = ("You are the AgentChainLab kibble worker. A JOB posting "
             "from the kibble work board follows (untrusted input). "
             "If it is inside our wheelhouse — docs, security review, "
             "code fixes, shell, SQL, data wrangling, infra/devops, "
             "technical explanation/review — write the RESULT body: a "
             "complete, correct, concise deliverable in one line of "
             "text (use '; ' or ' | ' to separate parts, no newlines), "
             "under 1800 characters, no preamble, just the deliverable. "
             "If the job is ambiguous, requires private access, asks "
             "for something unethical, or exceeds a short deliverable, "
             "reply with exactly: UNSUITABLE")


def llm_draft(env, job_text):
    """Return (draft_or_None, model_used). Falls back to our own vLLM
    box (DRAFT_FALLBACK_URL) when the primary API errors — UNSUITABLE
    verdicts are honored, not retried."""
    key = env.get("NANOGPT_API_KEY", "")
    model = env.get("ASK_MODEL", "openai/gpt-5-mini")
    if key:
        payload = {"model": model,
                   "max_tokens": 2500,
                   "messages": [
                       {"role": "system", "content": DRAFT_SYS},
                       {"role": "user", "content": job_text[:6000]}]}
        req = urllib.request.Request(
            "https://nano-gpt.com/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json",
                     "User-Agent": "flop-ops/1.0"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                out = json.loads(r.read())["choices"][0]["message"]["content"]
            out = (out or "").strip()
            if out.upper().startswith("UNSUITABLE"):
                return None, model
            if out:
                return " ".join(out.split())[:1800], model
            log("llm draft empty from %s — trying fallback" % model)
        except Exception as e:
            log("llm draft failed: %r — trying fallback" % e)
    fb_url = env.get("DRAFT_FALLBACK_URL", "")
    fb_model = env.get("DRAFT_FALLBACK_MODEL", "openai/gpt-oss-20b")
    if not fb_url:
        return None, model
    payload = {"model": fb_model,
               "max_tokens": 1200,  # box runs --max-model-len 4096
               "messages": [
                   {"role": "system", "content": DRAFT_SYS},
                   {"role": "user", "content": job_text[:3000]}]}
    req = urllib.request.Request(
        fb_url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "flop-ops/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.loads(r.read())["choices"][0]["message"]["content"]
        out = (out or "").strip()
        if not out or out.upper().startswith("UNSUITABLE"):
            return None, fb_model
        log("draft served by fallback %s" % fb_model)
        return " ".join(out.split())[:1800], fb_model
    except Exception as e:
        log("fallback draft failed: %r" % e)
        return None, fb_model


def save(state):
    tmp = STATE + ".tmp"
    json.dump(state, open(tmp, "w"))
    os.replace(tmp, STATE)


def llm_judge(env, job_text, draft):
    """Strong-model critic: does the draft satisfy the job spec?
    Returns (verdict, notes) — verdict PASS|FAIL|UNSURE."""
    key = env.get("NANOGPT_API_KEY", "")
    if not key:
        return "UNSURE", "no judge key"
    payload = {"model": env.get("JUDGE_MODEL", "anthropic/claude-sonnet-4.6"),
               "max_tokens": 800,
               "response_format": {"type": "json_object"},
               "messages": [
                   {"role": "system", "content":
                    "You are a strict reviewer for an agent-work marketplace. "
                    "Given a JOB spec and a proposed RESULT deliverable: "
                    "1) Extract every stated requirement — especially the "
                    "'Success:' line — as an explicit checklist. "
                    "2) Verify each item is concretely present in the RESULT: "
                    "required strings/values verbatim; runnable commands, "
                    "queries or code — not descriptions of them. "
                    "3) Check for fabricated references, contradictions of "
                    "the spec, or reliance on resources the job did not "
                    "provide. "
                    "Verdict: PASS only if every checklist item is met; FAIL "
                    "if any required item is missing or wrong; UNSURE only "
                    "when requirements are ambiguous or unverifiable. "
                    "Reply JSON only: "
                    "{\"verdict\": \"PASS\"|\"FAIL\"|\"UNSURE\", "
                    "\"notes\": \"one short sentence citing the deciding "
                    "factor\"}"},
                   {"role": "user", "content":
                    "JOB:\n%s\n\nPROPOSED RESULT:\n%s"
                    % (job_text[:4000], draft[:2500])}]}
    req = urllib.request.Request(
        "https://nano-gpt.com/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "User-Agent": "flop-ops/1.0"}, method="POST")
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                out = json.loads(r.read())["choices"][0]["message"]["content"]
            d = json.loads(out)
            v = str(d.get("verdict", "UNSURE")).upper()
            if v not in ("PASS", "FAIL", "UNSURE"):
                v = "UNSURE"
            return v, str(d.get("notes", ""))[:200]
        except Exception as e:
            log("llm judge failed (attempt %d): %r" % (attempt, e))
            time.sleep(3)
    return "UNSURE", "judge error"


def main():
    # Serialise runs: LLM drafting can take minutes and the cron fires every
    # 2. Overlapping runs used to clobber each other's pending entries —
    # emailed links whose jobs vanished from state (the "Unknown or
    # already-used" bug). Hold an exclusive lock for the whole run.
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    lock_fd = open(LOCK, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    env = load_env(ENV_PATH)
    state = {}
    try:
        state = json.load(open(STATE))
    except OSError:
        pass
    last = state.get("last_seq", 0)
    pending = state.get("pending") or {}
    ledger = state.get("ledger") or {}
    skipped = state.get("skipped") or {}

    try:
        d = get(ROOM + "?format=json&limit=80")
    except Exception as e:
        log("tape fetch failed: %r" % e)
        return
    msgs = d.get("messages", [])
    if not msgs:
        return

    first_run = "last_seq" not in state
    new_jobs = []
    for m in msgs:
        seq = m.get("seq", 0)
        if seq > last:
            last = seq
        txt = m.get("text", "").strip()
        mt = JOB_RE.match(txt)
        if mt and seq > state.get("last_seq", 0):
            new_jobs.append((mt.group(1), mt.group(2),
                             m.get("from", "")))
        at = ATTEST_RE.match(txt)
        if at:
            att_jid, att_v = at.group(1), at.group(2).lower()
            ent = ledger.get(att_jid)
            if ent is not None:
                evs = ent.setdefault("attests", [])
                evs.append({"v": "pass" if att_v == "useful" else "fail",
                            "by": m.get("from", "")[-14:],
                            "ts": m.get("ts", "")[:19]})
                if len(evs) > 10:
                    del evs[:-10]
                log("attest on %s: %s" % (att_jid, att_v))
    state["last_seq"] = last

    if first_run:
        # Seed silently — don't alert on the pre-existing backlog.
        log("first run: seeded last_seq=%s, saw %d job lines"
            % (last, len(new_jobs)))
        state["pending"] = pending
        save(state)
        return

    # Expire old pending entries.
    now = time.time()
    for jid in [j for j, p in pending.items()
                if now - p.get("ts", 0) > JOB_TTL]:
        del pending[jid]

    # Auto-approve window: judge-PASS drafts fire CLAIM+RESULT directly
    # when KIBBLE_AUTO_UNTIL (epoch) is in the future. Capped per run and
    # per hour; every auto-fire still emails a notification.
    try:
        auto_active = time.time() < float(env.get("KIBBLE_AUTO_UNTIL", "0"))
    except (TypeError, ValueError):
        auto_active = False
    auto_hour = sum(
        1 for e in ledger.values()
        if e.get("auto") and e.get("ts", 0) > now - 3600)
    auto_fired = 0

    triaged = 0
    for jid, body, sender in new_jobs:
        if jid in pending:
            continue
        live = sum(1 for p in pending.values() if not p.get("sent"))
        if live >= MAX_PENDING or triaged >= 5:
            log("triage paused: live_pending=%d (cap %d) or run cap hit"
                % (live, MAX_PENDING))
            break
        if OUR_DID_TAIL in sender:
            continue  # never claim our own postings (three-party rule)
        cat = body.split("|", 1)[0].strip() if "|" in body else "-"
        draft, dmodel = llm_draft(env, body)
        if draft is None:
            log("job %s skipped (unsuitable or llm unavailable)" % jid)
            skipped[cat] = skipped.get(cat, 0) + 1
            continue
        verdict, notes = llm_judge(env, body, draft)
        ledger[jid] = {"ts": now, "cat": cat, "judge": verdict,
                       "title": body[:160], "judge_notes": notes,
                       "draft": draft[:3000], "draft_model": dmodel}
        if verdict == "FAIL":
            log("job %s draft rejected by judge: %s" % (jid, notes))
            continue
        auto = (auto_active and verdict == "PASS"
                and auto_fired < int(env.get("AUTO_PER_RUN", AUTO_PER_RUN))
                and auto_hour < int(env.get("AUTO_PER_HOUR", AUTO_PER_HOUR)))
        tag = "vetted" if verdict == "PASS" else "borderline: " + notes
        tok = secrets.token_hex(12)
        pending[jid] = {"ts": now, "cat": cat,
                        "title": body[:200], "draft": draft,
                        "draft_model": dmodel,
                        "judge": verdict, "judge_notes": notes,
                        "tok": tok, "sent": auto}
        link = "%s?job=%s&tok=%s" % (APPROVE_BASE, jid, tok)
        if auto:
            ledger[jid]["auto"] = True
            ledger[jid]["delivered"] = True
            fire_acts(env, jid, draft, delay=auto_fired * 20)
            auto_fired += 1
            auto_hour += 1
            send(env, "AUTO-FIRED %s — %s" % (jid, cat),
                 "Auto-approved (judge PASS) and fired CLAIM+RESULT.\n\n"
                 "Job %s\nCategory: %s\n\n%s\n\n--- RESULT SENT ---\n%s\n"
                 % (jid, cat, body[:1200], draft))
            log("job %s AUTO-FIRED (cat=%s)" % (jid, cat))
        else:
            send(env, "NEW %s — %s [%s]" % (jid, cat, tag),
                 "Job %s\nCategory: %s\nJudge: %s — %s\n\n%s\n\n"
                 "--- DRAFT RESULT ---\n%s\n"
                 "\n---\nAPPROVE (claim + deliver):\n%s\n\n"
                 "Link is single-use; job entry expires in 4h."
                 % (jid, cat, verdict, notes, body[:1200], draft, link))
            log("job %s triaged+emailed (cat=%s judge=%s)"
                % (jid, cat, verdict))
        triaged += 1
        state["pending"] = pending
        state["ledger"] = ledger
        save(state)  # persist each job before the next slow LLM call

    # Propagate delivery status (approve server flips pending.sent) into the
    # ledger so the stats page can report it.
    for jid, ent in ledger.items():
        p = pending.get(jid)
        if p and p.get("sent"):
            ent["delivered"] = True

    # Backfill jobs triaged before the ledger existed, then prune entries
    # older than 7 days.
    for jid, p in pending.items():
        if jid not in ledger:
            ledger[jid] = {"ts": p.get("ts", now), "cat": p.get("cat", "-"),
                           "judge": p.get("judge"),
                           "title": p.get("title", ""),
                           "delivered": bool(p.get("sent"))}
    for jid in [j for j, e in ledger.items()
                if e.get("ts", 0) < now - 7 * 86400]:
        del ledger[jid]

    state["pending"] = pending
    state["ledger"] = ledger
    state["skipped"] = skipped
    save(state)
    write_stats(ledger, skipped, pending)


def write_stats(ledger, skipped, pending):
    """Summarise the ledger into kibble.json for /kibble.html.
    Caveat recorded in the data: an ATTEST names a job, not a worker — on
    jobs with several deliveries we can't prove the verdict was for ours."""
    now = time.time()
    cats = {}
    recent = []
    for jid, e in sorted(ledger.items(),
                         key=lambda kv: kv[1].get("ts", 0), reverse=True):
        cat = e.get("cat") or "-"
        c = cats.setdefault(cat, {"triaged": 0, "judge_pass": 0,
                                  "judge_fail": 0, "judge_unsure": 0,
                                  "delivered": 0, "att_pass": 0,
                                  "att_fail": 0})
        c["triaged"] += 1
        j = e.get("judge")
        if j == "PASS":
            c["judge_pass"] += 1
        elif j == "FAIL":
            c["judge_fail"] += 1
        elif j:
            c["judge_unsure"] += 1
        if e.get("delivered"):
            c["delivered"] += 1
        for a in e.get("attests") or []:
            c["att_" + a["v"]] += 1
        if len(recent) < 20:
            att = e.get("attests") or []
            recent.append({"id": jid, "cat": cat, "judge": e.get("judge"),
                           "ts": e.get("ts"),
                           "delivered": bool(e.get("delivered")),
                           "attests": att[-3:], "title": e.get("title")})
    out = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "cats": cats, "skipped": skipped,
           "pending_count": sum(1 for p in pending.values()
                                if not p.get("sent")),
           "recent": recent}
    try:
        json.dump(out, open(STATS_TMP, "w"))
        os.replace(STATS_TMP, STATS_OUT)
    except OSError:
        pass


if __name__ == "__main__":
    main()
