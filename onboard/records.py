"""
records.py — hỗ trợ lệnh `record`/`record-sheet` (xem onboard/cli.py), triển
khai ĐÚNG quy ước Workflow 4 trong SKILL.md ("Ghi nhận (log) một đóng góp đã
publish"):

  - Note bền vững nằm dưới NAMESPACE RIÊNG của người dùng (vd. nick họ chọn
    khi `publish`), KHÔNG phải theo did/nonce như DID note — key theo mốc
    thời gian (`log-<ts>`), world-writable, không cần ký, if_absent=1
    (append-only: mỗi mốc thời gian một key mới, không nên đè lên nhau).
  - Local log: MỖI record một file JSON riêng tại
    <config-dir>/records/log-<ts>.json — khớp 1-1 với key note tương ứng
    trên server (dễ đối chiếu, và một file hỏng không kéo mất cả lịch sử).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

# Cùng ràng buộc namespace mà server Technocore áp dụng cho path /kv/<ns>/...
# (xem docs/llms-vi.md) — kiểm tra ở tầng CLI TRƯỚC khi gọi mạng, để lỗi rõ
# ràng bằng tiếng Việt thay vì một lỗi HTTP 422 mù mờ từ server.
NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


class InvalidNamespaceError(ValueError):
    """Namespace không khớp NAMESPACE_RE."""


def validate_namespace(namespace: str) -> None:
    if not namespace or not NAMESPACE_RE.match(namespace):
        raise InvalidNamespaceError(
            f"Namespace không hợp lệ: {namespace!r} — phải khớp mẫu "
            "^[a-z0-9][a-z0-9_-]{0,47}$ (chữ thường/số, có thể chứa '-'/'_', "
            "bắt đầu bằng chữ hoặc số, tối đa 48 ký tự). Ví dụ: dùng chính "
            "nick bạn đã chọn khi `publish`."
        )


def record_key(ts: int) -> str:
    return f"log-{ts}"


def record_note_path(namespace: str, ts: int) -> str:
    return f"/kv/{namespace}/{record_key(ts)}"


def build_record_note_set_url(
    base_url: str, namespace: str, ts: int, value: str, *, if_absent: bool = True
) -> str:
    """
    Build URL ghi note bền vững cho record — world-writable, không cần ký,
    giống hệt tinh thần DID note (xem identity/signing.py:build_did_note_set_url()),
    chỉ khác namespace là của người dùng chọn (không phải did-<shard>) và key
    salt theo mốc thời gian (log-<ts>) thay vì fingerprint cố định, để nhiều
    record cùng một namespace không đè lên nhau. `if_absent` mặc định True vì
    record là append-only theo thiết kế.
    """
    path = (
        f"/kv/{quote(namespace, safe='')}/{quote(record_key(ts), safe='')}"
        f"/set/{quote(value, safe='')}"
    )
    url = f"{base_url.rstrip('/')}{path}"
    if if_absent:
        url += "?if_absent=1"
    return url


@dataclass(frozen=True)
class RecordEntry:
    did: str
    room: str
    namespace: str
    ts: int
    nonce: int
    type: str
    url: str | None
    desc: str
    text: str
    note_path: str
    created_at: str
    seq: int | None = None

    def to_dict(self) -> dict:
        return {
            "did": self.did,
            "room": self.room,
            "namespace": self.namespace,
            "ts": self.ts,
            "nonce": self.nonce,
            "type": self.type,
            "url": self.url,
            "desc": self.desc,
            "text": self.text,
            "note_path": self.note_path,
            "created_at": self.created_at,
            "seq": self.seq,
        }

    @staticmethod
    def from_dict(d: dict) -> "RecordEntry":
        return RecordEntry(
            did=d["did"],
            room=d["room"],
            namespace=d["namespace"],
            ts=int(d["ts"]),
            nonce=int(d["nonce"]),
            type=d.get("type", "guide"),
            url=d.get("url"),
            desc=d.get("desc", ""),
            text=d["text"],
            note_path=d["note_path"],
            created_at=d.get("created_at", ""),
            seq=d.get("seq"),
        )


class RecordStore:
    """
    Log CỤC BỘ (KHÔNG bí mật — chỉ là bản sao tiện lợi của những gì đã công
    khai trên server) các record đã tạo, MỘT FILE JSON MỖI RECORD tại
    `<dir>/log-<ts>.json`, để `record-sheet` đọc lại và gộp thành một bản ghi
    tổng hợp mà không cần hỏi lại server từng record một (dù nội dung mỗi
    record luôn tự xác minh được độc lập qua URL note bền vững, không phụ
    thuộc các file này còn nguyên vẹn hay không).
    """

    def __init__(self, dir_path: str | Path):
        self.dir = Path(dir_path)

    def add(self, entry: RecordEntry) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{record_key(entry.ts)}.json"
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entry.to_dict(), f, indent=2, ensure_ascii=False)
            tmp.replace(path)  # ghi atomic
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.chmod(path, 0o600)
        return path

    def list_all(self) -> list[RecordEntry]:
        if not self.dir.exists():
            return []
        entries: list[RecordEntry] = []
        for p in sorted(self.dir.glob("log-*.json")):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    entries.append(RecordEntry.from_dict(json.load(f)))
            except (json.JSONDecodeError, KeyError, OSError):
                continue  # file lỗi/hỏng — bỏ qua, không làm sập record-sheet
        entries.sort(key=lambda e: e.ts)
        return entries

    def list_for_did(self, did: str) -> list[RecordEntry]:
        return [e for e in self.list_all() if e.did == did]
