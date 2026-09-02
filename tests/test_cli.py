"""
Test cho onboard/cli.py.

Phần 1 — cờ `--max-retries-503`: monkeypatch thẳng `client.send_prebuilt_url`
để bắt kwargs, không cần mock server đầy đủ (tests/test_client.py đã cover
retry/backoff ở tầng client.get() rồi).

Phần 2 — lệnh `record`/`record-sheet`: chạy CLI THẬT qua mock_server.py local
(giống tests/eval_case5.py), verify cả hai lượt ghi (room + note bền vững) và
nội dung record-sheet xuất ra.

Chạy: python3 -m pytest tests/test_cli.py -v
"""

from __future__ import annotations

import argparse
import builtins
import getpass as getpass_module
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mock_server  # noqa: E402
from identity import keystore  # noqa: E402
from onboard import cli, client, records  # noqa: E402


# ---------------------------------------------------------------------------
# Phần 1 — Parsing --max-retries-503
# ---------------------------------------------------------------------------


def test_max_retries_503_default_matches_client_default():
    args = cli.build_parser().parse_args(["publish", "-y"])
    assert args.max_retries_503 == client.DEFAULT_MAX_RETRIES_503


def test_max_retries_503_custom_value_parsed():
    args = cli.build_parser().parse_args(["--max-retries-503", "7", "hello", "-y"])
    assert args.max_retries_503 == 7


def test_max_retries_503_zero_parsed():
    args = cli.build_parser().parse_args(["--max-retries-503", "0", "room-claim", "--room", "x", "-y"])
    assert args.max_retries_503 == 0


def test_max_retries_503_is_top_level_not_per_subcommand():
    try:
        cli.build_parser().parse_args(["publish", "--max-retries-503", "2", "-y"])
        assert False, "argparse phải từ chối --max-retries-503 sau tên lệnh con"
    except SystemExit:
        pass


def _make_identity(tmp: str) -> Path:
    path = Path(tmp) / "identity.json"
    keystore.generate_and_save(path, "pw-test")
    return path


def _patch_confirm_flow(monkeypatch):
    monkeypatch.setattr(cli.getpass, "getpass", lambda *a, **k: "pw-test")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")


def _capture_send_prebuilt_url(monkeypatch):
    calls = []

    def fake(url, **kwargs):
        calls.append(kwargs)
        return client.Response(status=200, body="ok", budget=None)

    monkeypatch.setattr(client, "send_prebuilt_url", fake)
    return calls


