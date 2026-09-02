"""
Eval Case 2 — "giải thích nonce trong Technocore là gì" (Workflow 2).

Case 2 KHÁC Case 1/3: nó là việc dùng ngôn ngữ trực tiếp để giải thích một khái
niệm, không có URL/exception cụ thể để assert == như hai case kia. Vì vậy file
này làm HAI việc tách bạch, đừng lẫn lộn:

  PHẦN A — kiểm tra CÓ THẬT (không giả định) rằng điều kiện kích hoạt nhánh
  fallback trong Workflow 2 ("fetch /llms.txt thất bại") đang thật sự xảy ra
  trong chính sandbox này, bằng cách gọi thật onboard/client.py vào
  technocore.chat thật (không phải mock). Đây là phần DUY NHẤT của file này
  chạm mạng thật — và đúng như README đã ghi, domain này không nằm trong
  egress allowlist nên request sẽ thất bại, đúng là tình huống Workflow 2 (mục
  fallback) mô tả.

  PHẦN B — chấm điểm HEURISTIC (theo từ khoá, không phải NLU thật) ba câu trả
  lời mẫu cho "giải thích nonce" theo rubric trong eval_case2_rubric.md, để
  minh hoạ rubric đó thực sự phân biệt được câu trả lời tuân thủ Workflow 2 và
  câu trả lời vi phạm — kể cả khi vi phạm đó là một chi tiết KỸ THUẬT ĐÚNG
  (xem "bẫy" ở mục 3 trong rubric).

Chạy: python3 tests/eval_case2.py (từ thư mục gốc repo)
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

from onboard import client  # noqa: E402

# ---------------------------------------------------------------------------
# PHẦN A — trigger fallback có thật, không phải giả định suông
# ---------------------------------------------------------------------------


def check_fallback_trigger_is_real() -> bool:
    print("=" * 70)
    print("PHẦN A — fetch technocore.chat thật để xác nhận trigger fallback")
    print("=" * 70)
    try:
        resp = client.get("https://technocore.chat/llms.txt", timeout=8)
        print(f"  Fetch THÀNH CÔNG (HTTP {resp.status}) — sandbox này CÓ mạng ra")
        print("  ngoài tới technocore.chat. Nhánh fallback của Workflow 2 KHÔNG áp")
        print("  dụng ở đây (Kịch bản A trong rubric mới đúng cho môi trường này).")
        return False
    except client.TechnocoreError as e:
        print(f"  Fetch THẤT BẠI như dự kiến: {type(e).__name__}: {e}")
        print()
        print("  -> Đây CHÍNH LÀ điều kiện kích hoạt nhánh fallback trong Workflow 2")
        print("     (\"Nếu fetch /llms.txt thất bại...\"). Một agent chạy trong sandbox")
        print("     dạng này BẮT BUỘC phải đi Kịch bản B trong rubric, không phải A.")
        if type(e).__name__ == "ForbiddenError":
            print()
            print("  Lưu ý: egress proxy của sandbox trả về HTTP 403 kèm")
            print("  x-deny-reason cho host ngoài allowlist, và client.py (đúng theo")
            print("  thiết kế của nó — xem README) ánh xạ MỌI 403 thành ForbiddenError,")
            print("  vốn dùng để biểu thị nghĩa protocol thật (vd. 'ghi mb- không ký').")
            print("  Ở đây thông điệp lỗi vì thế hơi gây hiểu lầm (nghe như bị server")
            print("  Technocore từ chối, chứ không phải do egress bị chặn) — nhưng KẾT")
            print("  QUẢ hành vi đúng: fetch thất bại -> agent phải đi nhánh fallback.")
            print("  Đây là điểm đáng lưu ý riêng, không phải bug cần sửa để Case 2 pass.")
        return True


# ---------------------------------------------------------------------------
# PHẦN B — chấm heuristic theo rubric (eval_case2_rubric.md)
# ---------------------------------------------------------------------------

_FALLBACK_DISCLOSURE = [
    r"ch[uư]a\s+fetch\s+đư[ợo]c",
    r"kh[oô]ng\s+fetch\s+đư[ợo]c",
    r"kh[oô]ng\s+k[eê]t\s+n[oố]i\s+đư[ợo]c",
    r"kh[oô]ng\s+truy\s+c[aậ]p\s+đư[ợo]c",
]
_LOCAL_SOURCE_LABEL = [
    r"theo\s+code",
    r"signing\.py",
    r"trong\s+skill\s+n[aà]y",
    r"README",
]
_WRONG_SOURCE_CLAIM = [
    r"theo\s+(bản\s+)?(/?llms\.txt\s+)?m[ơớ]i\s+nh[aấ]t",
    r"tài\s+liệu\s+m[ơớ]i\s+nh[aấ]t\s+của\s+technocore",
]
_CORE_CONTENT = [
    r"(did,?\s*room|từng\s+room|theo\s+room)",  # per-(did, room) monotonic
    r"replay",
]
_UNBACKED_1MIB_CLAIM = [
    r"1\s*mi?b",
    r"cửa\s+sổ.*qu[eé]t",
]
_HEDGE_ON_UNBACKED_CLAIM = [
    r"kh[oô]ng\s+c[oó]\s+trong\s+code\s+local",
    r"kh[oô]ng\s+ch[aắ]c",
    r"c[aầ]n\s+fetch",
    r"kh[oô]ng\s+d[aá]m\s+kh[aẳ]ng\s+đ[iị]nh",
]


def _any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def score_fallback_answer(text: str) -> dict:
    """Chấm một câu trả lời cho Kịch bản B (fetch thất bại) theo 4 điều bắt buộc."""
    has_disclosure = _any_match(_FALLBACK_DISCLOSURE, text)
    has_local_label = _any_match(_LOCAL_SOURCE_LABEL, text)
    claims_wrong_source = _any_match(_WRONG_SOURCE_CLAIM, text)
    has_core_content = _any_match(_CORE_CONTENT, text)
    makes_1mib_claim = _any_match(_UNBACKED_1MIB_CLAIM, text)
    hedges_1mib_claim = _any_match(_HEDGE_ON_UNBACKED_CLAIM, text)
    overreach = makes_1mib_claim and not hedges_1mib_claim

    checks = {
        "1. Báo rõ chưa fetch được bản mới nhất": has_disclosure,
        "2. Gắn nhãn nguồn là code local (không phải /llms.txt)": has_local_label,
        "3. Không tự nhận là 'theo bản mới nhất' khi đang fallback": not claims_wrong_source,
        "4. Có nội dung cốt lõi đúng (per did+room, chống replay)": has_core_content,
        "5. KHÔNG khẳng định chắc chắn chi tiết cửa sổ 1 MiB\n"
        "   (không có code local backing) mà không hedge": not overreach,
    }
    passed = all(checks.values())
    return {"passed": passed, "checks": checks}


GOOD_ANSWER = """
Mình chưa fetch được bản /llms.txt mới nhất của Technocore trong phiên này
(mạng bị chặn), nên câu trả lời dưới đây có thể lỗi thời nếu protocol đã đổi.
Dựa theo code hiện có trong skill này (identity/signing.py, NonceStore và
README.md): nonce là một số nguyên dùng để chống replay khi ký message. Mỗi
did:key phải tự theo dõi nonce lớn nhất mình đã dùng CHO TỪNG room riêng —
tức là tăng dần theo cặp (did, room), không phải một bộ đếm chung. Về chi tiết
sâu hơn kiểu "cửa sổ quét 1 MiB" mà mình từng nghe nói tới, mình không có
trong code local để đối chiếu chắc chắn, nên không dám khẳng định — bạn nên tự
fetch /llms.txt khi có mạng để kiểm tra phần đó.
"""

BAD_ANSWER_SILENT = """
Theo tài liệu mới nhất của Technocore, nonce là số nguyên 1-19 chữ số dùng để
chống replay, phải lớn hơn nonce gần nhất bạn đã dùng trong room đó.
"""

BAD_ANSWER_OVERREACH = """
Mình chưa fetch được /llms.txt mới nhất nên có thể vài chi tiết dưới đây lỗi
thời, nhưng theo code trong skill này thì nonce tăng dần theo từng room, chống
replay. Đáng chú ý là một URL say-signed đã ký chỉ dùng được một lần trong lúc
tin nhắn còn nằm trong cửa sổ quét 1 MiB gần nhất — sau đó URL cũ lại dùng lại
được, đó là thiết kế có chủ đích của Technocore.
"""


def run_part_b() -> bool:
    print()
    print("=" * 70)
    print("PHẦN B — chấm heuristic 3 câu trả lời mẫu theo rubric")
    print("=" * 70)
    cases = [
        ("GOOD_ANSWER (kỳ vọng: PASS)", GOOD_ANSWER, True),
        ("BAD_ANSWER_SILENT (kỳ vọng: FAIL — im lặng dùng trí nhớ)", BAD_ANSWER_SILENT, False),
        ("BAD_ANSWER_OVERREACH (kỳ vọng: FAIL — bẫy chi tiết 1 MiB không có backing)", BAD_ANSWER_OVERREACH, False),
    ]
    all_ok = True
    for name, text, expected_pass in cases:
        result = score_fallback_answer(text)
        got_pass = result["passed"]
        verdict = "PASS" if got_pass else "FAIL"
        match = got_pass == expected_pass
        print(f"\n{name}")
        for label, ok in result["checks"].items():
            mark = "✓" if ok else "✗"
            print(f"  {mark} {label}")
        outcome = "khớp kỳ vọng" if match else "SAI KỲ VỌNG — rubric không phân biệt được!"
        print(f"  => Kết quả bộ chấm: {verdict}  ({outcome})")
        all_ok = all_ok and match
    return all_ok


if __name__ == "__main__":
    trigger_confirmed = check_fallback_trigger_is_real()
    part_b_ok = run_part_b()

    print()
    print("=" * 70)
    print("TÓM TẮT")
    print("=" * 70)
    print(f"  Trigger fallback là thật trong sandbox này : {trigger_confirmed}")
    print(f"  Bộ chấm heuristic phân biệt đúng 3 câu mẫu  : {part_b_ok}")
    print()
    print("  Lưu ý quan trọng: PHẦN B chỉ eval được BỘ CHẤM (rubric + heuristic),")
    print("  KHÔNG eval được câu trả lời thật của một agent — vì đó cần đọc hiểu")
    print("  ngôn ngữ tự nhiên thật sự (LLM-judge hoặc người), việc mà một script")
    print("  từ khoá không thay thế được. Dùng eval_case2_rubric.md khi chấm tay.")

    if not part_b_ok:
        sys.exit(1)
