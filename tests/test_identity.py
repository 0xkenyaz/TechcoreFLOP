"""
Test cho lớp identity/signing. Chạy: python3 -m pytest tests/ -v
(hoặc python3 tests/test_identity.py nếu không có pytest)

Các test verify chữ ký bằng CHÍNH cryptography library (không phụ thuộc server
thật) để đảm bảo canonical string đúng như /llms.txt mô tả.
"""

from __future__ import annotations

import sys
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.exceptions import InvalidSignature  # noqa: E402

from identity import did as did_mod  # noqa: E402
from identity import keystore  # noqa: E402
from identity import signing  # noqa: E402


def test_did_roundtrip():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    did = did_mod.public_key_to_did(pub)
    assert did.startswith("did:key:z6Mk") or did.startswith("did:key:z")
    pub2 = did_mod.did_to_public_key(did)
    assert pub2.public_bytes_raw() == pub.public_bytes_raw()
    print(f"  did:key sinh ra: {did}")


def test_did_invalid_rejected():
    try:
        did_mod.did_to_public_key("did:key:zNotValidBase58!!!")
        assert False, "phải ném lỗi với did không hợp lệ"
    except did_mod.InvalidDidKeyError:
        pass


def test_fingerprint_shard_matches_name_regex():
    import re

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    did = did_mod.public_key_to_did(priv.public_key())
    shard, key = did_mod.fingerprint_shard_path(did)
    name_re = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
    assert name_re.match(shard), f"shard '{shard}' không khớp regex tên"
    assert name_re.match(key), f"key '{key}' không khớp regex tên"
    assert len(shard) == 2
    assert len(key) == 14
    print(f"  fingerprint shard/key: {shard}/{key} -> path {did_mod.did_note_path(did)}")


def test_keystore_generate_load_wrong_passphrase():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "identity.json"
        did = keystore.generate_and_save(path, "correct horse battery staple")
        assert did.startswith("did:key:")

        # load đúng passphrase → thành công
        ident = keystore.load(path, "correct horse battery staple")
        assert ident.did == did

        # load sai passphrase → lỗi rõ ràng, không lộ gì
        try:
            keystore.load(path, "wrong passphrase")
            assert False, "phải ném WrongPassphraseError"
        except keystore.WrongPassphraseError:
            pass

        # không ghi đè file đã tồn tại
        try:
            keystore.generate_and_save(path, "another pass")
            assert False, "phải từ chối ghi đè"
        except keystore.KeystoreError:
            pass
        print(f"  keystore roundtrip OK cho {did}")


def test_single_line_sweep_removes_control_and_line_separators():
    raw = "Xin chào\nthế giới\u2028test\u200b(zero width)\x01(control)"
    swept = signing.single_line_sweep(raw)
    assert "\n" not in swept
    assert "\u2028" not in swept
    assert "\u200b" not in swept
    assert "\x01" not in swept
    # không còn ký tự thuộc các category bị quét
    for ch in swept:
        assert unicodedata.category(ch) not in signing._SWEEP_CATEGORIES
    print(f"  sweep: {raw!r} -> {swept!r}")


def test_single_line_sweep_keeps_vietnamese_intact():
    raw = "Chào mừng bạn đến với Technocore Việt — dễ dùng, an toàn, hữu ích."
    swept = signing.single_line_sweep(raw)
    assert swept == raw  # không có ký tự nào trong text này bị sweep


def test_sign_say_verifies_with_cryptography():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "identity.json"
        keystore.generate_and_save(path, "pw")
        ident = keystore.load(path, "pw")

        nonces = signing.NonceStore(Path(tmp) / "nonces.json")
        nonce = nonces.next_nonce(ident.did, "lobby")

        signed = signing.sign_say(ident, "lobby", "Xin chào từ Technocore Việt", nonce)

        # verify độc lập bằng public key + canonical string, y hệt server sẽ làm
        canonical = f"lobby|{nonce}|{signed['text']}".encode("utf-8")
        import base64

        sig_bytes = base64.urlsafe_b64decode(signed["sig"] + "==")
        assert len(signed["sig"]) == 86, "sig phải đúng 86 ký tự base64url không padding"
        ident.public_key.verify(sig_bytes, canonical)  # ném lỗi nếu sai
        print(f"  chữ ký hợp lệ, nonce={nonce}, sig len={len(signed['sig'])}")


