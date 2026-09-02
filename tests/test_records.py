"""Test cho onboard/records.py — validate_namespace, record_note_path,
build_record_note_set_url (hàm thuần) và RecordStore (local log, mỗi record
một file JSON, không cần network)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onboard import records  # noqa: E402

_DID = "did:key:z6MkqbBCHBuJrz85cVbdMEMCfeohRmiUw7FutYDSSvuNXLmV"


# ---------------------------------------------------------------------------
# validate_namespace
# ---------------------------------------------------------------------------


def test_validate_namespace_accepts_simple_lowercase():
    records.validate_namespace("nguyenvana")  # không raise


def test_validate_namespace_accepts_digits_dash_underscore():
    records.validate_namespace("a1-b_2")  # không raise


def test_validate_namespace_rejects_uppercase():
    try:
        records.validate_namespace("NguyenVanA")
        assert False, "phải raise với chữ hoa"
    except records.InvalidNamespaceError:
        pass


def test_validate_namespace_rejects_spaces():
    try:
        records.validate_namespace("khong hop le")
        assert False, "phải raise với khoảng trắng"
    except records.InvalidNamespaceError:
        pass


def test_validate_namespace_rejects_empty():
    try:
        records.validate_namespace("")
        assert False, "phải raise với chuỗi rỗng"
    except records.InvalidNamespaceError:
        pass


def test_validate_namespace_rejects_leading_dash():
    try:
        records.validate_namespace("-abc")
        assert False, "phải raise khi bắt đầu bằng '-'"
    except records.InvalidNamespaceError:
        pass


def test_validate_namespace_rejects_too_long():
    try:
        records.validate_namespace("a" * 49)
        assert False, "phải raise khi vượt quá 48 ký tự"
    except records.InvalidNamespaceError:
        pass


def test_validate_namespace_accepts_max_length_48():
    records.validate_namespace("a" * 48)  # không raise


# ---------------------------------------------------------------------------
# record_note_path / build_record_note_set_url
# ---------------------------------------------------------------------------


def test_record_note_path_format():
    assert records.record_note_path("nguyenvana", 1788226783) == "/kv/nguyenvana/log-1788226783"


def test_record_key_format():
    assert records.record_key(1788226783) == "log-1788226783"


def test_build_record_note_set_url_has_if_absent_by_default():
    url = records.build_record_note_set_url(
        "https://technocore.chat", "nguyenvana", 1788226783, "hello world"
    )
    assert url == (
        "https://technocore.chat/kv/nguyenvana/log-1788226783/set/hello%20world?if_absent=1"
    )


def test_build_record_note_set_url_if_absent_false():
    url = records.build_record_note_set_url(
        "https://technocore.chat", "nguyenvana", 1, "x", if_absent=False
    )
    assert "if_absent" not in url


def test_build_record_note_set_url_quotes_value():
    url = records.build_record_note_set_url(
        "https://technocore.chat", "ns", 1, "type:guide url:https://x.com desc:a b"
    )
    assert "%3A" in url  # dấu ':' trong value đã được quote
    assert "%20" in url  # khoảng trắng trong value đã được quote


# ---------------------------------------------------------------------------
# RecordStore — mỗi record một file log-<ts>.json trong thư mục
# ---------------------------------------------------------------------------


def _entry(**overrides) -> "records.RecordEntry":
    base = dict(
        did=_DID,
        room="lobby",
        namespace="nguyenvana",
        ts=1788226783,
        nonce=123,
        type="guide",
        url="https://x.com/a/status/1",
        desc="đóng góp thử",
        text="Đã publish: đóng góp thử — chi tiết: /kv/nguyenvana/log-1788226783",
        note_path="/kv/nguyenvana/log-1788226783",
        created_at="2026-08-31T00:00:00Z",
        seq=42,
    )
    base.update(overrides)
    return records.RecordEntry(**base)


def test_record_store_starts_empty():
    with tempfile.TemporaryDirectory() as tmp:
        store = records.RecordStore(Path(tmp) / "records")
        assert store.list_all() == []


def test_record_store_add_and_list_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        store = records.RecordStore(Path(tmp) / "records")
        store.add(_entry())
        store.add(_entry(ts=1788226800, nonce=124, desc="đóng góp thứ hai"))

        entries = store.list_all()
        assert len(entries) == 2
        assert entries[0].desc == "đóng góp thử"
        assert entries[1].desc == "đóng góp thứ hai"


def test_record_store_writes_one_file_per_record():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "records"
        store = records.RecordStore(d)
        store.add(_entry(ts=1788226783))
        store.add(_entry(ts=1788226800, nonce=124))

        files = sorted(p.name for p in d.glob("log-*.json"))
        assert files == ["log-1788226783.json", "log-1788226800.json"]


def test_record_store_file_content_matches_note_path():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "records"
        records.RecordStore(d).add(_entry(ts=1788226783))
        f = d / "log-1788226783.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["note_path"] == "/kv/nguyenvana/log-1788226783"


def test_record_store_persists_across_reopen():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "records"
        records.RecordStore(d).add(_entry())

        reopened = records.RecordStore(d)
        assert len(reopened.list_all()) == 1
        assert reopened.list_all()[0].did == _DID


def test_record_store_list_for_did_filters():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "records"
        store = records.RecordStore(d)
        store.add(_entry())
        store.add(
            _entry(
                did="did:key:zOtherIdentityNotMatching1111111111111111",
                ts=1788226800,
                nonce=999,
            )
        )

        mine = store.list_for_did(_DID)
        assert len(mine) == 1
        assert mine[0].did == _DID


def test_record_store_list_all_sorted_by_ts():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "records"
        store = records.RecordStore(d)
        store.add(_entry(ts=1788226900, nonce=2))
        store.add(_entry(ts=1788226800, nonce=1))

        entries = store.list_all()
        assert [e.ts for e in entries] == [1788226800, 1788226900]


def test_record_store_file_permissions_0600():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "records"
        records.RecordStore(d).add(_entry())
        f = d / "log-1788226783.json"
        mode = f.stat().st_mode & 0o777
        assert mode == 0o600


def test_record_store_ignores_corrupt_file():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "records"
        d.mkdir(parents=True)
        (d / "log-999.json").write_text("{not valid json", encoding="utf-8")
        store = records.RecordStore(d)
        assert store.list_all() == []  # không crash, chỉ bỏ qua file hỏng


def test_record_store_list_all_on_missing_dir_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        store = records.RecordStore(Path(tmp) / "chua-tung-tao")
        assert store.list_all() == []
