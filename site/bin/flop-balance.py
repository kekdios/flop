#!/usr/bin/env python3
# Query FLOP devnet System.Account balances and write flop.json for the
# wallet page. Stdlib-only WebSocket client (no deps on the droplet).
# Cron: run from collect cycle or own timer. Output:
# /var/www/agentchainlab/flop.json — {updated, rpc_up, accounts:[{label,address,free}]}
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
import urllib.request

RPC = "wss://rpc.devnet.flop.network"
OUT_TMP = "/var/www/agentchainlab/flop.json.tmp"
OUT = "/var/www/agentchainlab/flop.json"
HEALTH = "https://faucet.flop.network/health"
SYS_ACCOUNT_PREFIX = (
    "26aa394eea5630e07c48ae0c9558cef7"
    "b99d880ec681799c0cf30e8886371da9")  # this runtime's System.Account
                                       # storage prefix (custom item name
                                       # hash — verified via metadata)
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

ACCOUNTS = [
    ("wallet", "5H1E2XQ3VhTt9WdwxFQcipBGgD67FqjXfHSnvMnWQKgPsUNS"),
    ("bridge", "5GpjXEBuS1L8Svrrh9EMDTruwvhNPAquAjjJ4XLAbbe3fR6w"),
    ("miner", "5DLMZtB5it7s6DZRmd98ABzfY8zPx9JBAAcvCtgXTL4ttSkC"),
    ("stale", "5DUAJj3YrFmYj5bbzzJULgrfKeLrVHpuWE1UAEsHPwSYivu9"),
]


def b58dec(s):
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    b = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + b


def storage_key(addr):
    pub = b58dec(addr)[1:-2]
    return ("0x" + SYS_ACCOUNT_PREFIX
            + hashlib.blake2b(pub, digest_size=16).hexdigest()
            + pub.hex())


def ws_connect(url, timeout=15):
    host = url.split("://", 1)[1].split("/")[0]
    sock = ssl.create_default_context().wrap_socket(
        socket.create_connection((host, 443), timeout=timeout),
        server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        ("GET / HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
         "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
         "Sec-WebSocket-Version: 13\r\nOrigin: https://agentchainlab.com\r\n\r\n"
         % (host, key)).encode())
    resp = sock.recv(4096)
    if b" 101 " not in resp.split(b"\r\n", 1)[0]:
        raise IOError("ws handshake failed: %r" % resp[:80])
    return sock


def ws_send(sock, text):
    payload = text.encode()
    mask = os.urandom(4)
    n = len(payload)
    header = b"\x81"  # FIN + text
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", n)
    sock.sendall(header + mask + bytes(b ^ mask[i % 4]
                                       for i, b in enumerate(payload)))


def ws_recv(sock, timeout=15):
    sock.settimeout(timeout)
    def read(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise IOError("ws closed")
            buf += chunk
        return buf
    while True:  # skip non-data frames until a text frame arrives
        b0, b1 = read(2)
        n = b1 & 0x7F
        if n == 126:
            (n,) = struct.unpack(">H", read(2))
        elif n == 127:
            (n,) = struct.unpack(">Q", read(8))
        payload = read(n)
        if (b0 & 0x0F) == 1:  # text frame
            return payload.decode()
        if (b0 & 0x0F) == 8:  # close
            raise IOError("ws close frame")


def rpc_call(sock, method, params, req_id):
    ws_send(sock, json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}))
    for _ in range(20):
        m = json.loads(ws_recv(sock))
        if m.get("id") == req_id:
            return m.get("result")
    raise IOError("no reply for id %s" % req_id)


def main():
    out = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "rpc_up": False, "accounts": []}
    try:
        h = json.loads(urllib.request.urlopen(HEALTH, timeout=10).read())
        out["faucet_health"] = h.get("status")
    except Exception:
        out["faucet_health"] = None
    try:
        sock = ws_connect(RPC)
        out["rpc_up"] = True
        out["chain"] = rpc_call(sock, "system_chain", [], 100)
        for i, (label, addr) in enumerate(ACCOUNTS):
            rec = {"label": label, "address": addr}
            val = rpc_call(sock, "state_getStorage", [storage_key(addr)], i)
            if val:
                raw = bytes.fromhex(val[2:])
                free = int.from_bytes(raw[16:32], "little")
                rec["free"] = free / 1e18
            else:
                rec["free"] = 0
            out["accounts"].append(rec)
        sock.close()
    except Exception as e:
        out["rpc_error"] = str(e)[:200]
    tmp = json.dumps(out)
    open(OUT_TMP, "w").write(tmp)
    os.replace(OUT_TMP, OUT)
    print(tmp)


if __name__ == "__main__":
    main()
