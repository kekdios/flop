# FLOP devnet-ts miner pack (public path)

Test network: test FLOP has no value and there are no earnings yet.

This pack runs a miner without FLOP's private network. The miner reaches the public RPC, the
public calibration mailbox and the public stake sponsor, and serves its own HTTPS session endpoint.
`miner.env.example` uses the `:stable` images; the pack was built only after each image `stable`
pointed at met its minimum version in `releases.json`. `./decode-policy.sh miner.env && $C pull &&
$C up -d` (step 3) updates to the current ones.

What works today: sponsored registration and stake, and calibration. Paid sessions and gateway
traffic for public-path miners follow; we tell invited operators when they open.

## Host

- Ubuntu 22.04 or 24.04, an NVIDIA GPU with at least 16 GB of VRAM, Docker with Compose and the
  NVIDIA container toolkit (`docker info | grep -i nvidia` must print something), and
  `apt install jq curl openssl`.
- A public DNS name for this machine (an A or AAAA record pointing at it) with inbound TCP 80 and
  443 open. Caddy (`docker-compose.tls.yml`) uses them to get a Let's Encrypt certificate and serve
  the session endpoint. If the host cannot take inbound traffic, run your own tunnel from a
  hostname to `http://127.0.0.1:8645` instead and leave `docker-compose.tls.yml` out below.
- Open nothing else: the session port 8645 and the model backend stay on loopback.

Run everything as root from `/opt/flop-miner`:

```sh
mkdir -p /opt/flop-miner && tar xzf flop-miner-pack-*.tar.gz -C /opt/flop-miner --strip-components=1
cd /opt/flop-miner
CLI="$(sed -n 's/^FLOP_CLI=//p' miner.env.example)"
```

## 1. Create the miner account and ask for sponsorship

```sh
docker run --rm --network none --entrypoint zkv-relay ghcr.io/flop-labs/flop-core/node-devnet:sha-eea56e7 key generate --scheme sr25519 --output-type json > miner-key.json
chmod 600 miner-key.json && (umask 077; jq -r .secretPhrase miner-key.json > signer)
read -rs FLOP_KEY_PASSWORD; export FLOP_KEY_PASSWORD; export MINER_SECRET="$(cat signer)"
docker run --rm --network none -e MINER_SECRET -e FLOP_KEY_PASSWORD -v /opt/flop-miner:/work "$CLI" keys import --type sr25519 --secret-env MINER_SECRET --out /work/miner.json --password-env FLOP_KEY_PASSWORD
unset MINER_SECRET
echo "miner address: $(jq -r .ss58Address miner-key.json)"
```

Back up `miner-key.json` offline; never send it, the `signer` file or the phrase to anyone,
including FLOP. The sponsor approves a new miner automatically while only a few automatically
sponsored miners are still calibrating, so go on to step 2. An approved operator grants the address
itself instead (next section).

### Approved operators: grant each miner yourself