def test_sign_say_url_is_well_formed():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "identity.json"
        keystore.generate_and_save(path, "pw")
        ident = keystore.load(path, "pw")
        nonces = signing.NonceStore(Path(tmp) / "nonces.json")
        nonce = nonces.next_nonce(ident.did, "lobby")
        signed = signing.sign_say(ident, "lobby", "hello world", nonce)
        url = signing.build_say_signed_url("https://technocore.chat", signed)
        assert url.startswith("https://technocore.chat/r/lobby/say-signed/did%3Akey%3A")
        assert "%20" in url  # space phải được encode
        print(f"  URL: {url}")


def test_nonce_store_monotonic_increasing():
    with tempfile.TemporaryDirectory() as tmp:
        store = signing.NonceStore(Path(tmp) / "nonces.json")
        n1 = store.next_nonce("did:key:zabc", "lobby")
        n2 = store.next_nonce("did:key:zabc", "lobby")
        n3 = store.next_nonce("did:key:zabc", "other-room")  # room khác, độc lập
        assert n2 > n1
        assert n3 >= 1  # room khác không kế thừa nonce của room trước
        print(f"  nonces: lobby={n1},{n2}  other-room={n3}")


def test_peek_next_nonce_does_not_persist():
    """peek_next_nonce() (dùng cho --dry-run) phải gợi ý đúng nonce SẼ dùng,
    nhưng KHÔNG ghi gì vào file — gọi lặp lại nhiều lần không được làm nonce
    thật (next_nonce) sau đó bị nhảy vọt hay lệch quy tắc tăng dần."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nonces.json"
        store = signing.NonceStore(path)

        peek1 = store.peek_next_nonce("did:key:zabc", "lobby")
        peek2 = store.peek_next_nonce("did:key:zabc", "lobby")
        assert not path.exists(), "peek_next_nonce không được tạo/ghi file"
        assert peek2 >= peek1  # không giảm, nhưng không bắt buộc phải lớn hơn (chưa lưu)

        real = store.next_nonce("did:key:zabc", "lobby")
        assert real >= peek1, "nonce thật đầu tiên phải khớp hoặc lớn hơn giá trị đã preview"

        # Sau real, next_nonce tiếp theo vẫn phải lớn hơn real (đúng quy tắc cũ) —
        # peek ở trên không được để lại trạng thái sai làm hỏng việc này.
        real2 = store.next_nonce("did:key:zabc", "lobby")
        assert real2 > real
        print(f"  peek={peek1},{peek2}  real={real},{real2}")


def test_sign_room_owner_claim_and_allow():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "identity.json"
        keystore.generate_and_save(path, "pw")
        ident = keystore.load(path, "pw")

        claim_nonce = 1000
        claim = signing.sign_room_owner_claim(ident, "jobs", claim_nonce)
        assert claim["room"] == "d-jobs"
        canonical = f"room-owners|d-jobs|{claim_nonce}|{ident.did}".encode("utf-8")
        import base64

        sig_bytes = base64.urlsafe_b64decode(claim["sig"] + "==")
        ident.public_key.verify(sig_bytes, canonical)

        allow_nonce = claim_nonce + 1
        allow = signing.sign_room_allow(ident, "jobs", allow_nonce, ["did:key:zOther"])
        canonical2 = f"room-allow|d-jobs|{allow_nonce}|did:key:zOther".encode("utf-8")
        sig2 = base64.urlsafe_b64decode(allow["sig"] + "==")
        ident.public_key.verify(sig2, canonical2)
        print("  room-owners + room-allow đều verify đúng canonical string")


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
        except InvalidSignature:
            print(f"FAIL {t.__name__}: chữ ký không verify được")
    print(f"\n{passed}/{len(tests)} test pass")
    if passed != len(tests):
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
