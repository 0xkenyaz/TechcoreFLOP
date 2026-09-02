"""
Eval Case 3 — "mình vừa đăng bài hướng dẫn, ghi nhận việc này lên Technocore giúp mình"
Đi đúng Workflow 4 trong SKILL.md: note bền vững (không ký) tại namespace của
người dùng, rồi message ký ngắn trỏ tới note đó. Vẫn qua mock server local.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, ".")
import mock_server  # noqa: E402

server = mock_server.start()
BASE_URL = f"http://127.0.0.1:{server.server_port}"
print(f"[mock server] {BASE_URL}\n")

from identity import keystore, signing  # noqa: E402
from onboard import client  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    keystore_path = Path(tmp) / "identity.json"
    nonces_path = Path(tmp) / "nonces.json"

    did = keystore.generate_and_save(keystore_path, "eval-case3-pw")
    ident = keystore.load(keystore_path, "eval-case3-pw")
    print(f"(Giả định: người dùng đã có identity từ trước — DID: {did})\n")

    # --- bước 1: note bền vững tại namespace riêng của người dùng ---
    ns = "nguyenvana"  # nick người dùng đã chọn lúc publish, khớp ^[a-z0-9][a-z0-9_-]{0,47}$
    ts = int(time.time())
    key = f"log-{ts}"
    url_slug = "https://example-blog.vn/bai-huong-dan-technocore"
    value = signing.single_line_sweep(
        f"type:guide url:{url_slug} desc:Hướng dẫn dùng Technocore bằng tiếng Việt cho người mới"
    )
    note_url = f"{BASE_URL}/kv/{quote(ns, safe='')}/{quote(key, safe='')}/set/{quote(value, safe='')}?if_absent=1"

    print("=" * 70)
    print("BƯỚC 1 — build note bền vững (world-writable, KHÔNG cần ký)")
    print("=" * 70)
    print(f"  Namespace : {ns}")
    print(f"  Key       : {key}")
    print(f"  Nội dung  : {value}")
    print(f"  URL       : {note_url}")
    print()
    print("  [Xin xác nhận người dùng trước khi gửi — Workflow 5 bước 2]")
    print("  Xác nhận gửi note? [y/N]: y  (giả lập người dùng đồng ý)")
    resp = client.send_prebuilt_url(note_url)
    print(f"  -> HTTP {resp.status}: {resp.body.strip()}")

    # --- bước 2: message ký ngắn trỏ tới note ---
    print()
    print("=" * 70)
    print("BƯỚC 2 — message ký ngắn trong lobby, TRỎ tới note (không nhắc lại toàn bộ)")
    print("=" * 70)
    nonces = signing.NonceStore(nonces_path)
    nonce = nonces.next_nonce(ident.did, "lobby")
    text = f"Đã publish: hướng dẫn Technocore tiếng Việt — chi tiết: /kv/{ns}/{key}"
    signed = signing.sign_say(ident, "lobby", text, nonce)
    say_url = signing.build_say_signed_url(BASE_URL, signed)
    print(f"  Nội dung  : {signed['text']}")
    print(f"  Nonce     : {nonce}")
    print(f"  URL       : {say_url}")
    print()
    print("  [Xin xác nhận người dùng trước khi gửi — Workflow 5 bước 2]")
    print("  Xác nhận gửi? [y/N]: y  (giả lập người dùng đồng ý)")
    resp2 = client.send_prebuilt_url(say_url)
    print(f"  -> HTTP {resp2.status}: {resp2.body.strip()}")

    # --- bước 3: biên nhận ---
    print()
    print("=" * 70)
    print("BƯỚC 3 — biên nhận (receipt) người dùng nên lưu lại, tự verify bằng curl")
    print("=" * 70)
    import re

    seq_match = re.search(r"seq=(\d+)", resp2.body)
    seq = seq_match.group(1) if seq_match else "<seq>"
    print(f"  curl '{BASE_URL}/kv/{ns}/{key}'")
    print(f"  curl '{BASE_URL}/r/lobby?since={int(seq) - 1}&limit=1'")

print("\n[case 3] HOÀN TẤT — không đụng technocore.chat thật, không có airdrop/token nào bị nhắc tới.")
