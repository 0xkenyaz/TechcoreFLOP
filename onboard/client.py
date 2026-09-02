"""
client.py — lớp HTTP THUẦN nói chuyện với server Technocore.

Cố tình dùng `urllib.request` (thư viện chuẩn) thay vì `requests`/`httpx` để
CLI onboard chạy được trên mọi máy có Python 3.9+ mà không cần cài thêm gì
ngoài `cryptography`/`base58` (đã cần cho lớp identity).

Module này KHÔNG biết gì về passphrase hay private key — nó chỉ nhận URL đã
được build sẵn (từ `identity.signing`) hoặc tham số thô (room, nick, text) rồi
gửi đi. Tách bạch: identity/ ký, onboard/client.py gửi.

Mọi lỗi HTTP được ánh xạ sang exception có ý nghĩa, kèm giải thích tiếng Việt,
dựa đúng theo /llms.txt (mục LIMITS, DUPLICATES, MAILBOX, OWNED ROOMS).
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://technocore.chat"
USER_AGENT = "technocore-viet-onboard/0.1 (+https://github.com/flop-labs/technocore-chat)"

# 503 KHÔNG được đặc tả trong /llms.txt (khác 429 vốn có mục LIMITS riêng) —
# đây là backpressure ở tầng process. README của chính flop-labs/technocore-chat
# ghi rõ: "Exceeded concurrency limit -> 503" khi vượt --limit-concurrency, dù
# CPU/tài nguyên còn dư. Nghĩa là cả service đang quá tải tạm thời cho MỌI
# client, không riêng client này (khác 429 — giới hạn cá nhân theo IP). Retry
# với backoff là hợp lý ở đây; KHÔNG dùng delay cố định lấy từ body như 429,
# vì server không hứa hẹn gì về thời gian phục hồi cho 503.
DEFAULT_MAX_RETRIES_503 = 3
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_BACKOFF_CAP_SECONDS = 8.0

# Các đường dẫn KHÔNG bao giờ bị rate-limit theo /llms.txt — có thể gọi thoải
# mái để kiểm tra kết nối/limits mà không tốn ngân sách request.
NEVER_RATE_LIMITED_PATHS = (
    "/", "/llms.txt", "/skill.md", "/patterns.md", "/interop.md", "/auth.md",
    "/openapi.json", "/config", "/.well-known/", "/healthz",
)

_BUDGET_RE = re.compile(r"#\s*budget:\s*(\d+)\s*of\s*(\d+)\s*(reads|writes)\s*left")
_RETRY_SECONDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*second")


class TechnocoreError(Exception):
    """Lỗi chung khi gọi Technocore API."""


class ConnectionFailedError(TechnocoreError):
    """Không kết nối được tới server (DNS, timeout, mạng...)."""


class RateLimitedError(TechnocoreError):
    """429 — vượt giới hạn request. `retry_after` (giây) lấy từ body nếu tìm thấy."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class DuplicateMessageError(TechnocoreError):
    """422 — nội dung này (sau khi chuẩn hoá) vừa được gửi quá nhiều lần gần đây."""


class ForbiddenError(TechnocoreError):
    """403 — ví dụ: ghi vào mb-<room> không ký, hoặc post vào /r/events."""


class ConflictError(TechnocoreError):
    """409 — thua trong race điều kiện (?if=...); body mang giá trị hiện tại."""

    def __init__(self, message: str, current_value: str | None = None):
        super().__init__(message)
        self.current_value = current_value


class NotFoundError(TechnocoreError):
    """404 — note/room không tồn tại."""


class ServiceUnavailableError(TechnocoreError):
    """
    503 — server quá tải tạm thời (backpressure ở tầng process, xem ghi chú
    DEFAULT_MAX_RETRIES_503 ở đầu file), KHÔNG phải lỗi ở code gọi. `get()` đã
    tự retry với backoff `attempts` lần trước khi raise cái này — nhận được
    exception này nghĩa là server vẫn quá tải sau ngần ấy lần thử.
    """

    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class Response:
    status: int
    body: str
    budget: dict | None  # {"left": int, "max": int, "kind": "reads"|"writes"} hoặc None


def parse_budget(body: str) -> dict | None:
    """Tìm footer '# budget: N of M reads left this minute' trong body, nếu có."""
    m = _BUDGET_RE.search(body)
    if not m:
        return None
    return {"left": int(m.group(1)), "max": int(m.group(2)), "kind": m.group(3)}


