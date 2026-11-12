# FLOP Devnet Miner Ops — Agent Handoff

Read this first in any new session. It maps the whole setup so you don't have to rediscover it.

## What this repo is
Ops repo for the AgentChainLab FLOP devnet miner. Contains the public status site
(`site/`) and a verified mirror of the official miner pack (`pack/`). Deployed
artifacts live on the ops droplet; this repo is the source of truth for scripts.

## Infrastructure map
- **Ops droplet** `agentchainlab` = 152.42.168.173 (DigitalOcean, Ubuntu, nginx).
  SSH: `ssh agentchainlab` (root, key `~/.ssh/id_ed25519`). Was previously named
  "yumibot" — old project; also still serves solvequest.io (keep, user wants it up).
  - nginx sites: `/etc/nginx/sites-enabled/{agentchainlab,flop-miner,solvequest}`
  - Site root: `/var/www/agentchainlab/` (index.html is generated — edit
    `index.html.tpl` + `diary/` fragments, rebuild with `bin/build.sh`)
  - Config: `/etc/flop-ops.env` (mode 600) — VM_SSH_*, SPEND_SO_FAR, ENDPOINT,
    RESEND_API_KEY, ALERT_TO, ALERT_FROM
- **GPU miner** = gpu.ai instance `flop-miner-01` (gpu-801118f4), RTX A4000 16GB,
  eu-west, $0.13/hr.
  - SSH: `ssh -i ~/.flop/vast_ed25519 -p 10101 root@frp.gpu.ai`
    (needs `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`)
  - Miner dir: `/opt/flop-miner/` — miner.env, signer, models/, sponsor scripts
  - Containers: `flop-miner-01-worker-1`, `flop-miner-01-backend-1` (vLLM TOPLOC)
  - API: `curl -H "Authorization: Bearer $GPUAI_API_KEY" https://api.gpu.ai/v1/instances`

## Current state (2026-11-12)
- **SPONSORED + LIVE**: capacity expanded 256→1024 overnight; `*/30`
  sponsor-retry cron caught the window ~06:30 UTC. Stake (~35,000 FLOP) +
  ops (250) legs settled on-chain (see /var/log/flop-sponsor.log on the
  box; amounts are raw/1e18).
  Miner registered, calibrated (cap 3 GFLOP/s from 64 verified jobs),
  serving 4-session capacity, chain-settle ON, endpoint ad published.
- Worker upgraded v0.1.238→**v0.1.336** — devnet runtime moved to spec
  1014020 which the old SDK can't read ("Unsupported runtime spec
  version"; ≤1014014 qualified). The stale image also caused the "miner
  signer is fenced after an unclassified submission failure" GPU-inventory
  error. ALWAYS check https://flop.network/releases.json for new pins when
  the worker misbehaves (faucet can also move the `min` floor). miner.env
  pins by digest; refresh the box's releases.json from the same URL.
  collect.sh tracks BOTH miner-worker drift AND cli.latest in the alert
  signature — a cli bump emails because it may unblock `flop chat`/
  `declare-gpu` (cli 0.1.321 can't speak runtime 1014020).
- Miner cron `*/30` runs `/opt/flop-miner/sponsor-retry.sh` — auto-fires
  `sponsor.sh` when `sponsor.miner.status` reads `open` (vocab now also
  has `automatic_full`; capacity can sell out again so keep the retry).
  `*/3` runs `health.sh`.
- Miner address: `5DLMZtB5it7s6DZRmd98ABzfY8zPx9JBAAcvCtgXTL4ttSkC`
- **Ghost tasks** = the protocol's own synthetic work: after registration
  the core host fires an encrypted burst of micro-benchmarks at the new
  miner (X25519 ghost key decrypts them via the mailbox on the miner
  port), each scored vs `gn_weight_expected`. Ours: 72 tasks, all
  completed, blocks 3535729–3535939 → produced the "64 verified jobs →
  cap 3 GFLOP/s" calibration. They arrive in a per-miner burst then
  stop (recent thousands all target other calibrating miners). Explorer
  API: `/api/ghost-tasks` (no target filter — paginate). This is the
  ONLY work flowing: ~7000 payment sessions exist but only 6 ever
  spent (0.774 FLOP total); proofs24h 0. Ghost tasks ≠ paid work.
- **Mining status page**: `/mining.html` ← `flop-mining.py` collector
  (`/usr/local/lib/flop-ops/`, cron `*/5`) → `/var/www/agentchainlab/
  mining.json`. Pulls auth'd `/session/info`, worker log events,
  sponsor legs, containers, GPU. Backup of miner identity (signer,
  miner-key.json, session-keys): `/var/lib/flop-ops/miner-identity-backup/`
  on the droplet + `~/.flop/miner-backup-5DLM/` locally.
- User submitted the FLOP miner interest form (operator-grant admission path).
- **Kibble participation started 2026-11-10**: FLOP Labs' pre-genesis
  useful-work board (attested work = airdrop IOU, "practice for the FLOP
  loop"). DID minted, 2 CLAIMs + 2 correct RESULTs on the tape (Ed25519
  review jobs k536028655d + k28b09efc49). Ongoing: keep claiming
  substantive jobs, ATTEST others' delivered work (needs our franchise =
  ≥1 scored RESULT), daily MEM_PUT once franchised. Score:
  `~/.flop/kibble.py score`.

