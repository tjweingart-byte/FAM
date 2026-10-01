"""Print a VAPID key pair for Web Push (`push.py`).

    python tools/vapid_keys.py

Put the two lines in the server's environment (or FAM_SECRETS). The public
key is what browsers subscribe with; the private key signs every notification
and is never sent anywhere. Make one pair per deployment and keep it: a new
pair orphans every phone already subscribed.
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_pair() -> tuple:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(public), _b64(private)


if __name__ == "__main__":
    public, private = make_pair()
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
