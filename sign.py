#!/usr/bin/env python3
"""
sign.py — the signer. It runs on YOUR machine, never on the server.

rules_batch hands back a digest and tells you to sign it. This is the thing that
signs it. Nothing here ever talks to the registry: you paste a digest in, you
get a base64 signature out, and you paste that back into the conversation.

That separation is the whole point. A password would eventually end up in a
project's instructions, because it is convenient and it is needed often — and
from that moment every chat has it. A private key cannot make that trip: what
enters the conversation is a signature, valid for one digest and for nothing
else.

    python3 sign.py --keygen                 make the key pair, once
    python3 sign.py <digest>                 sign a batch digest
    python3 sign.py --pubkey                 print the public key again

The key lives in ~/.codifier/approval.key by default, mode 0600. Override with
--key or CODIFIER_KEY. Losing it is not a catastrophe: you are root on the
server and sqlite3 opens the file — which is why there is no recovery ceremony
here, and why there does not need to be one.

Requires: cryptography  (pip install cryptography)
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import stat
import sys

DEFAULT_KEY = os.environ.get("CODIFIER_KEY") or os.path.expanduser("~/.codifier/approval.key")

# A batch digest is sha256 in hex, and so are the renew and promote messages.
# Checking the shape here is not pedantry: signing the wrong string produces a
# signature that verifies against nothing, and the error you would get back —
# "signature does not match this digest" — sends you looking at the key.
RE_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def die(msg: str) -> None:
    print(f"sign.py: {msg}", file=sys.stderr)
    sys.exit(2)


try:
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    die("this needs the 'cryptography' package: pip install cryptography")


def load(path: str) -> Ed25519PrivateKey:
    if not os.path.exists(path):
        die(f"no key at {path} — run: python3 sign.py --keygen")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        die(f"{path} is {oct(mode)}: readable by others. chmod 600 it before using it.")
    with open(path, "rb") as f:
        key = ser.load_pem_private_key(f.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        die(f"{path} is not an ed25519 key")
    return key


def public_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def keygen(path: str) -> None:
    if os.path.exists(path):
        die(f"{path} already exists. Generating over it would orphan every approval "
            "made so far — move the old one aside first if you really mean to.")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8,
                            ser.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    print(f"private key written to {path} (mode 600). It does not leave this machine.\n")
    print("Put this in APPROVAL_PUBKEY, in the container template:\n")
    print(f"    {public_b64(key)}\n")
    print("Then clear APPROVAL_GRACE_UNTIL, and Apply (never Restart).")


def main() -> None:
    p = argparse.ArgumentParser(description="Sign a codifier-mcp batch digest.")
    p.add_argument("digest", nargs="?", help="the digest from rules_batch")
    p.add_argument("--key", default=DEFAULT_KEY, help=f"key file (default {DEFAULT_KEY})")
    p.add_argument("--keygen", action="store_true", help="generate the key pair, once")
    p.add_argument("--pubkey", action="store_true", help="print the public key")
    p.add_argument("--force", action="store_true",
                   help="sign a message that is not a sha256 digest")
    a = p.parse_args()

    if a.keygen:
        keygen(a.key)
        return
    if a.pubkey:
        print(public_b64(load(a.key)))
        return
    if not a.digest:
        p.print_help()
        sys.exit(2)

    digest = a.digest.strip()
    if not RE_DIGEST.match(digest) and not a.force:
        die(f"{digest!r} does not look like a digest (64 hex characters). Copy it from "
            "rules_batch exactly, with no quotes and no spaces. Use --force if you "
            "really mean to sign something else.")

    key = load(a.key)
    sig = base64.b64encode(key.sign(digest.encode())).decode()
    print(sig)


if __name__ == "__main__":
    main()
