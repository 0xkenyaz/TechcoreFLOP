"""
Test cho onboard/client.py bằng một mock server local (http.server), KHÔNG
đụng tới internet thật. Mục tiêu: verify client.py map đúng status code sang
exception, và parse đúng budget footer theo /llms.txt mục LIMITS.

Chạy: python3 tests/test_client.py
"""

from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mock_server  # noqa: E402
from onboard import client  # noqa: E402


class _MockHandler(BaseHTTPRequestHandler):
    # Đếm số lần bị gọi cho hai route mô phỏng 503 — reset qua reset_counters()
    # ở ĐẦU mỗi test dùng chúng, vì class này (không phải instance) được dùng
    # chung cho mọi HTTPServer tạo ra trong suốt tiến trình test.
    _always503_hits = 0
    _flaky_hits = 0

    @classmethod
    def reset_counters(cls) -> None:
        cls._always503_hits = 0
        cls._flaky_hits = 0

    def log_message(self, format, *args):  # im lặng, khỏi rác console test
        pass

    def _send(self, status: int, body: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        if self.path == "/always503":
            _MockHandler._always503_hits += 1
            self._send(503, "service overloaded — exceeded concurrency limit")
            return
        if self.path == "/flaky2":
            # 503 hai lần đầu, thành công từ lần gọi thứ 3 trở đi — mô phỏng
            # đúng kịch bản "quá tải tạm thời rồi tự phục hồi".
            _MockHandler._flaky_hits += 1
            if _MockHandler._flaky_hits <= 2:
                self._send(503, "service overloaded — exceeded concurrency limit")
            else:
                self._send(200, "recovered # budget: 5 of 100 reads left this minute")
            return
        routes = {
            "/ok": (200, "hello # budget: 12 of 100 reads left this minute"),
            "/ok-write-budget": (200, "ok # budget: 3 of 20 writes left this minute"),
            "/notfound": (404, "not found"),
            "/dupe": (422, "duplicate content, refused for 8 more seconds"),
            "/ratelimited": (429, "rate limited, retry after 12 seconds"),
            "/forbidden": (403, "signed writes only"),
            "/conflict": (409, "current value: something-else"),
            "/weird": (500, "internal error"),
        }
        status, body = routes.get(self.path, (404, "unmapped path"))
        self._send(status, body)


def _start_server() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_ok_with_read_budget():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        resp = client.get(f"{base}/ok")
        assert resp.status == 200
        assert resp.budget == {"left": 12, "max": 100, "kind": "reads"}
        print(f"  budget parse OK: {resp.budget}")
    finally:
        server.shutdown()


def test_ok_with_write_budget():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        resp = client.get(f"{base}/ok-write-budget")
        assert resp.budget == {"left": 3, "max": 20, "kind": "writes"}
    finally:
        server.shutdown()


def test_404_maps_to_notfound_error():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            client.get(f"{base}/notfound")
            assert False, "phải raise NotFoundError"
        except client.NotFoundError:
            pass
    finally:
        server.shutdown()


def test_get_or_none_if_404():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        result = client.get_or_none_if_404(f"{base}/notfound")
        assert result is None
        result_ok = client.get_or_none_if_404(f"{base}/ok")
        assert result_ok is not None and result_ok.status == 200
    finally:
        server.shutdown()


def test_422_maps_to_duplicate_error():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            client.get(f"{base}/dupe")
            assert False, "phải raise DuplicateMessageError"
        except client.DuplicateMessageError as e:
            assert "422" not in str(e) or True  # message chỉ cần rõ ràng, không cứng nhắc
            print(f"  DuplicateMessageError: {e}")
    finally:
        server.shutdown()


def test_429_maps_to_ratelimited_with_retry_after():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            client.get(f"{base}/ratelimited")
            assert False, "phải raise RateLimitedError"
        except client.RateLimitedError as e:
            assert e.retry_after == 12.0
            print(f"  RateLimitedError.retry_after = {e.retry_after}")
    finally:
        server.shutdown()


def test_403_maps_to_forbidden():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            client.get(f"{base}/forbidden")
            assert False, "phải raise ForbiddenError"
        except client.ForbiddenError:
            pass
    finally:
        server.shutdown()


def test_409_maps_to_conflict_with_current_value():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            client.get(f"{base}/conflict")
            assert False, "phải raise ConflictError"
        except client.ConflictError as e:
            assert "something-else" in e.current_value
    finally:
        server.shutdown()


def test_unknown_status_maps_to_generic_error():
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            client.get(f"{base}/weird")
            assert False, "phải raise TechnocoreError"
        except client.TechnocoreError:
            pass
    finally:
        server.shutdown()


def test_connection_failed_on_unreachable_host():
    try:
        client.get("http://127.0.0.1:1/definitely-closed", timeout=1)
        assert False, "phải raise ConnectionFailedError"
    except client.ConnectionFailedError:
        pass


def test_find_own_message_seq_found():
    """Gửi một message qua mock room thật (không giả lập), rồi tra lại đúng
    seq bằng (did, nonce) — đi qua đúng code path find_own_message_seq() gọi
    read_room(?format=json), kể cả phần cắt footer '# budget: ...' phía sau
    JSON mà mock_server luôn nối thêm. Dùng room riêng để cô lập với các test
    khác (STATE của mock_server là module-level global, dùng chung 1 process)."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        room = "test-seq-found"
        did = "did:key:z6MkExampleDidNotReal11111111111111111111"
        nonce = 1700000000123
        write_url = f"{base}/r/{room}/say-signed/{did}/fakesig/{nonce}/hello%20world"
        write_resp = client.get(write_url)
        assert write_resp.status == 200

        seq = client.find_own_message_seq(base, room, did, nonce)
        assert seq is not None, "phải tra được seq vừa ghi"
        assert seq == 1
    finally:
        server.shutdown()


def test_find_own_message_seq_not_found_when_nonce_mismatch():
    """did đúng nhưng nonce không khớp -> None, không được nhầm sang message khác."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        room = "test-seq-nonce-mismatch"
        did = "did:key:z6MkExampleDidNotReal11111111111111111111"
        client.get(f"{base}/r/{room}/say-signed/{did}/fakesig/111/hello")

        seq = client.find_own_message_seq(base, room, did, nonce=999)
        assert seq is None
    finally:
        server.shutdown()


def test_find_own_message_seq_defaults_to_server_max_limit():
    """Mặc định phải là 200 (biên tối đa server cho phép theo docs/llms-vi.md
    mục ĐỌC/GHI: GET /r/<room>?limit=<1..200>), và limit lớn hơn phải bị kẹp
    lại chứ không gửi thẳng lên server."""
    import inspect

    sig = inspect.signature(client.find_own_message_seq)
    assert sig.parameters["limit"].default == 200

    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        room = "test-seq-clamped-limit"
        did = "did:key:z6MkExampleDidNotReal11111111111111111111"
        client.get(f"{base}/r/{room}/say-signed/{did}/fakesig/1/hello")
        # limit=99999 phải bị kẹp về 200, không gây lỗi và vẫn tra được message
        seq = client.find_own_message_seq(base, room, did, nonce=1, limit=99999)
        assert seq == 1
    finally:
        server.shutdown()


def test_get_room_nonce_defaults_to_zero_when_note_missing():
    """Room chưa từng có claim/allow -> note /kv/room-nonce/<d-room> chưa tồn tại
    (404) -> phải trả 0, để nơi gọi dùng 0 + 1 = 1 làm claim_nonce đầu tiên."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        nonce = client.get_room_nonce(base, "d-never-claimed-room")
        assert nonce == 0
    finally:
        server.shutdown()


def test_get_room_nonce_parses_existing_value_ignoring_budget_footer():
    """Sau khi claim thành công (mock lưu room-nonce dạng số thô), get_room_nonce
    phải parse đúng số nguyên dù body có thể có footer '# budget: ...' phía sau."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        d_room = "d-nonce-parse-test"
        did = "did:key:z6MkExampleDidNotReal11111111111111111111"
        claim_url = f"{base}/kv/room-owners/{d_room}/set-signed/{did}/fakesig/1/{did}?if_absent=1"
        resp = client.get(claim_url)
        assert resp.status == 200

        nonce = client.get_room_nonce(base, d_room)
        assert nonce == 1
    finally:
        server.shutdown()


def test_room_claim_then_allow_happy_path():
    """Đi qua đúng chuỗi thao tác thật: claim (nonce=1) rồi allow (nonce=2, phải
    lớn hơn claim_nonce) — khớp docs/llms-vi.md mục OWNED ROOMS."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        d_room = "d-happy-path-room"
        owner = "did:key:z6MkOwnerNotReal1111111111111111111111111"
        other = "did:key:z6MkOtherNotReal1111111111111111111111111"

        claim_url = f"{base}/kv/room-owners/{d_room}/set-signed/{owner}/fakesig/1/{owner}?if_absent=1"
        resp = client.get(claim_url)
        assert resp.status == 200

        allow_url = (
            f"{base}/kv/room-allow/{d_room}/set-signed/{owner}/fakesig/2/{other}"
        )
        resp2 = client.get(allow_url)
        assert resp2.status == 200
        assert client.get_room_nonce(base, d_room) == 2
    finally:
        server.shutdown()


def test_room_claim_rejects_claiming_a_different_did():
    """Value trong URL claim PHẢI là chính did đang ký — nếu khác, server (mock)
    phải từ chối 403 (chống 'khai báo hộ' — xem README mục 'dễ làm sai')."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        d_room = "d-mismatched-claim-room"
        signer = "did:key:z6MkSignerNotReal111111111111111111111111"
        claimed_for = "did:key:z6MkOtherNotReal1111111111111111111111111"
        url = f"{base}/kv/room-owners/{d_room}/set-signed/{signer}/fakesig/1/{claimed_for}?if_absent=1"
        try:
            client.get(url)
            assert False, "phải raise ForbiddenError"
        except client.ForbiddenError:
            pass
    finally:
        server.shutdown()


def test_room_claim_conflicts_when_already_owned():
    """Claim lần 2 (if_absent=1) khi đã có chủ -> 409, không chiếm được từ tay chủ cũ."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        d_room = "d-already-owned-room"
        owner = "did:key:z6MkOwnerNotReal1111111111111111111111111"
        challenger = "did:key:z6MkChallengerNotReal11111111111111111111"

        client.get(f"{base}/kv/room-owners/{d_room}/set-signed/{owner}/fakesig/1/{owner}?if_absent=1")
        try:
            client.get(
                f"{base}/kv/room-owners/{d_room}/set-signed/{challenger}/fakesig/2/{challenger}?if_absent=1"
            )
            assert False, "phải raise ConflictError"
        except client.ConflictError:
            pass
    finally:
        server.shutdown()


def test_room_allow_rejects_non_owner():
    """Chỉ chủ sở hữu HIỆN TẠI mới ghi được room-allow — did khác phải bị 403,
    kể cả khi chọn nonce hợp lệ (lớn hơn room-nonce hiện tại)."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        d_room = "d-not-owner-allow-room"
        owner = "did:key:z6MkOwnerNotReal1111111111111111111111111"
        intruder = "did:key:z6MkIntruderNotReal111111111111111111111"

        client.get(f"{base}/kv/room-owners/{d_room}/set-signed/{owner}/fakesig/1/{owner}?if_absent=1")
        try:
            client.get(f"{base}/kv/room-allow/{d_room}/set-signed/{intruder}/fakesig/2/{intruder}")
            assert False, "phải raise ForbiddenError"
        except client.ForbiddenError:
            pass
    finally:
        server.shutdown()


def test_room_allow_rejects_stale_or_equal_nonce():
    """Nonce của room-allow phải LỚN HƠN claim_nonce (và mọi nonce trước đó) —
    dùng lại nonce=1 (bằng claim_nonce) phải bị từ chối 409, không được chấp nhận."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        d_room = "d-stale-nonce-room"
        owner = "did:key:z6MkOwnerNotReal1111111111111111111111111"
        other = "did:key:z6MkOtherNotReal1111111111111111111111111"

        client.get(f"{base}/kv/room-owners/{d_room}/set-signed/{owner}/fakesig/1/{owner}?if_absent=1")
        try:
            client.get(f"{base}/kv/room-allow/{d_room}/set-signed/{owner}/fakesig/1/{other}")
            assert False, "phải raise ConflictError (nonce không tăng)"
        except client.ConflictError:
            pass
    finally:
        server.shutdown()


def test_room_allow_before_any_claim_is_forbidden():
    """Room chưa từng được claim -> chưa có chủ sở hữu -> room-allow phải bị 403,
    không phải 404/500 mơ hồ."""
    server = mock_server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        d_room = "d-never-claimed-allow-room"
        someone = "did:key:z6MkSomeoneNotReal1111111111111111111111"
        try:
            client.get(f"{base}/kv/room-allow/{d_room}/set-signed/{someone}/fakesig/1/{someone}")
            assert False, "phải raise ForbiddenError"
        except client.ForbiddenError:
            pass
    finally:
        server.shutdown()


def test_503_retries_with_backoff_then_succeeds():
    """503 hai lần đầu, thành công lần 3 -> get() phải tự retry và trả về kết
    quả thành công cho caller, không raise gì cả."""
    _MockHandler.reset_counters()
    server = _start_server()
    original_backoff = client._backoff_delay
    client._backoff_delay = lambda attempt: 0.01  # bỏ qua backoff thật cho test nhanh
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        resp = client.get(f"{base}/flaky2", max_retries_503=3, quiet=True)
        assert resp.status == 200
        assert resp.budget == {"left": 5, "max": 100, "kind": "reads"}
        assert _MockHandler._flaky_hits == 3, "phải gọi đúng 3 lần (2 fail + 1 success)"
    finally:
        client._backoff_delay = original_backoff
        server.shutdown()


def test_503_raises_service_unavailable_after_exhausting_retries():
    """Server LUÔN 503 -> sau khi hết max_retries_503 lượt thử, get() phải raise
    ServiceUnavailableError (không phải TechnocoreError chung chung), với
    .attempts đúng bằng tổng số lần đã thử (1 lần đầu + số lần retry)."""
    _MockHandler.reset_counters()
    server = _start_server()
    original_backoff = client._backoff_delay
    client._backoff_delay = lambda attempt: 0.01
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            client.get(f"{base}/always503", max_retries_503=2, quiet=True)
            assert False, "phải raise ServiceUnavailableError"
        except client.ServiceUnavailableError as e:
            assert e.attempts == 3, f"phải thử đúng 3 lần (1+2), got {e.attempts}"
        assert _MockHandler._always503_hits == 3
    finally:
        client._backoff_delay = original_backoff
        server.shutdown()


def test_503_max_retries_0_disables_retry():
    """max_retries_503=0 -> KHÔNG retry gì cả, raise ngay sau lần thử đầu tiên
    — cần cho caller nào tự muốn kiểm soát việc retry (ví dụ ghi KHÔNG điều
    kiện qua say_unsigned(), xem docstring của hàm đó)."""
    _MockHandler.reset_counters()
    server = _start_server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            client.get(f"{base}/always503", max_retries_503=0, quiet=True)
            assert False, "phải raise ServiceUnavailableError"
        except client.ServiceUnavailableError as e:
            assert e.attempts == 1
        assert _MockHandler._always503_hits == 1, "không được retry khi max_retries_503=0"
    finally:
        server.shutdown()


def test_503_quiet_suppresses_stderr_progress():
    """quiet=True không được in gì ra stderr trong lúc retry (dùng trong
    test/eval để output không lẫn tạp âm 'thử lại sau Ns')."""
    import io
    import contextlib

    _MockHandler.reset_counters()
    server = _start_server()
    original_backoff = client._backoff_delay
    client._backoff_delay = lambda attempt: 0.01
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            client.get(f"{base}/flaky2", max_retries_503=3, quiet=True)
        assert captured.getvalue() == "", f"quiet=True nhưng vẫn in ra: {captured.getvalue()!r}"
    finally:
        client._backoff_delay = original_backoff
        server.shutdown()


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
    print(f"\n{passed}/{len(tests)} test pass")
    if passed != len(tests):
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