def _raise_for_error_status(status: int, body: str) -> None:
    if status == 429:
        m = _RETRY_SECONDS_RE.search(body)
        retry_after = float(m.group(1)) if m else None
        raise RateLimitedError(
            f"Bị giới hạn tốc độ (429). Server trả lời: {body.strip()[:300]}",
            retry_after=retry_after,
        )
    if status == 422:
        raise DuplicateMessageError(
            "Server từ chối (422) — nội dung này (sau khi chuẩn hoá) có thể vừa "
            f"được gửi nhiều lần trong vài giây qua. Hãy đổi bớt câu chữ rồi thử lại. "
            f"Chi tiết: {body.strip()[:300]}"
        )
    if status == 403:
        raise ForbiddenError(f"Bị từ chối (403): {body.strip()[:300]}")
    if status == 404:
        raise NotFoundError(f"Không tìm thấy (404): {body.strip()[:300]}")
    if status == 409:
        raise ConflictError(
            f"Xung đột ghi (409) — có agent khác vừa ghi trước bạn. "
            f"Body mang giá trị hiện tại: {body.strip()[:300]}",
            current_value=body,
        )
    raise TechnocoreError(f"Lỗi HTTP {status} không mong đợi: {body.strip()[:300]}")


def _backoff_delay(attempt: int) -> float:
    """attempt bắt đầu từ 1 (lần thử ĐẦU vừa thất bại). Exponential + jitter,
    kẹp trần ở DEFAULT_BACKOFF_CAP_SECONDS để không chờ vô hạn."""
    base = min(DEFAULT_BACKOFF_CAP_SECONDS, DEFAULT_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    return base + random.uniform(0, base * 0.25)


def get(
    url: str,
    *,
    timeout: float = 15.0,
    max_retries_503: int = DEFAULT_MAX_RETRIES_503,
    quiet: bool = False,
) -> Response:
    """
    GET thuần. Ném exception có ý nghĩa nếu status không phải 2xx.

    Riêng `503` (quá tải tạm thời ở tầng process — xem ghi chú đầu file, KHÔNG
    phải lỗi cá nhân như `429`) được tự động retry với exponential backoff +
    jitter, tối đa `max_retries_503` lần (mặc định `DEFAULT_MAX_RETRIES_503`,
    tức tối đa `max_retries_503 + 1` lượt gọi thật sự). Truyền `max_retries_503=0`
    để tắt hẳn (ví dụ khi caller tự lo việc retry, hoặc đang test). An toàn để
    bật mặc định ở đây vì MỌI đường ghi thật trong dự án này đi qua
    `send_prebuilt_url()` với URL hoặc có điều kiện (`?if_absent=1`/`?if=`) hoặc
    có ký kèm nonce tăng dần — retry lại y nguyên URL, nếu lượt trước đó thật ra
    ĐÃ thành công (503 tới từ edge/proxy sau khi origin đã xử lý), thì lượt sau
    chỉ nhận `409`/lỗi nonce đã dùng chứ không tạo ra bản ghi thứ hai.

    `quiet=True` để im lặng hoàn toàn (không in tiến trình retry ra stderr) —
    dùng trong test/eval để output không lẫn tạp âm.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return Response(status=resp.status, body=body, budget=parse_budget(body))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 503:
                if attempt <= max_retries_503:
                    delay = _backoff_delay(attempt)
                    if not quiet:
                        print(
                            f"  ...503 (server quá tải tạm thời), thử lại sau "
                            f"{delay:.1f}s (lần {attempt}/{max_retries_503})",
                            file=sys.stderr,
                        )
                    time.sleep(delay)
                    continue
                raise ServiceUnavailableError(
                    "Server báo quá tải (503) sau "
                    f"{attempt} lần thử, không phải lỗi ở phía bạn. Đây là "
                    "backpressure ở tầng process của technocore-chat (xem README "
                    "gốc, mục 'Exceeded concurrency limit') — cả service đang quá "
                    "tải cho mọi client, không riêng bạn. Thử lại sau vài phút "
                    f"(gọi lại đúng lệnh cũ là an toàn). Chi tiết: {body.strip()[:300]}",
                    attempts=attempt,
                )
            _raise_for_error_status(e.code, body)
            raise AssertionError("unreachable")  # _raise_for_error_status luôn raise
        except urllib.error.URLError as e:
            raise ConnectionFailedError(f"Không kết nối được tới {url}: {e.reason}") from e


def get_or_none_if_404(url: str, *, timeout: float = 15.0) -> Response | None:
    """Như get(), nhưng trả None thay vì raise khi gặp 404 — tiện cho việc 'kiểm tra tồn tại'."""
    try:
        return get(url, timeout=timeout)
    except NotFoundError:
        return None


def get_json(base_url: str, path: str, *, timeout: float = 15.0) -> dict:
    resp = get(f"{base_url.rstrip('/')}{path}", timeout=timeout)
    return json.loads(resp.body)


# ---------------------------------------------------------------------------
# Đọc room / note (không cần identity)
# ---------------------------------------------------------------------------


def read_room(
    base_url: str,
    room: str,
    *,
    since: int | None = None,
    wait: int | None = None,
    limit: int | None = None,
    fmt: str | None = None,
    timeout: float = 30.0,
) -> Response:
    params = {}
    if since is not None:
        params["since"] = since
    if wait is not None:
        params["wait"] = wait
    if limit is not None:
        params["limit"] = limit
    if fmt is not None:
        params["format"] = fmt
    url = f"{base_url.rstrip('/')}/r/{urllib.parse.quote(room, safe='')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    # wait=<s> nghĩa là request có thể treo tới s giây — timeout mạng phải lớn hơn wait
    real_timeout = timeout if wait is None else max(timeout, wait + 10)
    return get(url, timeout=real_timeout)


def read_note(base_url: str, path: str, *, timeout: float = 15.0) -> Response | None:
    """path dạng '/kv/<ns>/<key>'. Trả None nếu note chưa tồn tại (404)."""
    return get_or_none_if_404(f"{base_url.rstrip('/')}{path}", timeout=timeout)


def get_room_nonce(base_url: str, d_room: str, *, timeout: float = 15.0) -> int:
    """
    Đọc bộ đếm nonce chống replay dùng CHUNG cho `room-owners`/`room-allow` của
    `d_room` — note world-readable `/kv/room-nonce/<d_room>` (xem docs/llms-vi.md
    mục OWNED ROOMS: "cả hai namespace sở hữu này dùng chung /kv/room-nonce/<room>
    làm bộ đếm chống replay"). `d_room` truyền vào PHẢI đã có tiền tố "d-" (nơi
    gọi, không phải hàm này, chịu trách nhiệm thêm vào — tránh double-prefix).

    Trả 0 nếu note chưa tồn tại (room chưa từng có claim/allow nào) hoặc nội
    dung không parse được thành số nguyên. Nơi gọi PHẢI dùng `giá_trị_trả_về + 1`
    làm nonce tiếp theo cho claim/allow — không bao giờ dùng trực tiếp số đọc
    được, vì đó là nonce CUỐI CÙNG đã dùng, không phải nonce kế tiếp còn trống.
    """
    resp = read_note(
        base_url, f"/kv/room-nonce/{urllib.parse.quote(d_room, safe='')}", timeout=timeout
    )
    if resp is None:
        return 0
    # Body có thể mang theo footer '# budget: ...' nối phía sau — chỉ lấy phần
    # số nguyên ở đầu chuỗi, bỏ qua phần còn lại.
    m = re.match(r"\s*(\d+)", resp.body)
    return int(m.group(1)) if m else 0


def list_rooms(base_url: str, *, fmt: str = "json", timeout: float = 15.0) -> Response:
    url = f"{base_url.rstrip('/')}/rooms"
    if fmt:
        url += f"?format={fmt}"
    return get(url, timeout=timeout)


def agent_info(base_url: str, *, timeout: float = 15.0) -> dict:
    """GET /.well-known/agent.json — limits, ephemeral TTL, v.v. Không bao giờ rate-limited."""
    return get_json(base_url, "/.well-known/agent.json", timeout=timeout)


def deployment_config(base_url: str, *, timeout: float = 15.0) -> dict:
    """GET /config — mọi knob của deployment này. Không bao giờ rate-limited."""
    return get_json(base_url, "/config", timeout=timeout)


# ---------------------------------------------------------------------------
# Ghi (say/say-signed/set note) — nhận URL đã build sẵn từ identity.signing,
# hoặc tham số thô cho lane KHÔNG ký.
# ---------------------------------------------------------------------------


def say_unsigned(
    base_url: str,
    room: str,
    nick: str,
    text: str,
    *,
    timeout: float = 15.0,
    max_retries_503: int = DEFAULT_MAX_RETRIES_503,
    quiet: bool = False,
) -> Response:
    """
    Gửi tin nhắn KHÔNG ký (nick tự xưng, ai cũng giả mạo được — xem TRUST/IDENTITY
    trong /llms.txt). Dùng cho việc thử nghiệm nhanh; production nên dùng say_signed_url().
    Text được sweep single-line ở đây để URL build ra khớp với những gì server sẽ lưu.

    LƯU Ý retry: khác các hàm ghi khác trong module này, đây là ghi KHÔNG điều
    kiện, KHÔNG nonce — nếu 503 xảy ra SAU KHI server đã thực sự ghi xong (edge
    timeout sau khi origin xử lý xong), retry lại sẽ ghi trùng một tin nhắn thứ
    hai (server có thể lọc bằng DUPLICATES nếu text giống hệt và trong cửa sổ
    thời gian, nhưng không đảm bảo). Truyền `max_retries_503=0` nếu muốn tự
    quyết định việc retry ở nơi gọi thay vì để module này tự làm.
    """
    from identity.signing import single_line_sweep

    swept = single_line_sweep(text)
    url = (
        f"{base_url.rstrip('/')}/r/{urllib.parse.quote(room, safe='')}/say/"
        f"{urllib.parse.quote(nick, safe='')}/{urllib.parse.quote(swept, safe='')}"
    )
    return get(url, timeout=timeout, max_retries_503=max_retries_503, quiet=quiet)


def send_prebuilt_url(
    url: str,
    *,
    timeout: float = 15.0,
    max_retries_503: int = DEFAULT_MAX_RETRIES_503,
    quiet: bool = False,
) -> Response:
    """
    Gửi một URL GET đã build sẵn (từ identity.signing.build_*_url). Dùng cho mọi
    thao tác CÓ KÝ — chữ ký được tính từ trước, module này chỉ việc gửi đi.

    Đây là hàm mọi lệnh ghi thật trong `onboard/cli.py` (publish/hello/room-claim/
    room-allow) đi qua. An toàn để retry mặc định trên 503: URL truyền vào luôn
    hoặc có điều kiện (`?if_absent=1`) hoặc có ký kèm nonce tăng dần, nên gọi lại
    y nguyên không tạo ra bản ghi thứ hai nếu lượt trước thực ra đã thành công.
    """
    return get(url, timeout=timeout, max_retries_503=max_retries_503, quiet=quiet)


def find_own_message_seq(
    base_url: str,
    room: str,
    did: str,
    nonce: int,
    *,
    limit: int = 200,
    timeout: float = 15.0,
) -> int | None:
    """
    Tra `seq` THẬT của chính message vừa gửi, bằng cách đọc lại room (JSON) và
    khớp theo (did, nonce) — KHÔNG dựa vào body trả về của chính lượt ghi.

    `limit` mặc định 200 — đúng biên tối đa server cho phép theo
    `docs/llms-vi.md` (mục ĐỌC/GHI: `GET /r/<room>?limit=<1..200>`), không có
    lý do gì để mặc định thấp hơn con số này: một request "tra lại ngay sau
    khi ghi" nên tận dụng hết biên độ được phép để giảm rủi ro bị ring buffer
    (xem RETENTION) xoay vòng qua mất message trước khi kịp tra. Giá trị
    truyền vào bị kẹp về [1, 200] để không gửi lên server một con số server
    sẽ tự ý bỏ qua/kẹp lại theo cách không đoán trước được.

    Lý do cần hàm này: `/llms.txt` (và `/skill.md`) không đặc tả body của một
    write GET /r/<room>/say-signed/... trông như thế nào khi thành công. Đo
    thực tế trên server công khai cho thấy nó trả về ĐUÔI của room (giống một
    lượt đọc, kèm banner "UNTRUSTED CONTENT"), KHÔNG phải một xác nhận dạng
    "ok, seq=N" như `tests/mock_server.py` từng giả định. Với một room bận
    (lobby thật có thể nhận hàng trăm message/phút từ agent khác), đuôi room
    tại thời điểm response trả về CÓ THỂ đã không còn chứa message của bạn nữa
    — nên không an toàn để hiển thị nó cho người dùng như một "biên nhận".

    Trả về None nếu không tìm thấy trong `limit` message gần nhất (room quá
    bận, hoặc — hiếm — bị một request khác chen ngay giữa lúc gửi và lúc đọc
    lại) — đây KHÔNG có nghĩa message chưa được ghi, chỉ là không tra lại được
    ngay; `seq` cũ hơn vẫn tồn tại xa hơn trong room.
    """
    clamped_limit = max(1, min(200, limit))
    resp = read_room(base_url, room, limit=clamped_limit, fmt="json", timeout=timeout)
    # Cắt bỏ một dòng footer '# budget: ...' có thể bị nối thêm SAU phần JSON
    # hợp lệ (không được /llms.txt loại trừ khỏi format=json một cách tường
    # minh) — nếu không cắt, json.loads() vỡ vì có rác sau JSON.
    body_for_json = re.sub(r"\n?#\s*budget:.*$", "", resp.body.strip(), flags=re.IGNORECASE | re.DOTALL)
    try:
        data = json.loads(body_for_json)
    except (json.JSONDecodeError, ValueError):
        return None
    # Body có thể là {"messages": [...]} (object, như deployment thật quan sát
    # được) hoặc một mảng trần [...] (một số mock/deployment khác) — chấp nhận cả hai.
    messages = data.get("messages", []) if isinstance(data, dict) else data
    for msg in messages:
        if msg.get("from") == did and str(msg.get("nonce")) == str(nonce):
            seq = msg.get("seq")
            return int(seq) if seq is not None else None
    return None
