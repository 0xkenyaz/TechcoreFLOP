"""
Eval Case 1 — "giúp mình tạo DID và gửi tin nhắn đầu tiên vào lobby"
Mô phỏng agent làm đúng Workflow 1 trong SKILL.md, nhưng trỏ --base-url vào
mock server local thay vì technocore.chat thật. Monkeypatch getpass/input để
chạy không tương tác (vẫn đi qua ĐÚNG code path thật của cli.py, không giả lập).
"""
from __future__ import annotations

import argparse
import getpass
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import mock_server  # noqa: E402

server = mock_server.start()
BASE_URL = f"http://127.0.0.1:{server.server_port}"
print(f"[mock server] {BASE_URL}\n")

# --- monkeypatch để chạy non-interactive, nhưng vẫn qua đúng cmd_* thật ---
PASSPHRASE = "eval-case1-passphrase-khong-dung-that"
_confirm_answers = iter(["y", "y"])  # đồng ý publish, đồng ý hello

getpass.getpass = lambda prompt="": PASSPHRASE
_builtin_input = input


def _fake_input(prompt=""):
    print(prompt, end="")
    ans = next(_confirm_answers, "y")
    print(ans)
    return ans


import builtins  # noqa: E402
builtins.input = _fake_input

from onboard import cli  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    config_dir = Path(tmp)

    def ns(**kw):
        base = {"base_url": BASE_URL, "config_dir": str(config_dir)}
        base.update(kw)
        return argparse.Namespace(**base)

    print("=" * 70)
    print("BƯỚC 1 — status (kiểm tra đã có identity chưa)")
    print("=" * 70)
    cli.cmd_status(ns())

    print()
    print("=" * 70)
    print("BƯỚC 2 — init (tạo DID mới)")
    print("=" * 70)
    cli.cmd_init(ns())

    print()
    print("=" * 70)
    print("BƯỚC 3 — publish (công khai DID note, có hỏi xác nhận)")
    print("=" * 70)
    cli.cmd_publish(ns(nick="eval-tester", force=False, yes=False))

    print()
    print("=" * 70)
    print("BƯỚC 4 — hello (gửi tin nhắn ký đầu tiên vào lobby, có hỏi xác nhận)")
    print("=" * 70)
    cli.cmd_hello(ns(
        room="lobby",
        message="Chào từ Technocore Việt — dự án tài liệu và công cụ tiếng Việt.",
        yes=False,
    ))

    print()
    print("=" * 70)
    print("BƯỚC 5 — status lại (xác nhận đã publish + thấy message trong lobby)")
    print("=" * 70)
    cli.cmd_status(ns())

print("\n[case 1] HOÀN TẤT — toàn bộ đi qua mock server local, KHÔNG chạm technocore.chat thật.")