If you run several miners, request approval of an operator key once with an
[access request](https://github.com/flop-labs/network/issues/new?template=access.yml), with how many miners you plan. The operator key is a separate account, kept on a machine that is not a miner host, with this
pack and Docker. Create it there and send FLOP only its address:

```sh
docker run --rm --network none --entrypoint zkv-relay ghcr.io/flop-labs/flop-core/node-devnet:sha-eea56e7 key generate --scheme sr25519 --output-type json > operator-key.json
chmod 600 operator-key.json && (umask 077; jq -r .secretPhrase operator-key.json > operator-signer)
echo "operator address: $(jq -r .ss58Address operator-key.json)"
```

Once FLOP confirms the approval, grant each new miner from that machine, without asking FLOP again:

```sh
OPERATOR_SIGNER_FILE=operator-signer ./sponsor-grant.sh <miner address> > grant.json
```

`grant.json` is not secret: it names one miner account, which must still sign its own request.
Copy it to the miner host, for example to `/opt/flop-miner/grant.json`. A grant lasts 30 days
(`GRANT_DAYS=` up to 365). With `GENESIS=<chain genesis hash>` it signs without any network, so the
operator key can stay offline. Never copy the operator key to a miner host.

## 2. Sponsorship

```sh
./sponsor.sh                                   # approved automatically, or an address FLOP listed
GRANT_FILE=/opt/flop-miner/grant.json ./sponsor.sh   # a miner you granted
```

It registers the miner and posts about 1M test FLOP of locked stake. 200 or 201 is done, 503 means
retry in a minute, 409 means already sponsored (or sponsored under another grant), 403 means the
address is listed for the other role or the grant is expired or not from an approved operator, and
429 means a daily limit or your operator quota is used up, or too many automatically sponsored
miners are still calibrating: retry later. A later top-up, after calibration, is the same
command and needs an unexpired grant. Do not run `miner register` yourself.

Before you run it, and before any retry, read `https://faucet.flop.network/limits`. It should show
`sponsor.miner.status` as `open`; `automatic_full` or `daily_limit_reached` means a request without a
grant gets 429 now. Never retry in a loop. A refusal prints its `reason`, `action` and wait: on
`retry_later` wait at least `retryAfterSeconds`, on `fix_request` change the request, and on
`report_to_operator` stop and report the `error` and `reason` lines.

## 3. Model, settings, start

Set `NAME` to the DNS name first, for example `NAME=flop-miner.example.com`.

```sh
./fetch-model.sh Qwen/Qwen2.5-0.5B-Instruct 7ae557604adf67be50417f59c2c2f167def9a775 /opt/flop-miner/models/qwen2.5-0.5b-instruct
install -d -m 700 session-keys && (umask 077; set -C; openssl rand -hex 32 > session-keys/enclave.seed)
cp miner.env.example miner.env && chmod 600 miner.env
sed -i -e "s/^SESSION_PUBLIC_HOST=.*/SESSION_PUBLIC_HOST=$NAME/" \
  -e "s/^INFERENCE_API_KEY=.*/INFERENCE_API_KEY=$(openssl rand -hex 32)/" \
  -e "s/^SESSION_AUTH_TOKEN=.*/SESSION_AUTH_TOKEN=$(openssl rand -hex 32)/" miner.env
C="docker compose --env-file miner.env -f docker-compose.miner.yml -f docker-compose.backend.yml -f docker-compose.tls.yml"
./decode-policy.sh miner.env && $C pull && $C up -d
curl -s -o /dev/null -w '%{http_code}\n' "https://$NAME/session/info"
```

`decode-policy.sh` fills `DECODE_POLICY` and `DECODE_POLICY_HASH`, which the pack leaves empty.
It takes the policy that <https://flop.network/releases.json> publishes for `MODEL_HASH` under
`models`, hashes it the way the chain does, and writes it only if the finalized chain allowlists
that hash for the model. Otherwise it exits non-zero, changes nothing, and the `&&` keeps Compose
from starting. Run the same line again for later restarts, so a policy the chain has since dropped
is refused. The hash binds the JSON, so the manifest host needs no trust. To serve a
different allowlisted policy of the same model, set `DECODE_POLICY_HASH` to its hash before
running it.

The last command must print 401; the first certificate can take a minute. With your own tunnel,
leave `-f docker-compose.tls.yml` out of `C`. Never overwrite `session-keys/enclave.seed` later.

The worker declares the GPU, publishes its calibration key and its endpoint to the public mailbox,
and answers calibration jobs by itself. Calibration can take a day on devnet. Watch it at
<https://explorer.devnet.flop.network/mining> and keep the miner running: calibration jobs left
unanswered are penalized. There is nothing else to send.

## Network and TLS

- **Who connects to the session endpoint.** Any agent that has your endpoint may open a session
  with you directly, from any address, for example `flop session run --via force` pointed at you.
  There is no public miner directory yet, so today most sessions come through FLOP's gateway. The
  gateway is one client, with its renewer (endpoint probes) and admission writer (the signed info
  proof checked before a miner is listed). FLOP does not publish its addresses for allowlisting:
  keep the session port open to any client. Sessions are bound to a funded channel, and the worker
  limits each client's request rate. Calibration, the mailbox, the RPC and session DA are all
  outbound from the miner.
- **Another port.** Set `SESSION_PUBLIC_HOST=<name>:<port>` for any port but 443, which is written
  without a port. The worker signs that URL into its endpoint ad, and the gateway dials it.
- **Your own TLS.** Any publicly trusted certificate that covers the name works, wildcards
  included. Leave `docker-compose.tls.yml` out and proxy to `http://127.0.0.1:8645` from loopback
  or a private address, setting or appending `X-Forwarded-For`. The worker takes the client from
  the last entry, and only from such a proxy, for its per-client limits. Port 80 is needed only by
  the Caddy sidecar, to obtain its certificate.

## Rules

- Never share the secret phrase, `signer`, keystore password, `SESSION_AUTH_TOKEN` or
  `INFERENCE_API_KEY`.
- Expose only the session endpoint's port (80 and 443 with the Caddy sidecar), or your tunnel.
- A rented VM bills until it is deleted. When you stop mining, stop the worker, run
  `sudo ./retire.sh` to deregister the miner on chain (sponsored stake unbonds over 7 days and goes
  back to its sponsor; an open session or dispute refuses it until it settles), then delete the VM.
- A retired miner still counts toward an operator's quota.
