# FLOP Devnet Miner Ops

Public operations repo for the **AgentChainLab FLOP miner** — one rented GPU
VM mining on the [FLOP](https://flop.network) devnet, run as cheaply and as
openly as possible.

**Live status board & diary:** https://agentchainlab.com
**Miner endpoint:** https://flop-miner.agentchainlab.com

## Goal

Get a miner sponsored, calibrated, and serving paid compute sessions on FLOP —
starting on devnet (test FLOP, no value) and carrying a proven setup into
testnet/mainnet. Budget ceiling: **$50**. Every step is documented in the
public ops diary on the site, including the mistakes.

## Architecture

```
agents ──► FLOP gateway/mailbox
                │
                ▼
  flop-miner.agentchainlab.com ── nginx + Let's Encrypt ── DigitalOcean droplet
                │                                          ("agentchainlab")
                └────────── SSH reverse tunnel ──────────► gpu.ai VM
                                                             flop-miner-01
                                                        ┌── miner-worker ──┐
                                                        │  (session host   │
                                                        │   :8645, chain   │
                                                        │   settlement)    │
                                                        ├── miner-vllm ────┤
                                                        │  TOPLOC backend  │
                                                        │  RTX A4000 16GB  │
                                                        └──────────────────┘
```

- **GPU host:** gpu.ai KVM instance `gpu-801118f4` — RTX A4000, eu-west,
  $0.13/hr. Runs the official miner pack: `miner-worker` + `miner-vllm-toploc`.
- **Model:** `Qwen/Qwen2.5-0.5B-Instruct` (BF16) — the devnet test checkpoint,
  hash-pinned on-chain and re-verified by the backend at startup.
- **Ops host:** DigitalOcean droplet serving the status board, terminating TLS
  for the miner's session endpoint, and tunnelling it to the GPU host.

## What's in this repo

| Path | Contents |
|---|---|
| `site/` | Public ops board — `index.html` status cards, `diary/` entries, `exec-summary.html` |
| `site/bin/collect.sh` | Cron collector on the droplet (`*/3`) — SSHes to the miner, writes `status.json` |
| `site/bin/notify.py` | Email alerts via Resend on miner state changes (down, calibrating, first session, recovery) |
| `site/bin/build.sh` | Rebuilds `index.html` from template + diary fragments |
| `site/chat.html` | Live read-only window into technocore.chat (agent-chat network) via nginx `/chat/api/` proxy |
| `site/bin/askd.py` + `flop-ask.service` | nano-gpt analysis backend at `/chat/ask` — key stays server-side |
| `site/bin/kibble-watch.py` | Cron watcher (`*/15`) — emails on kibble score moves, on-ramp openings, DID mentions |
| `pack/` | Verified mirror of the official `flop-miner-pack-devnet-ts` release (tarball + extracted, sha256-checked) |
| `AGENTS.md` | Agent handoff doc — infra map, secrets locations, current state |

## Current status

Miner stack fully provisioned and running; **blocked on devnet sponsor
capacity** (`sponsor_capacity_full`, 256/256 positions). A retry job on the
miner fires automatically when the faucet reopens; the miner-interest form is
in as the operator-grant path. Progress is tracked live on the site.

**Kibble track (running in parallel):** we work FLOP Labs' pre-genesis
useful-work board — attested deliveries are an airdrop IOU and rehearsal for
the mining loop. DID `z6MkgjCi…NQNNMt` has a live assigned devnet address
(`5DUAJj3Yr…u9`) via kibble's `/api/flop-devnet` bridge — the demand path that
feeds miners once funded.

## Notes

- Secrets live outside the repo (`~/.flop/*.env`, `/etc/flop-ops.env` on the
  droplet). Nothing sensitive is committed.
- Deployed copies of `site/bin/*` live in `/usr/local/lib/flop-ops/` on the
  droplet; this repo is the source of truth.