## Numes provider probe (started 2026-11-11)
- Separate demand-measurement experiment: serve inference on numes.ai
  marketplace, BTC-settled (Stripe USD payouts impossible from Ecuador).
- **Box**: vast.ai instance `60277620` (contract), RTX A4000 16GB, Quebec,
  ~$0.085/hr all-in (~$2/day on vast credit).
  - SSH: `ssh -i ~/.flop/vast_ed25519 -p 40549 root@172.97.226.219`
    (needs `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`;
    proxy alt: `-p 27640 root@ssh5.vast.ai`). If IP/port change, re-pull:
    `~/.flop/venv/bin/vastai show instance 60277620 --raw`
  - vLLM serves `openai/gpt-oss-20b` (mxfp4/MARLIN) on :8000, mapped
    publicly at 172.97.226.219:40524. Working flags (16GB is TIGHT —
    weights 13.8 GiB): `--max-model-len 4096 --max-num-seqs 4
    --gpu-memory-utilization 0.97 --enforce-eager --tool-call-parser openai`.
    Higher ctx/lower util = KV OOM. `/root/start-vllm.sh` restarts it;
    `/root/onstart.sh` is boot config (vLLM + numes daemon).
  - numes agent v0.0.50 at `/root/.local/bin/numes`, daemon runs
    `numes provider daemon --profile gpt-oss-a4000` (detached, PPID 1).
- **Offer**: provider "AgentChainLab", listing openai/gpt-oss-20b,
  `--currency BTC-indexed` (USD anchors, sats payout), $0.03/1M prompt,
  $0.14/1M completion = parity with Numes-hosted USD offer. **LIVE as of
  2026-11-12** — approved, verified, routable. Marketplace stats: 100%
  availability (sparse samples), 329ms TTFT — 2nd fastest of 4
  providers (AnonInference 261ms BTC-only, Numes 1.6s, amazee 4.2s).
  Agent note: ~1 reconnect/min on the outbound session — watch it,
  routing rank is measured health/latency. Check: `numes provider
  status --profile gpt-oss-a4000` on the box.
- **Reseller experiment (2026-11-12)**: second profile `or-tail` on the
  same box — `openrouter` source kind, same AgentChainLab provider,
  second endpoint. Sells 3 TheDrummer RP fine-tunes absent from the
  Numes catalogue (scarce-tail play), all BTC-indexed: unslopnemo-12b
  $0.52/$0.52, cydonia-24b-v4.1 $0.39/$0.65, skyfall-36b-v2 $0.72/$1.04
  (~30% over OR list). OR key = dedicated `numes-hosted` key, $5-capped,
  stored in `/root/.numes-or-env` (mode 600) — daemon needs it via env.
  CLI: `setup openrouter` needs uniform --prompt-price; per-model prices
  get edited in the TUI (`provider tui`, needs ssh -tt) + `p` publish.
  Endpoint **approved + active ~17:40 UTC same-day**; transport flapped
  while pending_approval (normal), session settled to connected on
  approval. Provider board now shows 4 models / 2 endpoints.
  onstart.sh runs both daemons. `numes-status.py` collects both
  profiles → /numes.html shows the reseller card + marks all our
  models in the BTC catalogue table.
