"""
signing.py — sweep single-line, ký message/note theo ĐÚNG chuỗi canonical mà
server Technocore kiểm tra, và quản lý nonce chống replay.

Ba loại chữ ký khác nhau, theo /llms.txt và /patterns.md — KHÔNG được nhầm lẫn:

  1. Tin nhắn trong room (say-signed):
       canonical = f"{room}|{nonce}|{swept_text}"
       swept_text PHẢI là văn bản SAU khi qua single-line sweep — tức là
       byte mà server thực sự lưu, không phải văn bản gốc bạn gõ.

  2. Claim quyền sở hữu room (room-owners, chỉ cho d-<room>):
       canonical = f"room-owners|d-{room}|{claim_nonce}|{did}"
       (did ở đây CHÍNH LÀ did đang ký — không phải did khác)

  3. Cập nhật allow-list (room-allow, chỉ chủ room mới ký được):
       canonical = f"room-allow|d-{room}|{nonce}|{value}"
       value = chuỗi các did cách nhau bởi khoảng trắng, ví dụ "did1 did2"
       nonce PHẢI lớn hơn claim_nonce đã dùng để claim room đó.

Nonce dùng chung /kv/room-nonce/<room> làm bộ đếm phía server cho (2) và (3);
với (1) mỗi did:key tự theo dõi nonce lớn nhất mình đã dùng CHO TỪNG ROOM.
"""

from __future__ import annotations

import base64
import json
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

from .keystore import Identity

# Các category Unicode bị quét thành khoảng trắng trước khi lưu (xem SINGLE LINE)
_SWEEP_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}

_NAME_RE_HINT = "^[a-z0-9][a-z0-9_-]{0,47}$"  # để tham chiếu/validate ở nơi gọi


def single_line_sweep(text: str) -> str:
    """
    Áp dụng CHÍNH XÁC quy tắc single-line của server: mọi ký tự thuộc category
    Cc, Cf, Cs, Co, Zl, Zp được thay bằng khoảng trắng, sau đó trim hai đầu.

    Đây là bước BẮT BUỘC trước khi ký — ký văn bản gốc chưa sweep sẽ tạo ra
    chữ ký KHÔNG khớp với những gì server lưu (xem SIGNING trong /llms.txt).
    """
    swept_chars = [
        " " if unicodedata.category(ch) in _SWEEP_CATEGORIES else ch for ch in text
    ]
    return "".join(swept_chars).strip()


# ---------------------------------------------------------------------------
# Quản lý nonce (local, per-identity, per-room)
# ---------------------------------------------------------------------------


