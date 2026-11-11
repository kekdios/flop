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

## Current state (2026-11-10)
- Miner software fully up; model `Qwen/Qwen2.5-0.5B-Instruct` BF16 verified
  against on-chain `MODEL_HASH=0x1a52c1…3306`. Endpoint ad published to mailbox.
- **BLOCKED on sponsorship**: faucet returns `429 sponsor_capacity_full`
  (256/256 signer positions). This is devnet-side, not a config problem.
- Miner cron `*/30` runs `/opt/flop-miner/sponsor-retry.sh` — auto-fires
  `sponsor.sh` when `sponsor.miner.status` flips to `open` in
  https://faucet.flop.network/limits . `*/3` runs `health.sh`.
- Miner address: `5DLMZtB5it7s6DZRmd98ABzfY8zPx9JBAAcvCtgXTL4ttSkC`
- User submitted the FLOP miner interest form (operator-grant admission path).
- **Kibble participation started 2026-11-10**: FLOP Labs' pre-genesis
  useful-work board (attested work = airdrop IOU, "practice for the FLOP
  loop"). DID minted, 2 CLAIMs + 2 correct RESULTs on the tape (Ed25519
  review jobs k536028655d + k28b09efc49). Ongoing: keep claiming
  substantive jobs, ATTEST others' delivered work (needs our franchise =
  ≥1 scored RESULT), daily MEM_PUT once franchised. Score:
  `~/.flop/kibble.py score`.

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
  assigned devnet address ROTATES: currently
  `5GpjXEBuS1L8Svrrh9EMDTruwvhNPAquAjjJ4XLAbbe3fR6w` (was
  `5DUAJj3YrFmYj5bbzzJULgrfKeLrVHpuWE1UAEsHPwSYivu9` — re-check via
  action:"address"). Bridge faucet action disabled; funded via public
  faucet instead — balance 25 FLOP (2026-11-11). chat → devnet gateway
  models = the demand path to miners. chat enforces a daily cap (per
  DID or gateway) independent of balance — retry after reset.
  Rule: poster/worker/validator must be 3 different parties — never
  self-claim own JOBs.
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
