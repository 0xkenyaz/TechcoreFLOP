"""
Eval Case 6 — `--dry-run` cho `publish`/`hello`: xem trước nội dung/URL sẽ gửi
mà KHÔNG chạm mạng, không hỏi xác nhận, và (với `hello`) không đốt nonce thật.

Verify bằng code thật (không giả lập):
  1. `cmd_publish(..., dry_run=True)` KHÔNG được gọi `client.send_prebuilt_url`
     (patch hàm này để raise nếu bị gọi) — nếu dry-run mà vẫn gửi mạng, đây là
     một bug an toàn nghiêm trọng, không phải chi tiết vặt.
  2. Sau dry-run, DID note vẫn CHƯA tồn tại trên mock server (chưa publish thật).
  3. `cmd_hello(..., dry_run=True)` cũng không gọi mạng, và `nonces.json` KHÔNG
     được tạo ra (peek_next_nonce không lưu) — nonce thật đầu tiên sau đó vẫn
     bắt đầu sạch từ số hợp lệ, không bị "mất" một nonce cho message ảo.
  4. Room lobby trên mock server vẫn rỗng sau dry-run.

Chạy: python3 tests/eval_case6.py (từ thư mục gốc repo)
"""
from __future__ import annotations

import argparse
import builtins
import getpass
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import mock_server  # noqa: E402

server = mock_server.start()
BASE_URL = f"http://127.0.0.1:{server.server_port}"
print(f"[mock server] {BASE_URL}\n")

PASSPHRASE = "eval-case6-passphrase-khong-dung-that"
getpass.getpass = lambda prompt="": PASSPHRASE


def _fail_if_called(*a, **kw):
    raise AssertionError(
        "client.send_prebuilt_url() KHÔNG được gọi trong --dry-run — đây là bug an toàn."
    )


def _fail_if_input_called(prompt=""):
    raise AssertionError("--dry-run KHÔNG được hỏi xác nhận ([y/N]) — phải tự dừng trước đó.")


builtins.input = _fail_if_input_called

from onboard import cli, client  # noqa: E402
from identity import did as did_mod  # noqa: E402

real_send = client.send_prebuilt_url
client.send_prebuilt_url = _fail_if_called

with tempfile.TemporaryDirectory() as tmp:
    config_dir = Path(tmp)

    def ns(**kw):
        base = {"base_url": BASE_URL, "config_dir": str(config_dir)}
        base.update(kw)
        return argparse.Namespace(**base)

    print("=" * 70)
    print("BƯỚC 1 — init")
    print("=" * 70)
    cli.cmd_init(ns())

    import json

    did = json.loads((config_dir / "identity.json").read_text())["did"]
    shard, key = did_mod.fingerprint_shard_path(did)
    note_path = f"/kv/did-{shard}/{key}"

    print()
    print("=" * 70)
    print("BƯỚC 2 — publish --dry-run (không hỏi xác nhận, không gửi mạng)")
    print("=" * 70)
    cli.cmd_publish(ns(nick="dry-run-tester", force=False, yes=False, dry_run=True))

    note_after = client.read_note(BASE_URL, note_path)
    assert note_after is None, "DID note KHÔNG được tồn tại trên server sau --dry-run"
    print("\n[verify] DID note chưa được publish thật — OK\n")

    print("=" * 70)
    print("BƯỚC 3 — hello --dry-run (không hỏi xác nhận, không gửi mạng, không đốt nonce)")
    print("=" * 70)
    nonces_path = config_dir / "nonces.json"
    cli.cmd_hello(ns(room="lobby", message="Tin nhắn xem trước, chưa gửi thật.", yes=False, dry_run=True))

    assert not nonces_path.exists(), "nonces.json KHÔNG được tạo ra chỉ vì --dry-run"
    print("\n[verify] nonces.json chưa được tạo (peek không lưu) — OK")

    room_after = client.read_room(BASE_URL, "lobby", limit=5, fmt="json")
    assert '"count": 0' in room_after.body, "lobby PHẢI vẫn rỗng sau hello --dry-run"
    print("[verify] lobby trên mock server vẫn rỗng sau --dry-run — OK\n")

    print("=" * 70)
    print("BƯỚC 4 — hello THẬT (không --dry-run) ngay sau đó, để chắc nonce vẫn đúng quy tắc")
    print("=" * 70)
    builtins.input = lambda prompt="": (print(f"{prompt}y"), "y")[1]
    client.send_prebuilt_url = real_send
    cli.cmd_hello(ns(room="lobby", message="Tin nhắn thật, gửi sau khi đã preview.", yes=False, dry_run=False))

    assert nonces_path.exists(), "nonces.json phải được tạo sau lượt gửi THẬT"
    room_final = client.read_room(BASE_URL, "lobby", limit=5, fmt="json")
    assert '"count": 1' in room_final.body, "lobby phải có đúng 1 message sau lượt gửi thật"
    print("\n[verify] gửi thật sau dry-run vẫn hoạt động đúng, nonce không bị lệch — OK\n")

print("[case 6] HOÀN TẤT — --dry-run không chạm mạng, không hỏi xác nhận, không để lại")
print("trạng thái cục bộ giả (nonce ảo), và không cản trở lượt gửi thật ngay sau đó.")
