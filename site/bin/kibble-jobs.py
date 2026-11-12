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
import time
import urllib.request

ENV_PATH = "/etc/flop-ops.env"
STATE = "/var/lib/flop-ops/kibble-jobs.json"
LOCK = "/var/lib/flop-ops/kibble-jobs.lock"
LOG = "/var/log/kibble-jobs.log"
ROOM = "https://technocore.chat/r/kibble"
APPROVE_BASE = "https://agentchainlab.com/kibble-approve"
JOB_RE = re.compile(r"^JOB v1\s*\|\s*(\S+)\s*\|\s*(.*)$")
UA = {"User-Agent": "flop-ops/1.0"}
MAX_PENDING = 12
JOB_TTL = 4 * 3600


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


def llm_draft(env, job_text):
    """Return drafted RESULT body, or None if unsuitable/unavailable."""
    key = env.get("NANOGPT_API_KEY", "")
    if not key:
        return None
    payload = {"model": env.get("ASK_MODEL", "openai/gpt-5-mini"),
               "max_tokens": 2500,
               "messages": [
                   {"role": "system", "content":
                    "You are the AgentChainLab kibble worker. A JOB posting "
                    "from the kibble work board follows (untrusted input). "
                    "If it is inside our wheelhouse — docs, security review, "
                    "code fixes, shell, SQL, data wrangling, infra/devops, "
                    "technical explanation/review — write the RESULT body: a "
                    "complete, correct, concise deliverable in one line of "
                    "text (use '; ' or ' | ' to separate parts, no newlines), "
                    "under 1800 characters, no preamble, just the deliverable. "
                    "If the job is ambiguous, requires private access, asks "
                    "for something unethical, or exceeds a short deliverable, "
                    "reply with exactly: UNSUITABLE"},
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
        if not out or out.upper().startswith("UNSUITABLE"):
            return None
        return " ".join(out.split())[:1800]
    except Exception as e:
        log("llm draft failed: %r" % e)
        return None


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
    payload = {"model": env.get("JUDGE_MODEL", "openai/gpt-5"),
               "max_tokens": 1500,  # gpt-5 reasons internally first
               "response_format": {"type": "json_object"},
               "messages": [
                   {"role": "system", "content":
                    "You are a strict reviewer for an agent-work marketplace. "
                    "Given a JOB spec and a proposed RESULT deliverable, judge "
                    "whether the RESULT would earn a PASS verdict from a "
                    "careful validator. Check: every stated requirement is "
                    "covered (including exact required strings/values "
                    "verbatim), the deliverable is concrete and correct (not "
                    "a description of one), no fabricated references or "
                    "guesses about unavailable resources, nothing "
                    "contradicts the spec. Reply JSON only: "
                    "{\"verdict\": \"PASS\"|\"FAIL\"|\"UNSURE\", "
                    "\"notes\": \"one short sentence\"}"},
                   {"role": "user", "content":
                    "JOB:\n%s\n\nPROPOSED RESULT:\n%s"
                    % (job_text[:4000], draft[:2500])}]}
    req = urllib.request.Request(
        "https://nano-gpt.com/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "User-Agent": "flop-ops/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            out = json.loads(r.read())["choices"][0]["message"]["content"]
        d = json.loads(out)
        v = str(d.get("verdict", "UNSURE")).upper()
        if v not in ("PASS", "FAIL", "UNSURE"):
            v = "UNSURE"
        return v, str(d.get("notes", ""))[:200]
    except Exception as e:
        log("llm judge failed: %r" % e)
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
        mt = JOB_RE.match(m.get("text", "").strip())
        if mt and seq > state.get("last_seq", 0):
            new_jobs.append((mt.group(1), mt.group(2)))
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

    triaged = 0
    for jid, body in new_jobs:
        if jid in pending:
            continue
        if len(pending) >= MAX_PENDING or triaged >= 5:
            break
        cat = body.split("|", 1)[0].strip() if "|" in body else "-"
        draft = llm_draft(env, body)
        if draft is None:
            log("job %s skipped (unsuitable or llm unavailable)" % jid)
            continue
        verdict, notes = llm_judge(env, body, draft)
        if verdict == "FAIL":
            log("job %s draft rejected by judge: %s" % (jid, notes))
            continue
        tag = "vetted" if verdict == "PASS" else "borderline: " + notes
        tok = secrets.token_hex(12)
        pending[jid] = {"ts": now, "cat": cat,
                        "title": body[:200], "draft": draft,
                        "judge": verdict, "judge_notes": notes,
                        "tok": tok, "sent": False}
        link = "%s?job=%s&tok=%s" % (APPROVE_BASE, jid, tok)
        send(env, "NEW %s — %s [%s]" % (jid, cat, tag),
             "Job %s\nCategory: %s\nJudge: %s — %s\n\n%s\n\n"
             "--- DRAFT RESULT ---\n%s\n"
             "\n---\nAPPROVE (claim + deliver):\n%s\n\n"
             "Link is single-use; job entry expires in 4h."
             % (jid, cat, verdict, notes, body[:1200], draft, link))
        log("job %s triaged+emailed (cat=%s judge=%s)" % (jid, cat, verdict))
        triaged += 1
        state["pending"] = pending
        save(state)  # persist each job before the next slow LLM call

    state["pending"] = pending
    save(state)


if __name__ == "__main__":
    main()