- Numery keys in `~/.flop/numes.env`: NUMES_API_KEY (USD inference,
  catalogue reads), NUMES_MGMT_KEY (management), NUMES_BTC_KEY (BTC
  inference, customer-side loop testing later; ALSO on droplet in
  /etc/flop-ops.env for the collector).
- **Status page**: `/numes.html` — collector `site/bin/numes-status.py` →
  `/usr/local/lib/flop-ops/numes-status.py`, cron `*/10`, writes
  `/var/www/agentchainlab/numes.json`. SSH key for the probe box:
  `/root/.flop/vast_ed25519` on the droplet; NUMES_SSH_* in flop-ops.env.
  Catalogue data is BTC-key-scoped — BTC keys see ONLY BTC-priced models
  (14 frontier-scale models at probe start; our gpt-oss-20b would be the
  only sub-frontier BTC offer once approved).
- Caveats: only BTC-key customers can buy (small pool — we were the only
  BTC offer at scan time); tool capability needs vLLM's tool-call parser
  (without it validation fails `invalid_tool_call`); numes admin review
  is manual — days possible. Destroy instance if probe is uneconomical:
  `~/.flop/venv/bin/vastai destroy instance 60277620`.

## Ops-site alerting pipeline
- `site/bin/collect.sh` → deployed `/usr/local/lib/flop-ops/collect.sh`, root cron
  `*/3`. SSHes to the miner, writes `/var/www/agentchainlab/status.json`.
- On any state-signature change (`miner|gpu|calibration|sessions`) it calls
  `site/bin/notify.py` → deployed `/usr/local/lib/flop-ops/notify.py`, which emails
  ALERT_TO via the Resend API. State file: `/var/lib/flop-ops/last-sig`;
  log: `/var/log/flop-ops-notify.log`. First run seeds silently.
- To test email manually on the droplet:
  `/usr/local/lib/flop-ops/notify.py "ok|ok|not calibrated|idle" "ok|ok|test|live"`

## Chat window + ask backend
- `/chat.html` — live read-only feed from technocore.chat (HTTP-native agent
  chat). nginx proxies `/chat/api/*` → `technocore.chat/*` (same-origin, no CORS).
- `/chat/ask` → `askd.py` on `127.0.0.1:8577` (systemd `flop-ask.service`), which
  forwards {room, question, messages} to nano-gpt OpenAI-compatible API with the
  key held server-side. Repo copies: `site/bin/askd.py`, `site/bin/flop-ask.service`.

## Secrets (locations only — never commit, never print values)
- `~/.flop/gpuai.env` GPUAI_API_KEY · `~/.flop/vast.env` VAST_API_KEY ·
  `~/.flop/alerts.env` RESEND_API_KEY + ALERT_TO + ALERT_FROM
- `~/.flop/vast_ed25519` — SSH key for the gpu.ai miner
- `~/.flop/kibble.json` — kibble DID identity (did:key:z6MkgjCi…NQNNMt) +
  seed_hex for signing; `~/.flop/kibble.py` is the worker CLI
  (score/board/cycle/claim/result/attest/tape — see its --help docstring)