def test_publish_passes_max_retries_503_through(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        identity_path = _make_identity(tmp)
        _patch_confirm_flow(monkeypatch)
        calls = _capture_send_prebuilt_url(monkeypatch)

        args = argparse.Namespace(
            config_dir=tmp,
            base_url=client.DEFAULT_BASE_URL,
            nick=None,
            force=False,
            yes=True,
            dry_run=False,
            max_retries_503=5,
        )
        cli.cmd_publish(args)

        assert identity_path.exists()
        assert len(calls) == 1
        assert calls[0]["max_retries_503"] == 5


def test_hello_passes_max_retries_503_through(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _make_identity(tmp)
        _patch_confirm_flow(monkeypatch)
        calls = _capture_send_prebuilt_url(monkeypatch)
        monkeypatch.setattr(client, "find_own_message_seq", lambda *a, **k: None)

        args = argparse.Namespace(
            config_dir=tmp,
            base_url=client.DEFAULT_BASE_URL,
            room="lobby",
            message="test",
            yes=True,
            dry_run=False,
            max_retries_503=0,
        )
        cli.cmd_hello(args)

        assert len(calls) == 1
        assert calls[0]["max_retries_503"] == 0


def test_room_claim_passes_max_retries_503_through(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _make_identity(tmp)
        _patch_confirm_flow(monkeypatch)
        calls = _capture_send_prebuilt_url(monkeypatch)
        monkeypatch.setattr(client, "get_room_nonce", lambda *a, **k: 0)

        args = argparse.Namespace(
            config_dir=tmp,
            base_url=client.DEFAULT_BASE_URL,
            room="minh-hoa",
            yes=True,
            max_retries_503=2,
        )
        cli.cmd_room_claim(args)

        assert len(calls) == 1
        assert calls[0]["max_retries_503"] == 2


def test_room_allow_passes_max_retries_503_through(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _make_identity(tmp)
        _patch_confirm_flow(monkeypatch)
        calls = _capture_send_prebuilt_url(monkeypatch)
        monkeypatch.setattr(client, "get_room_nonce", lambda *a, **k: 3)

        args = argparse.Namespace(
            config_dir=tmp,
            base_url=client.DEFAULT_BASE_URL,
            room="minh-hoa",
            dids=["did:key:zExample1111111111111111111111111111111111"],
            yes=True,
            max_retries_503=9,
        )
        cli.cmd_room_allow(args)

        assert len(calls) == 1
        assert calls[0]["max_retries_503"] == 9


# ---------------------------------------------------------------------------
# Phần 2 — record / record-sheet, end-to-end qua mock server local thật
# ---------------------------------------------------------------------------


def _ns(base_url: str, config_dir, **kw) -> argparse.Namespace:
    base = {"base_url": base_url, "config_dir": str(config_dir), "max_retries_503": 3}
    base.update(kw)
    return argparse.Namespace(**base)


def _record_ns(base_url: str, config_dir, **kw) -> argparse.Namespace:
    """Namespace mặc định cho cmd_record theo đúng args mới (Workflow 4)."""
    base = {
        "namespace": "tester",
        "type": "guide",
        "url": "https://x.com/example/status/1",
        "desc": "Viết tài liệu tiếng Việt",
        "message": None,
        "room": "lobby",
        "yes": True,
        "dry_run": False,
    }
    base.update(kw)
    return _ns(base_url, config_dir, **base)


def test_record_writes_room_message_and_persistent_note(monkeypatch):
    server = mock_server.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            monkeypatch.setattr(getpass_module, "getpass", lambda *a, **k: "pw-test")
            monkeypatch.setattr(builtins, "input", lambda *a, **k: "y")

            cli.cmd_init(_ns(base_url, config_dir))

            with open(config_dir / "identity.json", "r", encoding="utf-8") as f:
                import json as _json

                did = _json.load(f)["did"]

            cli.cmd_record(_record_ns(base_url, config_dir, namespace="tester"))

            store = records.RecordStore(config_dir / "records")
            entries = store.list_for_did(did)
            assert len(entries) == 1
            entry = entries[0]
            assert entry.namespace == "tester"
            assert entry.note_path.startswith("/kv/tester/log-")
            assert entry.text.startswith("Đã publish: Viết tài liệu tiếng Việt")
            assert entry.url == "https://x.com/example/status/1"
            assert entry.seq is not None

            # Note bền vững phải thật sự nằm trên mock server, đọc lại được.
            note_resp = client.read_note(base_url, entry.note_path)
            assert note_resp is not None
            assert "url:https://x.com/example/status/1" in note_resp.body
            assert "type:guide" in note_resp.body

            # File local phải đúng tên log-<ts>.json khớp với note_path.
            files = list((config_dir / "records").glob("log-*.json"))
            assert len(files) == 1
    finally:
        server.shutdown()


def test_record_rejects_invalid_namespace_before_any_network_call(monkeypatch):
    server = mock_server.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            monkeypatch.setattr(getpass_module, "getpass", lambda *a, **k: "pw-test")

            def _boom(*a, **k):
                raise AssertionError("không được hỏi xác nhận khi namespace không hợp lệ")

            monkeypatch.setattr(builtins, "input", _boom)

            cli.cmd_init(_ns(base_url, config_dir))
            try:
                cli.cmd_record(_record_ns(base_url, config_dir, namespace="Khong Hop Le"))
                assert False, "phải sys.exit khi namespace không hợp lệ"
            except SystemExit as e:
                assert e.code != 0

            # Không có record nào được lưu cục bộ, không nonce nào bị đốt.
            assert not (config_dir / "records").exists()
            assert not (config_dir / "nonces.json").exists()
    finally:
        server.shutdown()


def test_record_dry_run_does_not_hit_network_or_burn_nonce(monkeypatch):
    server = mock_server.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            monkeypatch.setattr(getpass_module, "getpass", lambda *a, **k: "pw-test")
            monkeypatch.setattr(builtins, "input", lambda *a, **k: "y")

            cli.cmd_init(_ns(base_url, config_dir))
            cli.cmd_record(_record_ns(base_url, config_dir, dry_run=True))

            # Không có gì được lưu cục bộ, không nonce nào bị đốt.
            assert not (config_dir / "records").exists()
            assert not (config_dir / "nonces.json").exists()
    finally:
        server.shutdown()


def test_record_sheet_includes_disclaimer_and_no_reward_promise(monkeypatch):
    server = mock_server.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            monkeypatch.setattr(getpass_module, "getpass", lambda *a, **k: "pw-test")
            monkeypatch.setattr(builtins, "input", lambda *a, **k: "y")

            cli.cmd_init(_ns(base_url, config_dir))
            cli.cmd_publish(_ns(base_url, config_dir, nick="tester", force=False, yes=True, dry_run=False))
            cli.cmd_record(
                _record_ns(
                    base_url,
                    config_dir,
                    namespace="tester",
                    desc="đóng góp thử nghiệm",
                    url="https://x.com/example/status/2",
                )
            )

            cli.cmd_record_sheet(_ns(base_url, config_dir, out=None))

            sheet_path = config_dir / "record-sheet.md"
            assert sheet_path.exists()
            content = sheet_path.read_text(encoding="utf-8")

            assert "đóng góp thử nghiệm" in content
            assert "https://x.com/example/status/2" in content
            assert "Đã publish" in content  # DID note đã publish, phải phản ánh đúng
            assert "không đại diện cho" in content  # disclaimer phải có mặt
            assert "không biết gì về bất kỳ token" in content
            # KHÔNG được tự bịa hứa hẹn phần thưởng — chỉ chấp nhận từ "token"
            # xuất hiện đúng trong câu disclaimer phủ định, không ở đâu khác.
            token_mentions = content.count("token")
            assert token_mentions == 1, f"chỉ nên nhắc 'token' đúng 1 lần (trong disclaimer), thấy {token_mentions}"
    finally:
        server.shutdown()


def test_record_sheet_without_any_record_still_works(monkeypatch):
    server = mock_server.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            monkeypatch.setattr(getpass_module, "getpass", lambda *a, **k: "pw-test")
            monkeypatch.setattr(builtins, "input", lambda *a, **k: "y")

            cli.cmd_init(_ns(base_url, config_dir))
            cli.cmd_record_sheet(_ns(base_url, config_dir, out=None))

            content = (config_dir / "record-sheet.md").read_text(encoding="utf-8")
            assert "chưa có record nào" in content
            assert "CHƯA publish" in content


    finally:
        server.shutdown()


def test_record_passes_max_retries_503_through(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _make_identity(tmp)
        _patch_confirm_flow(monkeypatch)
        calls = _capture_send_prebuilt_url(monkeypatch)
        monkeypatch.setattr(client, "find_own_message_seq", lambda *a, **k: 7)

        args = argparse.Namespace(
            config_dir=tmp,
            base_url=client.DEFAULT_BASE_URL,
            namespace="tester",
            type="guide",
            url="https://x.com/example/status/1",
            desc="test",
            message=None,
            room="lobby",
            yes=True,
            dry_run=False,
            max_retries_503=4,
        )
        cli.cmd_record(args)

        assert len(calls) == 2  # note bền vững + tin nhắn room
        assert all(c["max_retries_503"] == 4 for c in calls)
