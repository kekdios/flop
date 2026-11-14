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
- **FIRST INBOUND SESSION (2026-11-14 ~04:04 UTC)**: external agent
  `5CPwUPRRTpxgBCwCDQwEHT5njNAQurxRiEW7fdWZ9nBW9MoF` opened a payment
  channel to our miner — escrow 0.0038 FLOP LOCKED, spot market, Soft
  tier, our model hash, `open_until_timeout`, still open. First-ever
  non-self activity on the demand path, ~10 days pre-testnet (likely
  protocol/agent probing after Hayes's retail recruitment wave).
  `chain.work.inFlight` counts in-flight work obligations INSIDE open
  channels (pulsed 0→1→0 four times overnight while the session stayed
  open — items enter then expire/leave without commit); settled and
  committed still 0. Watch for committed/settled transitions = actual
  processed work. Work-sig emails now label correctly
  (settled/committed/inFlight) — previously mislabeled as
  "calibration: 1 -> 0" because notify.py labels slots positionally.
- **Serving nuance (2026-11-13)**: worker logs declare capacity
  ("capacity rate: 1 fits 4 sessions on cap 3") but `/session/info`
  reports `is_ready: false`, `capacity_sessions: null` — appears to be
  "accepting right now" vs "declared" semantics, not an outage (chain
  `is_active`, calibration valid to block ~4.18M). Don't panic on
  is_ready:false alone; check worker logs for the capacity-rate line.
- **Ghost tasks** = the protocol's own synthetic work: after registration
  the core host fires an encrypted burst of micro-benchmarks at the new
  miner (X25519 ghost key decrypts them via the mailbox on the miner
  port), each scored vs `gn_weight_expected`. Ours: 72 tasks, all
  completed, blocks 3535729–3535939 → produced the "64 verified jobs →
  cap 3 GFLOP/s" calibration. The burst is per-miner but the mailbox is
  global — nonces are a shared counter (ours now in the millions) and a
  trickle still reaches us post-calibration (~16/24h on 11-13), not zero.
  Explorer API: `/api/ghost-tasks` (no target filter — paginate). This
  is the ONLY work flowing: ~6,787 payment sessions exist but only 6
  ever spent (0.774 FLOP total); proofs24h 0. Ghost tasks ≠ paid work.
- **Testnet readiness notes (from devnet calibration)**:
  - The ghost-task burst fires ~5 min after registration and runs
    ~3.5 min — no warning, one shot. Register ONLY with a fully
    healthy stack: current worker image (check releases.json FIRST),
    model loaded, ghost key present, endpoint ad live.
  - Devnet forgave us (burst ran on the soon-stale v0.1.238) —
    testnet likely won't. Verify image ≥ releases.json minimum before
    registering, not after.
  - Our devnet rate: 72 tasks → 64 verified (~89%) → Soft tier,
    cap 3. Track verification rate on testnet; it sets tier/cap.
  - Re-registration (migration/blue-green) probably re-fires the
    burst — treat a cutover as a calibration event too.
- **Mining status page**: `/mining.html` ← `flop-mining.py` collector
  (`/usr/local/lib/flop-ops/`, cron `*/5`) → `/var/www/agentchainlab/
  mining.json`. Pulls auth'd `/session/info`, worker log events,
  sponsor legs, containers, GPU. Backup of miner identity (signer,
  miner-key.json, session-keys/enclave.seed, ghost-x25519.json —
  lives in docker volume `flop-miner-01_worker-data`, decrypts
  ghost-task verifications): `/var/lib/flop-ops/miner-identity-backup/`
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
- **GPU box RETIRED 2026-11-14**: vast.ai instance `60277620` (A4000)
  destroyed. True cost discovered ~$0.208/hr ≈ $5/day ≈ $150/mo —
  invoice showed GPU $0.0787/hr + **storage $0.1296/hr** (100GB disk at
  premium rate, 14% used; vast bills allocated, can't shrink). Demand
  measurement answer: near-zero BTC traffic (74 vLLM reqs/46h incl.
  health checks + our own fallback calls), $0 settled earnings.
  Server-side `pending: 0.006166 BTC` — CANNOT be real earnings at our
  volume; unit-ambiguous artifact, ask support alongside the ₿ ticket.
  - Reprovision recipe (if gpt-oss demand appears): cheap-storage host,
    ~50GB disk, vLLM gpt-oss-20b flags (16GB TIGHT — 13.8 GiB weights):
    `--max-model-len 4096 --max-num-seqs 4 --gpu-memory-utilization 0.97
    --enforce-eager --tool-call-parser openai` (~30 min end to end).
  - numes agent v0.0.51: self-contained 95MB at
    `~/.local/share/numes/current/numes`; profiles portable via
    `~/.config/numes/provider/profiles/<name>/` (profile.toml/json +
    secrets/transport-enrollment.secret = endpoint identity; daemon
    refuses to start unless the WHOLE config chain is mode 700).
- **Offer DELISTED 2026-11-14**: gpt-oss-a4000 endpoint removed via
  `numes provider profile remove --profile gpt-oss-a4000 --confirm`
  (remote_state: deleted; earnings/history preserved server-side).
  Numery API endpoint-delete can 500 transiently — retry worked.
  (Historical: offer was BTC-indexed USD anchors, $0.03/$0.14 per 1M,
  parity with Numes-hosted; went live 11-12, 329ms TTFT 2nd of 4
  providers. or-tail flapped ~55-66 reconnects/hr throughout while
  staying `connected` — routing-rank drag to watch on its new host.)
- **Human login expires ~72h (2026-11-14)**: the device-flow sign-in
  rolls a refresh credential that died Nov 14 ~11:40 UTC → cloud
  `provider status`/`remote` commands hang on a device-auth prompt.
  DAEMONS KEEP SERVING (process-local tokens; sessions stayed
  `connected`). Monitoring now uses `provider status --profile X
  --local` (works unauthenticated; exposes sessionState/sourceHealth/
  reconnects but not platform listings). Re-auth = user approves a
  device code at auth.numes.ai (generate via `numes auth login
  --login-method device`) — restores earnings/remote/platform queries.
  **Re-authed 2026-11-14 ~13:48 UTC → next expiry expected ~Nov 17**;
  when `mgmt_auth` flips to "expired" just rerun the device flow.
  **Credential is single-chain rotating**: a copy works until the next
  rotation elsewhere invalidates it (~minutes if daemons share the
  chain) — don't copy human-auth between hosts as a live credential;
  also endpoint-deletion-class ops may demand a FRESH approval even
  when reads pass.
  (Longer-term: investigate whether daemons can run on a non-expiring
  credential so this stops being a ~3-day chore.)
  Collector exposes `mgmt_auth` ("signed_in:device" vs "expired" —
  `auth status` top-level stays "local_only" even when authed; watch
  `signed_in_methods`). Also:
  api.numes.ai 403s default `Python-urllib` UA — collector sends a UA
  header now (same class of block as the FLOP faucet 403).
- **Human login expires ~72h (2026-11-14)**: the device-flow sign-in
  rolls a refresh credential that died Nov 14 ~11:40 UTC → cloud
  `provider status`/`remote` commands hang on a device-auth prompt.
  DAEMONS KEEP SERVING (process-local tokens; sessions stayed
  `connected`). Monitoring now uses `provider status --profile X
  --local` (works unauthenticated; exposes sessionState/sourceHealth/
  reconnects but not platform listings). Re-auth = user approves a
  device code at auth.numes.ai (generate via `numes auth login
  --login-method device`) — restores earnings/remote/platform queries.
  **Re-authed 2026-11-14 ~13:48 UTC → next expiry expected ~Nov 17**;
  when `mgmt_auth` flips to "expired" just rerun the device flow.
  (Longer-term: investigate whether daemons can run on a non-expiring
  credential so this stops being a ~3-day chore.)
  Collector exposes `mgmt_auth` ("signed_in:device" vs "expired" —
  `auth status` top-level stays "local_only" even when authed; watch
  `signed_in_methods`). Also:
  api.numes.ai 403s default `Python-urllib` UA — collector sends a UA
  header now (same class of block as the FLOP faucet 403).
- **Reseller — SOLE OFFER since 2026-11-14**: profile `or-tail`,
  `openrouter` source kind, provider AgentChainLab. Sells 3 TheDrummer
  RP fine-tunes absent from the Numes catalogue (scarce-tail play),
  all BTC-indexed: unslopnemo-12b $0.52/$0.52, cydonia-24b-v4.1
  $0.39/$0.65, skyfall-36b-v2 $0.72/$1.04 (~30% over OR list).
  **Moved to the ops droplet 2026-11-14** (no GPU needed — pure OR
  passthrough): systemd `numes-or-tail.service` (enabled, Restart=
  always) → `numes provider daemon --profile or-tail`; profile at
  `/root/.config/numes/provider/profiles/or-tail/`; OR key =
  dedicated `numes-hosted` $5-capped key in `/etc/numes-or.env`
  (mode 600, EnvironmentFile). numes binary at `/root/.local/bin/numes`
  (v0.0.51). Endpoint `e79e325f-…` stayed `active/healthy` through the
  move — identity is the enrollment secret, not the host/IP.
  CLI: `setup openrouter` needs uniform --prompt-price; per-model prices
  get edited in the TUI (`provider tui`, needs ssh -tt) + `p` publish.
- Numery keys in `~/.flop/numes.env`: NUMES_API_KEY (USD inference,
  catalogue reads), NUMES_MGMT_KEY (management), NUMES_BTC_KEY (BTC
  inference, customer-side loop testing later; ALSO on droplet in
  /etc/flop-ops.env for the collector).
- **Status page**: `/numes.html` — collector `site/bin/numes-status.py` →
  `/usr/local/lib/flop-ops/numes-status.py`, cron `*/10`, writes
  `/var/www/agentchainlab/numes.json`. Since 11-14 it checks the local
  droplet daemon (`--local` status + `auth status`); NUMES_SSH_* vars in
  flop-ops.env are stale (vast key kept at `/root/.flop/vast_ed25519`
  for future reprovisioning).
  Catalogue data is BTC-key-scoped — BTC keys see ONLY BTC-priced models
  (**18 as of 2026-11-14**; our 3 thedrummer resells marked `our_listed`).
- Caveats: only BTC-key customers can buy (small pool); numes admin
  review is manual — days possible. OR spend is wholesale cost —
  or-tail loses money if OR raises prices under our fixed markup.
- **Display bug (2026-11-13, reported)**: Numes "Prices BTC" view renders
  SATOSHIS with the ₿ glyph — our $0.03/$0.14 USD-indexed listing shows
  as "₿42 / ₿197". ₿ means whole BTC everywhere else; reads ~8 orders
  of magnitude too high to BTC customers. Our pricing is correct (USD
  anchor, sats float); the bug is their unit label. If a customer or
  agent reads the board in BTC view, this is why it looks insane.

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
  5 addresses in 4 days (5DUAJj3Y → 5GpjXEBu → 5EkGcLZf → 5DcAhExf →
  5Dhu1vbU as of 2026-11-14; re-check via action:"address"). Grants
  claimed to a rotated-out address STRAND there (custodial keys are
  bridge-side; transfer caps at 0.001; ~100 FLOP stranded across 4
  retired addresses — if rotation keeps a daily pace, consolidate
  stale-* cards into one "retired bridges" row). Bridge faucet
  action disabled.
  chat → devnet gateway models = the demand path to miners
  (catalog GREW 2026-11-14: qwen2.5-0.5b-instruct + qwen3-14b +
  qwen3-coder-30b-a3b-instruct-fp8 — bigger miners onboarding
  pre-testnet; arete-27b appeared transiently 11-13. Per-DID ~5s
  rate limit; 401 persists EVEN WHEN FUNDED — funding was not the
  gate, treat 401 as gateway-side auth failure; "daily cap" =
  per-DID quota; gateway also 502s/times out — zero successful
  chats ever logged). **flop-chat.py** (droplet cron
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
  nano-gpt error/empty the drafter retries against OpenRouter
  (DRAFT_FALLBACK_URL → `openai/gpt-oss-20b`, DRAFT_FALLBACK_KEY in
  flop-ops.env; moved off the retired vLLM box 2026-11-14) — only
  fires on primary failure.
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

## FLOP chain stack notes
- **Leadership (verified 2026-11-13 via docs.flop.network + press)**:
  Arthur Hayes is founder/CEO day-to-day — his ONLY active operating
  project. Maelstrom (his family office) is the SOLE funder: no
  presale, no VC, no pre-mine, 2+ yr runway; Labs' exposure = 8
  FLOP/block mint shared with team. CTO Sergey Vidyuk (ex-BitMEX infra),
  CSO Shu Duan (ex-DB). Thesis: compute oversupply + agents need a
  non-freezable payment rail (anti-stablecoin argument).
  **Testnet Nov 24, 2026; airdrop Q4 2026; genesis Q1 2027.** Fair
  launch ⇒ mining IS the token distribution ⇒ retail recruitment
  (consumer GPUs, Mac Minis) is structural, not charity. Our devnet
  work (registration, calibration, Kibble attests) is the attested
  position that converts to allocation.
- **TESTNET MIGRATION (docs: flop.finance/testnet)**: chain state NOT
  migrated — only the airdrop allocation carries; testnet = fresh
  registration on a new chain. Devnet FLOP is test-scrap; testnet
  tokens are likewise valueless — attested WORK is the score.
  Sponsored stake continues on testnet; miner admission via hardware
  attestation OR application list (we have both). Testnet runs ~90d;
  snapshot = ONE published finalized block height (no rolling), then
  review/dispute window → genesis. Minimum-activity floors will be
  published with testnet rules — sustained uptime through the window
  is part of the score. Validator spec (provisional): 8+ core CPU,
  64GB RAM, 4TB NVMe — droplet can't carry it; deferral stands.
  DAY-1 RUNBOOK: (1) fetch testnet pack/env + RPC endpoint docs;
  (2) repin worker/backend images per releases.json testnet keys;
  (3) copy identity files (signer/ghost/session/enclave — SAME
  identity, attestation continuity); (4) sponsorship grant — retry
  cron pattern ready; (5) register ONLY with fully healthy stack —
  calibration burst re-fires ~5min in, ONE shot; (6) `flop model
  onboard` for new chain hash (old hash does NOT carry);
  (7) republish endpoint ad; (8) repoint collectors to testnet
  explorer/RPC. Estimated: hours, gated by FLOP-side releases.
- **Validator landscape (verified 2026-11-13)**: separate role from
  miners — consensus/verification, NO GPU needed (Mac Mini viable;
  the retail-pitch door). Economics: inference fees split 85% miner /
  15% validator (validators earn a cut of ALL network inference, not
  just their own work) + share of the 96 FLOP/block role rewards.
  FLOP takes 0%. Stake floor 1,200,000 FLOP (not faucet-reachable —
  entry = sponsored grant or delegation). Devnet set: 14 total —
  11 at ~2.9M self-stake (institutional cluster), 2 at the 1.2M
  floor, 1 sponsored at 1.32M. Delegation/nomination exists
  (delegatedStake field, one live delegator). Sponsor path is built
  into our own pack: sponsor-grant.sh GRANT_ROLE=validator +
  sponsor-request.mjs accepts a validator account.json ({secretSeed,
  ss58Address}). RULE: never mine on a validator account — "a
  validator that mines becomes the whole committee" (miner.env).
  Validator apps ~4k vs miner ~9k — less crowded track; a sponsored
  validator node on the droplet = second attested position.
  Validator rewards = 10% of the 96-FLOP block reward (9.6/block,
  ~830k/day split across the set) + 15% inference-fee leg once
  implemented (chain currently pays miners 99%, 1% audit — issue
  #1352).
  **DEFERRED (decided 2026-11-13)**: not pursuing a validator seat
  pre-testnet — miner + Numes monitoring load is enough, and testnet
  launch chaos is the wrong time to add a new role's failure modes.
  No validator artifact is published yet anyway (releases.json has
  only cli/miner-worker/miner-vllm-toploc — watch for a node/validator
  key appearing). Revisit after testnet settles: path = validator
  application → sponsored grant (1.2M floor is far beyond faucet
  funds; self-stake unrealistic) or delegation if the floor accepts
  it. Host: droplet or dedicated VPS, Substrate node, no GPU.
- **Revenue model (published 2026-11-14, flop.finance/intro/revenue)**:
  emission 96 FLOP/block → 75% miners (~6.2M/day ÷ verified Gn share),
  10% validators, 10% agents/brokers + 5% community stakers (both
  undistributed), +16/block dev subsidy. Rewards = H100-eq weight ×
  network share, so block income exists even at zero demand; session
  income stacks on top at 85% miner share. Genesis pool 4.4B; Season 0
  airdrop hands out 3.76B — allocations SCORE-BASED (devnet/testnet
  attested work = score). Base case anchor: $100M network value on
  1,200 H100-eq → ~$0.22/FLOP (only 0.46B circulating at TGE);
  network 1,200→3,000 H100-eq by month 24. Preset weights: H100 1.0,
  B200 2.28, MI300X 1.32, 5090 0.21, 4090 0.17, 3090 0.07,
  **Mac mini M4 Pro 0.001** (~$1/day at anchor — retail mining is
  real but tiny). Our A4000 ≈ 0.07 → ~360 FLOP/day at anchor (~$80
  vs $3.12 cost — all hypothetical; token price + network size are
  planning inputs, not forecasts). Their honest disclosures: session
  income "not observed demand", a fixed 3.4× overstatement bug,
  no demand-saturation modeling.
- Devnet self-identifies as **zkVerify** (chain properties) — FLOP is
  built on Horizen Labs' zkVerify runtime (ZK-proof-verification
  Substrate chain) + custom pallets (sessions, HTLCs, miner staking,
  ghost tasks). Runtime spec numbers (1014020 etc.) are zkVerify's
  versioning. Talisman custom-network config: RPC
  `wss://rpc.devnet.flop.network`, symbol FLOP, 18 decimals, explorer
  `https://explorer.devnet.flop.network`, testnet ✓, CheckMetadataHash
  unchecked. Talisman ADD failed in practice (dedups by chain identity
  — merged into its built-in zkVerify entry / refused the custom add);
  doesn't matter: extension still signs by genesis hash. Polkadot.js
  Apps is the fallback UI for custom endpoints.

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
- **NEAR multichain account (2026-11-12)**: user created mainnet NEAR
  account `agentchainlab.near`. Via NEAR chain signatures (MPC
  `v1.signer.derived_public_key`, domain_id 1) it controls a derived
  FLOP account `5EZK4Z6jrNBw41X52P1X11aYzLjEmAfE3bAcjBheJ18KMiRw`
  (verified: valid SS58, liveBalance on-chain). **The NEAR recovery
  phrase is a FLOP key** — custody sits user-side in their NEAR
  wallet. Faucet cron claims for it nightly (MULTICHAIN_ADDR in
  flop-ops.env); wallet page tracks it as "multichain". Distinct from
  the Talisman wallet 5H1E2XQ3 (different key, different account).
- **Cross-chain swap mechanics (verified 2026-11-13)**: Talisman CAN
  sign FLOP extrinsics directly (no custom-network needed — extension
  signs by genesis hash). Explorer cross-chain page → New swap →
  maker = funded FLOP acct (5H1E2XQ3), counter = agentchainlab.near.
  Pallet: `HasStation.create_cross_chain_htlc`. RULE:
  FLOP-leg timelock must cover the NEAR leg's window —
  `TimelockSymmetryViolation` if too short; 2h fails, **24h works**.
  Failed dispatch still burns nonce+fee. HTLC auto-refunds at timeout
  if the NEAR leg never completes (NEAR leg = real mainnet NEAR, so
  refund is the free dry-run outcome). First offer: 10 FLOP locked
  under hash-lock 97655037… (tx 0x2cf55ce5) — visible on our wallet
  page via the account's `reserved` field; `/api/htlcs` does NOT cover
  cross-chain swaps (it indexes session-payment HTLCs only — 6,776 of
  them; swap = +1 on top, unlisted). UI "Recent FLOP_NEAR swaps" is
  indexer-fed (lags); the page's Contracts tab is the authoritative
  view for an open offer. Timelock rule backed by
  stats.finality.foreignTimeoutMargin.recommendedMinimumMarginBlocks
  = 3605 (~1h): foreign-chain clocks can't be frozen by FLOP safe
  mode, so the FLOP leg must outlive the NEAR leg + margin.

## Explorer indexer vs chain (2026-11-13 episode)
- The explorer API + faucet run off a shared **indexer** that can fall
  behind the chain. Overnight it stalled ~5,720 blocks (~2h) behind:
  swap lists empty, faucet `indexer_stale`/unavailable, pendingGrants
  frozen at 589 — while the chain itself was perfectly healthy
  (15 peers, finalizedLag 5, ~1.2s blocks). **Rule: explorer lag ≠
  chain trouble.** Check `/api/stats`: `latestBlockNumber` (indexed
  position) vs `finality.bestBlock` (chain head) — the gap is the
  indexer backlog. Chain-direct reads (account `free`/`reserved` via
  WS RPC) were never affected; our wallet page's live path bypasses
  the indexer entirely.
- `/api/stats` population numbers (don't estimate from API pages —
  they cap ~20; refreshed 2026-11-13): `registeredMiners` **305**,
  `activeAgents` **13,921**, `totalAccounts` 35,222,
  `activeValidators` 13, `flopLocked` ~630 FLOP, `txVolume24h` ~211k.
  Crowded devnet, zero spend — the demand bottleneck is economic, not
  headcount. (Indexer lag currently 0 — the 11-13 stall resolved.)
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