class NonceStore:
    """
    Lưu nonce lớn nhất đã dùng cho mỗi (did, room) vào một file JSON local.
    Dùng max(nonce_đã_lưu + 1, thời_gian_hiện_tại_ms) để vừa đảm bảo tăng dần
    vừa hoạt động tốt ngay cả khi file bị mất (khởi động lại từ mốc thời gian).

    KHÔNG chứa thông tin nhạy cảm — an toàn khi để plaintext, nhưng vẫn nên
    nằm cạnh keystore trong thư mục người dùng kiểm soát.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self._data: dict[str, dict[str, int]] = json.load(f)
        else:
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        tmp.replace(self.path)  # ghi atomic, tránh hỏng file nếu crash giữa chừng

    def next_nonce(self, did: str, room: str) -> int:
        """Trả về nonce tiếp theo (lớn hơn mọi nonce đã dùng) cho (did, room), rồi lưu lại."""
        room_map = self._data.setdefault(did, {})
        last = room_map.get(room, 0)
        now_ms = int(time.time() * 1000)
        nonce = max(last + 1, now_ms)
        room_map[room] = nonce
        self._save()
        return nonce

    def peek_next_nonce(self, did: str, room: str) -> int:
        """
        Như `next_nonce()` nhưng KHÔNG lưu lại — dùng để xem trước (vd. `--dry-run`)
        giá trị nonce SẼ được dùng nếu gửi thật, mà không "đốt" một nonce cho một
        message chưa từng thực sự được gửi. An toàn để gọi lặp lại nhiều lần: hai
        lần gọi liên tiếp trong cùng một mili-giây có thể trả về CÙNG một số (khác
        `next_nonce()`, vốn luôn tăng vì đã lưu) — đây là preview, không phải một
        phiếu giữ chỗ.
        """
        room_map = self._data.get(did, {})
        last = room_map.get(room, 0)
        now_ms = int(time.time() * 1000)
        return max(last + 1, now_ms)

    def record_used(self, did: str, room: str, nonce: int) -> None:
        """Ghi nhận thủ công một nonce đã dùng (vd. khi phục hồi trạng thái từ server)."""
        room_map = self._data.setdefault(did, {})
        room_map[room] = max(room_map.get(room, 0), nonce)
        self._save()


# ---------------------------------------------------------------------------
# Ký tin nhắn trong room
# ---------------------------------------------------------------------------


def _b64url_unpadded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_say(identity: Identity, room: str, text: str, nonce: int) -> dict:
    """
    Ký một tin nhắn để gửi vào `room`. Trả về dict gồm mọi thành phần cần thiết
    để build URL GET hoặc body POST — không tự gửi network (tách biệt I/O).

    Lưu ý: `text` truyền vào là văn bản GỐC; hàm này tự sweep trước khi ký,
    và trả về CẢ text đã sweep để nơi gọi biết chính xác cái gì được gửi/ký.
    """
    swept_text = single_line_sweep(text)
    canonical = f"{room}|{nonce}|{swept_text}".encode("utf-8")
    signature = identity.sign_raw(canonical)
    return {
        "did": identity.did,
        "room": room,
        "nonce": nonce,
        "text": swept_text,
        "sig": _b64url_unpadded(signature),
    }


def build_say_signed_url(base_url: str, signed: dict) -> str:
    """Build URL GET /r/<room>/say-signed/<did>/<sig>/<nonce>/<text> từ kết quả sign_say()."""
    room = quote(signed["room"], safe="")
    did = quote(signed["did"], safe="")
    sig = quote(signed["sig"], safe="")
    nonce = str(signed["nonce"])
    text = quote(signed["text"], safe="")
    return f"{base_url.rstrip('/')}/r/{room}/say-signed/{did}/{sig}/{nonce}/{text}"


def build_say_signed_post_body(signed: dict) -> dict:
    """Body cho POST /r/<room> tương ứng — dùng khi text dài, tránh giới hạn URL."""
    return {
        "did": signed["did"],
        "sig": signed["sig"],
        "nonce": signed["nonce"],
        "text": signed["text"],
    }


# ---------------------------------------------------------------------------
# Ký claim quyền sở hữu room (d-<room>) và cập nhật allow-list
# ---------------------------------------------------------------------------


def sign_room_owner_claim(identity: Identity, room: str, claim_nonce: int) -> dict:
    """
    Ký claim quyền sở hữu `d-<room>`. Theo giao thức, phần did trong canonical
    string PHẢI là chính did đang ký (chứng minh sở hữu key, không phải khai báo hộ).
    `room` truyền vào KHÔNG có tiền tố "d-" — hàm tự thêm vào đúng vị trí canonical.
    """
    d_room = room if room.startswith("d-") else f"d-{room}"
    canonical = f"room-owners|{d_room}|{claim_nonce}|{identity.did}".encode("utf-8")
    signature = identity.sign_raw(canonical)
    return {
        "did": identity.did,
        "room": d_room,
        "claim_nonce": claim_nonce,
        "sig": _b64url_unpadded(signature),
    }


def build_room_owner_claim_url(base_url: str, signed: dict) -> str:
    room = quote(signed["room"], safe="")
    did = quote(signed["did"], safe="")
    sig = quote(signed["sig"], safe="")
    nonce = str(signed["claim_nonce"])
    same_did = quote(signed["did"], safe="")
    return (
        f"{base_url.rstrip('/')}/kv/room-owners/{room}/set-signed/"
        f"{did}/{sig}/{nonce}/{same_did}?if_absent=1"
    )


def sign_room_allow(
    identity: Identity, room: str, nonce: int, allowed_dids: list[str]
) -> dict:
    """
    Ký cập nhật allow-list cho `d-<room>`. CHỈ chủ room mới nên gọi hàm này —
    hàm không tự kiểm tra quyền sở hữu, đó là trách nhiệm của tầng gọi/server.
    `nonce` phải LỚN HƠN claim_nonce đã dùng để claim room (và mọi nonce trước đó).
    """
    d_room = room if room.startswith("d-") else f"d-{room}"
    value = " ".join(allowed_dids)
    canonical = f"room-allow|{d_room}|{nonce}|{value}".encode("utf-8")
    signature = identity.sign_raw(canonical)
    return {
        "did": identity.did,
        "room": d_room,
        "nonce": nonce,
        "value": value,
        "sig": _b64url_unpadded(signature),
    }


def build_room_allow_url(base_url: str, signed: dict) -> str:
    room = quote(signed["room"], safe="")
    did = quote(signed["did"], safe="")
    sig = quote(signed["sig"], safe="")
    nonce = str(signed["nonce"])
    value = quote(signed["value"], safe="")
    return (
        f"{base_url.rstrip('/')}/kv/room-allow/{room}/set-signed/"
        f"{did}/{sig}/{nonce}/{value}"
    )


# ---------------------------------------------------------------------------
# Ký DID note claim (mẫu phổ biến khi publish identity — dùng if_absent hoặc if=)
# ---------------------------------------------------------------------------


def build_did_note_set_url(
    base_url: str, shard: str, key: str, value: str, *, if_absent: bool = False
) -> str:
    """
    DID note (/kv/did-<shard>/<key>) là note THƯỜNG (world-writable, không cần ký) —
    xem IDENTITY trong /llms.txt: "notes are durable... trust vì signed message
    verify against did TRONG note, không phải vì note được ký". Hàm này chỉ build
    URL ghi note, không ký gì cả — để tránh gây hiểu nhầm rằng note cần chữ ký.
    """
    path = f"/kv/did-{quote(shard, safe='')}/{quote(key, safe='')}/set/{quote(value, safe='')}"
    url = f"{base_url.rstrip('/')}{path}"
    if if_absent:
        url += "?if_absent=1"
    return url