- **Kibble→FLOP bridge**: `POST kibble.world/api/flop-devnet` with
  {action:status|address|faucet|chat|balance|transfer, seed_hex}. Our DID's
  assigned devnet address ROTATES FREQUENTLY without moving balances —
  3 addresses in 2 days (5DUAJj3Y → 5GpjXEBu → 5EkGcLZf as of
  2026-11-12; re-check via action:"address"). Grants claimed to a
  rotated-out address STRAND there (custodial keys are bridge-side;
  transfer caps at 0.001). Bridge faucet action disabled.
  chat → devnet gateway models = the demand path to miners
  (serves qwen/qwen2.5-0.5b-instruct; per-DID ~5s rate limit;
  401 = unfunded address; "daily cap" = per-DID chat quota independent
  of balance — retry after reset). **flop-chat.py** (droplet cron
  `23 */4`) now SELF-FUNDS: resolves the current address → if free<5
  FLOP claims the anonymous 25 FLOP grant onto it → waits → chats.
  This collapses the rotation window to seconds (previously the 22:15
  faucet cron claimed hours ahead and grants stranded). flop-faucet.sh
  still runs nightly (Talisman wallet top-up) but bridge-side is
  superseded by flop-chat.py's fund-then-spend. Faucet needs
  User-Agent header (403 without). Log: /var/log/flop-chat.log.
  Rule: poster/worker/validator must be 3 different parties — never
  self-claim own JOBs.
- **Kibble job pipeline (2026-11-12, STOPPED)**: `flop-kibble-jobs.service`
  stopped+disabled after the 30-min auto window concluded — 51 fired,
  45 tape-verified, 2 useful attests, score API still 0 (`found:false`).
  Race structurally unwinnable vs ~1s bots at serial triage latency
  (median JOB→CLAIM 764s). Re-enable: `systemctl enable --now
  flop-kibble-jobs`. Ledger preserved at `/var/lib/flop-ops/kibble-jobs.json`
  (~1551 stale backlog entries remain). Pipeline detail:
  `kibble-jobs.py` runs as a
  **systemd daemon** (`flop-kibble-jobs.service`, flock-guarded, ~6s
  poll) — replaced the old `*/2` cron, which is commented out in
  `/etc/crontab`. Feed is incremental: `?since=<last_seq>&limit=200`
  paged every pass, plus a mid-triage refresh every ~15s during LLM
  batches so last_seq keeps advancing and no CLAIM/RESULT/ATTEST lines
  are missed (the old 80-msg latest-window missed attests entirely).
  `KIBBLE_ONCE=1` runs a single pass for manual/debug use.
  Flow per job: `openai/gpt-5-mini`
  (ASK_MODEL) drafts a RESULT for in-wheelhouse jobs →
  `anthropic/claude-sonnet-4.6` (JUDGE_MODEL) judges it with a
  Success:-line checklist rubric → PASS/UNSURE jobs email a single-use
  approve link → `kibble-approve.py` (systemd `flop-kibble-approve`,
  127.0.0.1:8578, nginx `/kibble-approve`) fires detached CLAIM+RESULT.
  FAIL drafts are dropped (logged with reason). Draft failover: on
  nano-gpt error/empty the drafter retries against our vast.ai vLLM box
  (DRAFT_FALLBACK_URL → `openai/gpt-oss-20b`, :40524) — only fires on
  primary failure to keep Numes demand measurement clean.
  - **Auto-approve**: `KIBBLE_AUTO_UNTIL` (epoch) enables bounded
    judge-PASS auto-delivery — env-tunable `AUTO_PER_RUN`/`AUTO_PER_HOUR`
    (currently 15/150), 3s stagger, `NOTIFY_OFF=1` suppresses Resend
    sends while logging them; `AUTO_DROP_UNSURE=1` logs UNSURE drafts
    without occupying pending slots (only during auto mode).
  - **Wire format** (post-2026-11-12 fix): CLAIM = `{"kind":"claim",
    "job_id":jid,"seed_hex":...}`; RESULT = `{"kind":"result","job_id":
    jid,"body":draft,"seed_hex":...}` — free-text `text` payloads get
    `bad job id ''`/`not a kibble-v1 line`. Race reality: winning
    workers claim in 0.3–2s and deliver in 1–6s; our daemon lands
    CLAIM in ~30-60s. Attests observed per-delivery, landing 2–30s
    after DELIVER.
  - State: `/var/lib/flop-ops/kibble-jobs.json` (4h pending expiry,
    7-day ledger with draft+judge_notes+attests for audit). Scorecard:
    `https://agentchainlab.com/kibble.html` fed by the same script's
    stats → `/var/www/agentchainlab/kibble-stats.json`. Logs:
    `/var/log/kibble-jobs.log`, `/var/log/kibble-approve.log`. First run
    seeds seq silently (`seeded` flag). Gotchas: MAX_PENDING counts
    UNSENT entries only — counting sent ones caused a silent stall;
    when `NOTIFY_OFF` is set, non-auto jobs are ledger-only (never
    pended — unreachable links would saturate the cap); untriaged
    jobs persist via state["backlog"] so cap-breaks lose nothing.
  - **Verification layer**: the feed stamps ledger entries when OUR
    CLAIM/RESULT lines are observed on the tape (tape_claimed /
    tape_delivered / our_*_ts, plus first_claim/first_deliver for race
    measurement) — act writes are fire-and-forget, tape is proof.
    `kibble-score.py` (cron */5) retries the flaky /score endpoint and
    persists /var/lib/flop-ops/kibble-score.json + a JSONL history;
    stats merge it into kibble.json for the scorecard (server score,
    tape-confirmed counts, median claim/deliver/attest latencies).
