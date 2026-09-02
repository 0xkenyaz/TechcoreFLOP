"""
Eval Case 5 — "mình muốn sở hữu một room bounty, chỉ cho vài người bạn ghi vào"
Đi đúng chuỗi mô tả trong docs/llms-vi.md mục OWNED ROOMS / docs/patterns-vi.md
mẫu số 5: claim d-<room> lần đầu, rồi cập nhật allow-list — dùng CLI thật
(`onboard.cli.cmd_room_claim` / `cmd_room_allow`), qua mock server local.

Cũng verify nhánh an toàn: một identity KHÔNG phải chủ sở hữu thử ghi
room-allow phải bị server (mock) từ chối 403 — CLI phải thoát khác 0, không
được âm thầm coi là thành công.

Chạy: python3 tests/eval_case5.py (từ thư mục gốc repo)
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

PASSPHRASE = "eval-case5-passphrase-khong-dung-that"
_confirm_answers = iter(["y", "y"])  # đồng ý claim, đồng ý allow


def _fake_input(prompt=""):
    print(prompt, end="")
    ans = next(_confirm_answers, "y")
    print(ans)
    return ans


getpass.getpass = lambda prompt="": PASSPHRASE
builtins.input = _fake_input

from onboard import cli, client  # noqa: E402
from identity import keystore  # noqa: E402

ROOM = "eval-bounty-room"
D_ROOM = f"d-{ROOM}"


def ns(config_dir, **kw):
    base = {"base_url": BASE_URL, "config_dir": str(config_dir)}
    base.update(kw)
    return argparse.Namespace(**base)


with tempfile.TemporaryDirectory() as tmp_owner:
    owner_config = Path(tmp_owner)

    print("=" * 70)
    print("BƯỚC 1 — init identity của chủ sở hữu tương lai")
    print("=" * 70)
    cli.cmd_init(ns(owner_config))

    import json

    owner_did = json.loads((owner_config / "identity.json").read_text())["did"]
    print(f"(owner DID: {owner_did})\n")

    print("=" * 70)
    print(f"BƯỚC 2 — room-claim --room {ROOM} (lần đầu, phải thành công)")
    print("=" * 70)
    cli.cmd_room_claim(ns(owner_config, room=ROOM, yes=False))

    owner_note = client.read_note(BASE_URL, f"/kv/room-owners/{D_ROOM}")
    assert owner_note is not None, "note room-owners phải tồn tại sau khi claim"
    assert owner_did in owner_note.body, "note room-owners phải chứa đúng DID chủ sở hữu"
    print("\n[verify độc lập] note room-owners khớp đúng DID chủ sở hữu — OK\n")

    with tempfile.TemporaryDirectory() as tmp_friend:
        friend_config = Path(tmp_friend)
        cli.cmd_init(ns(friend_config))
        friend_did = json.loads((friend_config / "identity.json").read_text())["did"]
        print(f"(friend DID được thêm vào allow-list: {friend_did})\n")

        print("=" * 70)
        print(f"BƯỚC 3 — room-allow --room {ROOM} --dids <friend> (chủ sở hữu ký, phải thành công)")
        print("=" * 70)
        cli.cmd_room_allow(ns(owner_config, room=ROOM, dids=[friend_did], yes=False))

        allow_note = client.read_note(BASE_URL, f"/kv/room-allow/{D_ROOM}")
        assert allow_note is not None
        assert friend_did in allow_note.body
        print("\n[verify độc lập] note room-allow chứa đúng DID bạn bè — OK\n")

    with tempfile.TemporaryDirectory() as tmp_intruder:
        intruder_config = Path(tmp_intruder)
        cli.cmd_init(ns(intruder_config))
        intruder_did = json.loads((intruder_config / "identity.json").read_text())["did"]
        print(f"(intruder DID, KHÔNG phải chủ sở hữu: {intruder_did})\n")

        print("=" * 70)
        print("BƯỚC 4 — room-allow bởi identity KHÔNG phải chủ sở hữu (phải bị từ chối)")
        print("=" * 70)
        try:
            cli.cmd_room_allow(ns(intruder_config, room=ROOM, dids=[intruder_did], yes=False))
            raised = False
        except SystemExit as e:
            raised = e.code != 0
        assert raised, "cmd_room_allow phải sys.exit(khác 0) khi không phải chủ sở hữu"
        print("\n[verify] intruder bị từ chối đúng như kỳ vọng (403 -> exit khác 0) — OK\n")

        # allow-list KHÔNG được đổi bởi lượt ghi bị từ chối ở trên.
        allow_note_after = client.read_note(BASE_URL, f"/kv/room-allow/{D_ROOM}")
        assert allow_note_after is not None and intruder_did not in allow_note_after.body
        print("[verify] allow-list KHÔNG bị intruder ghi đè — OK\n")

print("[case 5] HOÀN TẤT — room-claim/room-allow đi qua đúng code path CLI thật,")
print("qua mock server local, KHÔNG chạm technocore.chat thật.")
