"""
did.py — mã hóa/giải mã did:key (Ed25519) và tính fingerprint theo convention
của Technocore (xem /llms.txt mục IDENTITY, /patterns.md mục 3).

did:key format:  "did:key:" + multibase(base58btc, multicodec(ed25519-pub) + raw_pubkey)
  - multicodec ed25519-pub = 0xed01 (varint 2 byte: 0xed, 0x01)
  - multibase base58btc    = tiền tố ký tự 'z'

Fingerprint (dùng cho tên note /kv/did-<shard>/<key>):
  - SHA-256 của TOÀN BỘ chuỗi "did:key:z6Mk..." (dạng string, UTF-8 bytes)
  - lấy 16 ký tự hex đầu, viết thường
  - shard = 2 ký tự đầu, key = 14 ký tự còn lại
  → khớp regex tên: ^[a-z0-9][a-z0-9_-]{0,47}$

Đây là module THUẦN (không I/O, không side-effect) — dễ test độc lập.
"""

from __future__ import annotations

import hashlib

import base58
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Multicodec varint cho ed25519-pub = 0xed01 → 2 byte: 0xed, 0x01
_MULTICODEC_ED25519_PUB = bytes([0xED, 0x01])
_DID_KEY_PREFIX = "did:key:z"


class InvalidDidKeyError(ValueError):
    """Chuỗi did:key không hợp lệ (sai prefix, sai multicodec, sai độ dài...)."""


def public_key_to_did(public_key: Ed25519PublicKey) -> str:
    """Chuyển Ed25519PublicKey → chuỗi did:key:z6Mk... (base58btc)."""
    raw = public_key.public_bytes_raw()  # 32 byte
    if len(raw) != 32:
        raise ValueError(f"Ed25519 public key phải 32 byte, nhận {len(raw)}")
    payload = _MULTICODEC_ED25519_PUB + raw
    return _DID_KEY_PREFIX + base58.b58encode(payload).decode("ascii")


def did_to_public_key(did: str) -> Ed25519PublicKey:
    """Ngược lại: chuỗi did:key:z6Mk... → Ed25519PublicKey. Ném lỗi nếu sai định dạng."""
    if not did.startswith(_DID_KEY_PREFIX):
        raise InvalidDidKeyError(f"did:key phải bắt đầu bằng '{_DID_KEY_PREFIX}', nhận: {did!r}")
    b58_body = did[len(_DID_KEY_PREFIX) - 1:]  # giữ lại ký tự 'z' để decode cho đúng chuẩn multibase
    # base58btc: bỏ ký tự 'z' rồi decode phần còn lại
    try:
        payload = base58.b58decode(b58_body[1:])
    except ValueError as exc:
        raise InvalidDidKeyError(f"Không decode được base58btc: {exc}") from exc

    if payload[:2] != _MULTICODEC_ED25519_PUB:
        raise InvalidDidKeyError(
            f"Multicodec không phải ed25519-pub (0xed01), nhận: {payload[:2].hex()}"
        )
    raw = payload[2:]
    if len(raw) != 32:
        raise InvalidDidKeyError(f"Public key phải 32 byte sau khi bỏ multicodec, nhận {len(raw)}")
    return Ed25519PublicKey.from_public_bytes(raw)


def fingerprint(did: str) -> str:
    """SHA-256(did) → 16 hex đầu, viết thường. Dùng làm tên note công khai."""
    digest = hashlib.sha256(did.encode("utf-8")).hexdigest()
    return digest[:16]


def fingerprint_shard_path(did: str) -> tuple[str, str]:
    """
    Trả về (shard, key) để build đường dẫn /kv/did-<shard>/<key>.
    shard = 2 ký tự đầu của fingerprint, key = 14 ký tự còn lại.
    """
    fp = fingerprint(did)
    return fp[:2], fp[2:]


def did_note_path(did: str) -> str:
    """Đường dẫn đầy đủ (không domain) tới DID note theo convention hiện hành."""
    shard, key = fingerprint_shard_path(did)
    return f"/kv/did-{shard}/{key}"


def legacy_did_note_path(did: str) -> str:
    """Đường dẫn note theo convention CŨ (trước khi shard) — chỉ để đọc, không dùng để ghi mới."""
    return f"/kv/did/{fingerprint(did)}"