- **Public faucet**: `POST https://faucet.flop.network/fund` with
  {"address":"<SS58>"} → 25 FLOP anonymous grant, 24h cooldown
  per address. Verified-human tier (500-2000 FLOP) needs an EVM wallet
  with Passport/Guild score or Self app proof. Limits/state:
  GET https://faucet.flop.network/limits. Daily auto-claim for the
  bridge address runs on the droplet: `site/bin/flop-faucet.sh` →
  `/usr/local/lib/flop-ops/flop-faucet.sh`, cron `15 22 * * *`,
  log `/var/log/flop-faucet.log` (resolves current address first —
  handles rotation).

## FLOP key/control model (verified 2026-11-11)
- Bridge agent address is **bridge-custodial**: ed25519 pubkey of our
  kibble seed SS58-encodes to `5Cq2JZs8…`, NOT the assigned address —
  the bridge holds the key and signs for us via seed_hex auth. FLOP
  there = inference budget (chat), not withdrawable: transfer action
  caps at 0.001 FLOP. Treat it as spendable compute credit, not a wallet.
- **Keys we DO hold**: miner identity on the GPU box —
  `/opt/flop-miner/signer` + `miner-key.json` (miner address
  `5DLMZtB5…SkC`). That signer is the on-chain miner identity; whoever
  holds it IS the miner. Back it up before any hot-swap.
- **Live FLOP (post-devnet)**: same model — control = key possession.
  Miner earnings accrue to the miner address (its key lives on the
  GPU host; keep `signer`+`miner-key.json` safe). A personal wallet =
  generate our own sr25519/ed25519 keypair in Polkadot.js/Talisman —
  only ever fund addresses whose keys we generated ourselves.
- `~/.config/nanogpt-key` — nano-gpt API key (local copy)
- `/etc/flop-ops.env` on the droplet — full runtime config incl. Resend +
  NANOGPT_API_KEY + ASK_MODEL

## Procedures
- **Blue-green miner swap**: new GPU VM → install pack + copy `miner.env`,
  `signer`, `miner-key.json`, `session-keys/` (signer = on-chain identity) →
  model + containers up + verify → repoint droplet's SSH reverse tunnel for
  flop-miner.agentchainlab.com to the new VM (that flip IS the cutover) →
  verify ad republishes + status.json green → retire old VM after soak.
  ~30–60 min total, mostly model download. Don't run two workers on one
  signer for more than the flip window; in-flight sessions die on cutover.

## Git workflow
- User reviews and pushes via **GitHub Desktop**. Agent should commit locally
  with a descriptive message but never push.
- `.gitignore` already excludes pack tarballs/extracted packs.

## Gotchas
- 152.42.161.97 is gone — the old droplet was destroyed and the IP reassigned to
  an unrelated customer (host-key change was expected, not an attack).
- `droplet` SSH alias → 152.42.220.44 (btcdefimastery.com) intentionally has no
  working key; user said leave it alone.
- `ssh agentchainlab` is the only way onto the ops droplet — yumibot/yumi keys
  were removed.
