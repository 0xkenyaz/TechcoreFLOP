"""
keystore.py — tạo, mã hóa và lưu Ed25519 keypair CHỈ ở local.

NGUYÊN TẮC AN TOÀN (không thương lượng):
  - Private key/seed KHÔNG BAO GIỜ được in ra chat, gửi qua network, hay log lại.
  - File keystore trên đĩa luôn ở dạng MÃ HÓA (AES-256-GCM, key dẫn xuất từ
    passphrase bằng Scrypt) — không bao giờ lưu private key dạng plaintext.
  - Module này không có bất kỳ lời gọi network nào. Nó chỉ đọc/ghi file local.
  - Người dùng chịu trách nhiệm giữ passphrase; không có cách khôi phục nếu mất.

Định dạng file keystore (JSON):
{
  "version": 1,
  "did": "did:key:z6Mk...",                # public — an toàn để publish
  "kdf": "scrypt",
  "kdf_params": {"n": 2**14, "r": 8, "p": 1, "salt": "<base64>"},
  "cipher": "AES-256-GCM",
  "nonce": "<base64, 12 byte>",
  "ciphertext": "<base64>"                  # mã hóa 32-byte Ed25519 seed
}
"""

from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import base58
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .did import public_key_to_did

_SCRYPT_N = 2**14  # ~16MB RAM, đủ chậm cho passphrase, đủ nhanh cho UX (~0.3-0.5s)
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32  # AES-256


class KeystoreError(Exception):
    """Lỗi chung khi thao tác với keystore."""


class WrongPassphraseError(KeystoreError):
    """Passphrase sai — không giải mã được (AEAD tag không khớp)."""


@dataclass(frozen=True)
class Identity:
    """
    Identity đã unlock trong bộ nhớ — CHỈ tồn tại tạm thời trong tiến trình đang chạy.
    Không bao giờ serialize dataclass này ra ngoài (log, print, gửi mạng...).
    """

    did: str
    _private_key: Ed25519PrivateKey

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._private_key.public_key()

    def sign_raw(self, data: bytes) -> bytes:
        """Ký raw bytes bằng private key. Trả về 64-byte chữ ký Ed25519."""
        return self._private_key.sign(data)


def export_seed_b58(identity: Identity) -> str:
    """
    Xuất RAW 32-byte Ed25519 seed (base58btc, KHÔNG có tiền tố/multicodec —
    khác hoàn toàn định dạng did:key) của một Identity đã unlock.

    CHỈ dùng cho mục đích đưa seed sang Web Signer (web/) chạy trên THIẾT BỊ
    của chính người dùng, khi họ chủ động chấp nhận rủi ro đó — xem cảnh báo
    ở `cmd_export_seed` trong onboard/cli.py. Bản thân hàm này KHÔNG in, log,
    hay gửi seed đi đâu cả — chỉ trả về chuỗi để nơi gọi tự quyết định hiển
    thị một lần rồi bỏ.
    """
    raw = identity._private_key.private_bytes_raw()  # 32 byte — seed gốc
    return base58.b58encode(raw).decode("ascii")


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8"))


def generate_and_save(path: str | Path, passphrase: str) -> str:
    """
    Tạo keypair Ed25519 mới, mã hóa bằng passphrase, lưu vào `path`.
    Trả về did:key (public, an toàn để hiển thị cho người dùng).

    Sẽ từ chối ghi đè nếu file đã tồn tại — tránh mất key hiện có do vô ý.
    """
    path = Path(path)
    if path.exists():
        raise KeystoreError(
            f"File '{path}' đã tồn tại. Nếu bạn thật sự muốn tạo identity mới, "
            "hãy đổi tên/di chuyển file cũ trước — công cụ này không tự ghi đè "
            "để tránh mất key hiện có."
        )

    private_key = Ed25519PrivateKey.generate()
    seed = private_key.private_bytes_raw()  # 32 byte — đây là bí mật cần bảo vệ
    did = public_key_to_did(private_key.public_key())

    salt = os.urandom(_SALT_LEN)
    aes_key = _derive_key(passphrase, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(aes_key)
    # associated_data = did → gắn ciphertext với đúng identity, chống hoán đổi file
    ciphertext = aesgcm.encrypt(nonce, seed, associated_data=did.encode("utf-8"))

    payload = {
        "version": 1,
        "did": did,
        "kdf": "scrypt",
        "kdf_params": {
            "n": _SCRYPT_N,
            "r": _SCRYPT_R,
            "p": _SCRYPT_P,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": "AES-256-GCM",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    # Ghi file với quyền 0600 ngay từ đầu (trước khi có nội dung) để tránh race condition
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except BaseException:
        path.unlink(missing_ok=True)
        raise

    # Xóa seed khỏi biến Python càng sớm càng tốt (không đảm bảo tuyệt đối do
    # GC của Python, nhưng vẫn là thực hành tốt hơn giữ tham chiếu lâu dài)
    del seed

    return did


def load(path: str | Path, passphrase: str) -> Identity:
    """Giải mã keystore bằng passphrase, trả về Identity đã unlock trong bộ nhớ."""
    path = Path(path)
    if not path.exists():
        raise KeystoreError(f"Không tìm thấy keystore tại '{path}'.")

    _check_permissions(path)

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if payload.get("version") != 1:
        raise KeystoreError(f"Phiên bản keystore không được hỗ trợ: {payload.get('version')}")

    did = payload["did"]
    kdf_params = payload["kdf_params"]
    salt = base64.b64decode(kdf_params["salt"])
    aes_key = _derive_key(
        passphrase, salt, kdf_params["n"], kdf_params["r"], kdf_params["p"]
    )
    nonce = base64.b64decode(payload["nonce"])
    ciphertext = base64.b64decode(payload["ciphertext"])

    aesgcm = AESGCM(aes_key)
    try:
        seed = aesgcm.decrypt(nonce, ciphertext, associated_data=did.encode("utf-8"))
    except InvalidTag as exc:
        raise WrongPassphraseError("Passphrase sai hoặc file keystore bị hỏng/bị sửa.") from exc

    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    del seed

    # Kiểm tra chéo: did tính lại từ private key phải khớp did lưu trong file
    recomputed_did = public_key_to_did(private_key.public_key())
    if recomputed_did != did:
        raise KeystoreError(
            "DID trong file không khớp với key giải mã được — file có thể đã bị "
            "sửa đổi hoặc hỏng."
        )

    return Identity(did=did, _private_key=private_key)


def _check_permissions(path: Path) -> None:
    """Cảnh báo (không chặn cứng) nếu file keystore có quyền quá lỏng lẻo."""
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        import warnings

        warnings.warn(
            f"Keystore '{path}' có quyền truy cập lỏng lẻo ({oct(mode)}). "
            f"Nên chạy: chmod 600 {path}",
            stacklevel=2,
        )
