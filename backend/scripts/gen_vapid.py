"""Print a fresh VAPID key pair to paste into .env. Run once per environment:

    python scripts/gen_vapid.py

Outputs:
  VAPID_PRIVATE_KEY  base64url of the raw 32-byte EC private value
                     (accepted by pywebpush's vapid_private_key)
  VAPID_PUBLIC_KEY   base64url of the uncompressed public point — this is the
                     applicationServerKey the browser subscribes with.
These match the format produced by the Node web-push tool.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())

    priv_value = private_key.private_numbers().private_value
    priv_bytes = priv_value.to_bytes(32, "big")

    pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )

    print("# Add these to your .env")
    print(f"VAPID_PRIVATE_KEY={b64url(priv_bytes)}")
    print(f"VAPID_PUBLIC_KEY={b64url(pub_bytes)}")


if __name__ == "__main__":
    main()
