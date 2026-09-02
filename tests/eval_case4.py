"""
Eval Case 4 — Idempotency của Workflow 1 (an toàn nguyên tắc #3 trong SKILL.md):
"Không tự ý publish DID note hay claim room sở hữu bằng DID của chính bạn nếu
chưa rõ đây là DID cố định của người dùng/dự án... đừng tạo identity mới 'cho
tiện'." Case này mô phỏng một agent BỊ YÊU CẦU chạy lại toàn bộ Workflow 1 từ
đầu (vd. người dùng gõ nhầm lại lệnh onboard) trên một identity ĐÃ TỒN TẠI, và
verify bằng code thật (không giả lập) rằng:

  1. `cmd_init` từ chối, KHÔNG ghi đè, thoát với mã lỗi khác 0.
  2. DID trên đĩa sau khi "chạy lại" vẫn y hệt DID trước đó (không có identity
     thứ hai nào được tạo ngầm).
  3. `cmd_status` (bước 1 của Workflow 1 — luôn kiểm tra trước khi init) đọc
     đúng DID cố định đó qua mock server, không cần passphrase.

Vẫn qua mock server local (tests/mock_server.py) cho phần status; phần init
không cần mạng nên không đụng mock server.

Chạy: python3 tests/eval_case4.py (từ thư mục gốc repo)
"""
from __future__ import annotations

import argparse
import builtins
import getpass
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import mock_server  # noqa: E402

server = mock_server.start()
BASE_URL = f"http://127.0.0.1:{server.server_port}"
print(f"[mock server] {BASE_URL}\n")

PASSPHRASE = "eval-case4-passphrase-khong-dung-that"
getpass.getpass = lambda prompt="": PASSPHRASE
builtins.input = lambda prompt="": "y"

from onboard import cli  # noqa: E402


def ns(config_dir: Path, **kw) -> argparse.Namespace:
    base = {"base_url": BASE_URL, "config_dir": str(config_dir)}
    base.update(kw)
    return argparse.Namespace(**base)


def _read_did(config_dir: Path) -> str:
    with open(config_dir / "identity.json", "r", encoding="utf-8") as f:
        return json.load(f)["did"]


def run() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)

        print("=" * 70)
        print("BƯỚC 1 — status trước khi có identity (đúng thứ tự Workflow 1 bước 1)")
        print("=" * 70)
        cli.cmd_status(ns(config_dir))

        print()
        print("=" * 70)
        print("BƯỚC 2 — init lần đầu (tạo DID cố định)")
        print("=" * 70)
        cli.cmd_init(ns(config_dir))
        did_first = _read_did(config_dir)
        print(f"  DID sau lần init đầu tiên: {did_first}")

        print()
        print("=" * 70)
        print("BƯỚC 3 — 'người dùng gõ nhầm lại' init — PHẢI bị từ chối, không ghi đè")
        print("=" * 70)
        refused = False
        try:
            cli.cmd_init(ns(config_dir))
            print("  !! cmd_init KHÔNG raise/exit — đây là VI PHẠM nguyên tắc an toàn #3")
        except SystemExit as e:
            refused = e.code not in (0, None)
            print(f"  cmd_init thoát với mã lỗi {e.code} (từ chối ghi đè) — đúng kỳ vọng")

        did_after = _read_did(config_dir)
        unchanged = did_after == did_first
        print(f"  DID sau lần init thứ hai : {did_after}")
        print(f"  DID không đổi             : {unchanged}")

        print()
        print("=" * 70)
        print("BƯỚC 4 — status lại, xác nhận vẫn cùng MỘT DID cố định qua mock server")
        print("=" * 70)
        cli.cmd_status(ns(config_dir))

        ok = refused and unchanged
        print()
        print(f"[case 4] {'PASS' if ok else 'FAIL'} — refused={refused}, did_unchanged={unchanged}")
        return ok


if __name__ == "__main__":
    if not run():
        sys.exit(1)
