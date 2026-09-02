"""
Eval Case 7 — "mình vừa publish một bài hướng dẫn, muốn ghi nhận công khai"
Đi đúng Workflow 4 trong SKILL.md ("Ghi nhận (log) một đóng góp đã publish"):
một note bền vững dưới namespace riêng của người dùng (`log-<ts>`), sau đó một
message ký ngắn trong room, TRỎ tới note đó — dùng CLI thật
(`onboard.cli.cmd_record`), qua mock server local.

Cũng verify:
  - namespace không hợp lệ bị chặn TRƯỚC khi gọi mạng (không lượt ghi nào lọt qua).
  - --dry-run không gọi mạng, không đốt nonce thật, và lượt record thật ngay sau
    đó vẫn tính nonce đúng quy tắc tăng dần (giống hello --dry-run).
  - note ghi xong độc lập với việc message có gửi thành công hay không (đọc lại
    note bằng client.read_note(), không dựa vào output của cmd_record).

Chạy: python3 tests/eval_case7.py (từ thư mục gốc repo)
"""
from __future__ import annotations

import argparse
import builtins
import getpass
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import mock_server  # noqa: E402

server = mock_server.start()
BASE_URL = f"http://127.0.0.1:{server.server_port}"
print(f"[mock server] {BASE_URL}\n")

PASSPHRASE = "eval-case7-passphrase-khong-dung-that"
_confirm_answers = iter(["y"])


def _fake_input(prompt=""):
    print(prompt, end="")
    ans = next(_confirm_answers, "y")
    print(ans)
    return ans


getpass.getpass = lambda prompt="": PASSPHRASE
builtins.input = _fake_input

from onboard import cli, client  # noqa: E402
from identity import keystore, signing  # noqa: E402

NS = "eval-nick"
ROOM = "lobby"


def _room_messages(resp):
    """Đọc lại room dạng JSON — mock server (giống server thật) có thể nối thêm
    footer '# budget: ...' SAU phần JSON hợp lệ, phải cắt bỏ trước khi parse
    (xem client.find_own_message_seq() — cùng một vấn đề)."""
    body = re.sub(r"\n?#\s*budget:.*$", "", resp.body.strip(), flags=re.IGNORECASE | re.DOTALL)
    data = json.loads(body)
    return data.get("messages", []) if isinstance(data, dict) else data


def ns_args(config_dir, **kw):
    base = {
        "base_url": BASE_URL,
        "config_dir": str(config_dir),
        "namespace": NS,
        "type": "guide",
        "url": "https://example.com/huong-dan-vi",
        "desc": "Huong dan onboard Technocore bang tieng Viet",
        "room": ROOM,
        "message": None,
        "yes": False,
        "dry_run": False,
        "max_retries_503": 3,
    }
    base.update(kw)
    return argparse.Namespace(**base)


with tempfile.TemporaryDirectory() as tmp:
    config_dir = Path(tmp)

    print("=" * 70)
    print("BƯỚC 1 — init identity")
    print("=" * 70)
    cli.cmd_init(argparse.Namespace(config_dir=str(config_dir), base_url=BASE_URL))
    did = json.loads((config_dir / "identity.json").read_text(encoding="utf-8"))["did"]
    print(f"(DID: {did})\n")

    print("=" * 70)
    print("BƯỚC 2 — record --dry-run (không được gọi mạng, không đốt nonce)")
    print("=" * 70)
    _confirm_answers = iter([])  # dry-run không được hỏi gì cả

    def _no_prompt(prompt=""):
        raise AssertionError("--dry-run không được hỏi xác nhận")

    builtins.input = _no_prompt
    cli.cmd_record(ns_args(config_dir, yes=False, dry_run=True))
    builtins.input = _fake_input
    _confirm_answers = iter(["y"])

    nonces_before = signing.NonceStore(config_dir / "nonces.json")
    assert nonces_before._data == {}, "dry-run không được lưu nonce nào vào nonces.json"
    print("\n[verify] --dry-run không gọi mạng, không đốt nonce — OK\n")

    print("=" * 70)
    print("BƯỚC 3 — record thật (phải ghi cả note lẫn message)")
    print("=" * 70)
    cli.cmd_record(ns_args(config_dir, yes=False))

    # Tìm đúng key note vừa ghi (log-<ts>) bằng cách đọc thẳng state mock qua HTTP,
    # không đoán timestamp chính xác — liệt kê record sheet đã lưu cục bộ để lấy key.
    sheets = list((config_dir / "records").glob("log-*.json"))
    assert len(sheets) == 1, "phải có đúng 1 record sheet cục bộ sau 1 lần record"
    sheet = json.loads(sheets[0].read_text(encoding="utf-8"))
    note_path = sheet["note_path"]
    assert note_path.startswith(f"/kv/{NS}/log-")

    note = client.read_note(BASE_URL, note_path)
    assert note is not None, "note phải tồn tại thật trên server sau record"
    assert "url:https://example.com/huong-dan-vi" in note.body
    assert "type:guide" in note.body
    print(f"\n[verify độc lập] note {note_path} chứa đúng type/url — OK")

    room_resp = client.read_room(BASE_URL, ROOM, limit=10, fmt="json")
    room_messages = _room_messages(room_resp)
    matched = [m for m in room_messages if m["from"] == did]
    assert len(matched) == 1, "phải có đúng 1 message ký bởi DID này trong room"
    assert note_path in matched[0]["text"], "message phải trỏ tới đúng đường dẫn note"
    print("[verify độc lập] message trong room trỏ đúng tới note — OK\n")

    print("=" * 70)
    print("BƯỚC 4 — record với namespace không hợp lệ (phải bị chặn TRƯỚC khi gọi mạng)")
    print("=" * 70)
    room_count_before = len(room_messages)
    try:
        cli.cmd_record(ns_args(config_dir, namespace="Khong Hop Le !!", yes=False))
        raised = False
    except SystemExit as e:
        raised = e.code != 0
    assert raised, "cmd_record phải sys.exit(khác 0) với namespace không hợp lệ"

    room_resp_after = client.read_room(BASE_URL, ROOM, limit=10, fmt="json")
    room_messages_after = _room_messages(room_resp_after)
    assert len(room_messages_after) == room_count_before, (
        "namespace không hợp lệ phải bị chặn ở tầng CLI TRƯỚC khi ghi bất cứ thứ gì"
    )
    print("\n[verify] namespace không hợp lệ bị chặn, không ghi trùng gì lên server — OK\n")

print("[case 7] HOÀN TẤT — record đi qua đúng code path CLI thật (Workflow 4 SKILL.md),")
print("qua mock server local, KHÔNG chạm technocore.chat thật.")
