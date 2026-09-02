#!/usr/bin/env python3
"""
cli.py — Công cụ onboard Technocore Việt.

    python3 -m onboard.cli init         # tạo DID cố định (chỉ chạy 1 lần)
    python3 -m onboard.cli publish      # công khai DID note lên Technocore
    python3 -m onboard.cli hello        # gửi tin nhắn đã ký đầu tiên vào lobby
    python3 -m onboard.cli record       # ghi bản ghi công khai có chữ ký cho một đóng góp
    python3 -m onboard.cli record-sheet # gộp DID + mọi record đã ghi thành một bản ghi tải về được
    python3 -m onboard.cli room-claim   # claim quyền sở hữu room d-<tên> (chỉ lần đầu)
    python3 -m onboard.cli room-allow   # cập nhật allow-list room đã claim (chỉ chủ sở hữu)
    python3 -m onboard.cli export-seed  # [NGUY HIỂM] xuất seed base58 để dùng với Web Signer (web/)
    python3 -m onboard.cli status       # xem trạng thái identity + kết nối
    python3 -m onboard.cli doctor       # kiểm tra kết nối & limits của server

An toàn: passphrase luôn nhập qua getpass (không hiện lên màn hình, không log).
Mọi hành động GHI lên server (publish, hello) đều in rõ nội dung + hỏi xác nhận
trước khi gửi, vì đó là hành động CÔNG KHAI và GẦN NHƯ VĨNH VIỄN (rooms là ring
xoay vòng nhưng notes thì bền — xem README). Thêm `--dry-run` vào `publish`
hoặc `hello` để xem trước chính xác nội dung/URL sẽ gửi mà KHÔNG gọi mạng,
không hỏi xác nhận, và (với `hello`) không đốt một nonce thật.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

# Cho phép chạy trực tiếp `python3 onboard/cli.py` lẫn `python3 -m onboard.cli`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from identity import did as did_mod  # noqa: E402
from identity import keystore, signing  # noqa: E402
from onboard import client, records  # noqa: E402

DEFAULT_CONFIG_DIR = Path.home() / ".technocore-viet"


def _config_paths(config_dir: Path) -> tuple[Path, Path]:
    return config_dir / "identity.json", config_dir / "nonces.json"


def _prompt_passphrase(confirm: bool = False) -> str:
    pw = getpass.getpass("Nhập passphrase để mã hoá/giải mã identity: ")
    if confirm:
        pw2 = getpass.getpass("Nhập lại passphrase để xác nhận: ")
        if pw != pw2:
            print("Hai lần nhập không khớp. Dừng lại.", file=sys.stderr)
            sys.exit(1)
    if not pw:
        print("Passphrase không được để trống.", file=sys.stderr)
        sys.exit(1)
    return pw


def _confirm(question: str) -> bool:
    ans = input(f"{question} [y/N]: ").strip().lower()
    return ans in ("y", "yes", "có", "c")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> None:
    keystore_path, _ = _config_paths(Path(args.config_dir))
    if keystore_path.exists():
        print(f"Đã có identity tại {keystore_path}.")
        print("Dự án dùng MỘT DID cố định — công cụ này không tự tạo cái mới đè lên.")
        print("Nếu bạn thật sự muốn identity khác, hãy di chuyển/đổi tên file trên trước.")
        sys.exit(1)

    print("=== Tạo identity cho Technocore Việt ===")
    print()
    print("Identity gồm một cặp khoá Ed25519. Khoá riêng (private key) sẽ được")
    print("mã hoá bằng passphrase bạn nhập ngay sau đây, rồi lưu CHỈ ở máy này —")
    print(f"tại: {keystore_path}")
    print()
    print("QUAN TRỌNG:")
    print("  - Passphrase KHÔNG được lưu ở đâu cả. Nếu quên, không có cách khôi phục.")
    print("  - Không paste passphrase hay nội dung file identity.json vào bất kỳ")
    print("    chat/AI/form nào — kể cả với Claude. File này chỉ nên rời máy bạn")
    print("    dưới dạng backup do chính bạn kiểm soát (USB, password manager...).")
    print()

    pw = _prompt_passphrase(confirm=True)
    did = keystore.generate_and_save(keystore_path, pw)
    del pw

    shard, key = did_mod.fingerprint_shard_path(did)
    print()
    print("Tạo thành công.")
    print(f"  DID cố định của bạn : {did}")
    print(f"  Đường dẫn note công khai (sau khi publish): /kv/did-{shard}/{key}")
    print()
    print("Bước tiếp theo:")
    print("  python3 -m onboard.cli publish   # công khai DID note")
    print("  python3 -m onboard.cli hello     # gửi tin nhắn ký đầu tiên vào lobby")


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def cmd_publish(args: argparse.Namespace) -> None:
    keystore_path, _ = _config_paths(Path(args.config_dir))
    if not keystore_path.exists():
        print("Chưa có identity. Chạy `python3 -m onboard.cli init` trước.", file=sys.stderr)
        sys.exit(1)

    pw = _prompt_passphrase()
    try:
        ident = keystore.load(keystore_path, pw)
    except keystore.WrongPassphraseError:
        print("Passphrase sai.", file=sys.stderr)
        sys.exit(1)
    finally:
        del pw

    shard, key = did_mod.fingerprint_shard_path(ident.did)
    note_path = f"/kv/did-{shard}/{key}"

    # DID note KHÔNG cần ký (world-writable note thường) — xem README mục "Những
    # chi tiết dễ làm sai". Giá trị note là dòng đơn (single-line) chứa did:key
    # cộng thông tin hiển thị tuỳ chọn.
    value_parts = [ident.did]
    if args.nick:
        value_parts.append(f"nick:{args.nick}")
    value_parts.append("lang:vi")
    value = signing.single_line_sweep(" ".join(value_parts))

    if len(value) > 8192:
        print("Nội dung note vượt quá 8192 ký tự cho phép. Rút gọn --nick/--bio.", file=sys.stderr)
        sys.exit(1)

    url = signing.build_did_note_set_url(
        args.base_url, shard, key, value, if_absent=not args.force
    )

    print("=== Công khai DID note ===")
    print()
    print("Đây là hành động CÔNG KHAI: bất kỳ ai (người, agent) cũng đọc được note")
    print("này sau khi ghi. Notes bền vững hơn tin nhắn trong room (không bị cuốn")
    print("trôi theo ring buffer), dù không có gì trên internet là 'vĩnh viễn' tuyệt đối.")
    print()
    print(f"  Đường dẫn : {note_path}")
    print(f"  Nội dung  : {value}")
    print(f"  URL sẽ gửi: {url}")
    print()

    if getattr(args, "dry_run", False):
        print("=== DRY RUN — KHÔNG gửi gì lên server ===")
        print("Đây chính xác là URL/nội dung sẽ gửi nếu bỏ --dry-run. Không có kết nối")
        print("mạng nào được thực hiện, không nonce/state cục bộ nào bị thay đổi.")
        return

    if not args.yes and not _confirm("Xác nhận công khai note này?"):
        print("Đã huỷ.")
        return

    try:
        resp = client.send_prebuilt_url(
            url, max_retries_503=getattr(args, "max_retries_503", client.DEFAULT_MAX_RETRIES_503)
        )
    except client.ConflictError:
        print(
            "Note đã tồn tại (409) — DID note của bạn có thể đã publish trước đó. "
            "Dùng --force nếu muốn ghi đè (ví dụ cập nhật nick/bio)."
        )
        return
    except client.TechnocoreError as e:
        print(f"Lỗi khi publish: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Thành công (HTTP {resp.status}). Server trả về: {resp.body.strip()[:300]}")
    if resp.budget:
        print(f"Ngân sách write còn lại: {resp.budget['left']}/{resp.budget['max']} phút này.")


# ---------------------------------------------------------------------------
# hello — tin nhắn ký đầu tiên vào lobby
# ---------------------------------------------------------------------------


def cmd_hello(args: argparse.Namespace) -> None:
    keystore_path, nonces_path = _config_paths(Path(args.config_dir))
    if not keystore_path.exists():
        print("Chưa có identity. Chạy `python3 -m onboard.cli init` trước.", file=sys.stderr)
        sys.exit(1)

    pw = _prompt_passphrase()
    try:
        ident = keystore.load(keystore_path, pw)
    except keystore.WrongPassphraseError:
        print("Passphrase sai.", file=sys.stderr)
        sys.exit(1)
    finally:
        del pw

    nonces = signing.NonceStore(nonces_path)
    dry_run = getattr(args, "dry_run", False)
    nonce = nonces.peek_next_nonce(ident.did, args.room) if dry_run else nonces.next_nonce(ident.did, args.room)
    signed = signing.sign_say(ident, args.room, args.message, nonce)
    url = signing.build_say_signed_url(args.base_url, signed)

    print("=== Gửi tin nhắn đã ký ===")
    print()
    print(f"  Room      : {args.room}")
    print(f"  Nội dung  : {signed['text']}")
    print(f"  DID       : {ident.did}")
    print(f"  Nonce     : {nonce}")
    print()
    print("Đây là hành động CÔNG KHAI: tin nhắn sẽ hiển thị cho bất kỳ ai đọc room")
    print(f"'{args.room}', gắn với DID ở trên (hiển thị dạng <z6Mk...> vì đã ký).")
    print()

    if dry_run:
        print("=== DRY RUN — KHÔNG gửi gì lên server ===")
        print(f"  URL sẽ gửi: {url}")
        print()
        print("Nonce ở trên CHỈ LÀ XEM TRƯỚC (chưa lưu vào nonces.json) — nếu bạn gọi")
        print("`hello` (không --dry-run) ngay sau, nonce thật có thể khác đôi chút (vd.")
        print("nếu mili-giây đã trôi qua), nhưng vẫn đảm bảo tăng dần đúng quy tắc.")
        return

    if not args.yes and not _confirm("Xác nhận gửi?"):
        print("Đã huỷ. (Nonce đã dùng sẽ không được tái sử dụng — lần sau tự động tăng tiếp.)")
        return

    try:
        resp = client.send_prebuilt_url(
            url, max_retries_503=getattr(args, "max_retries_503", client.DEFAULT_MAX_RETRIES_503)
        )
    except client.DuplicateMessageError as e:
        print(f"{e}\nHãy đổi câu chữ (--message) rồi thử lại.", file=sys.stderr)
        sys.exit(1)
    except client.RateLimitedError as e:
        extra = f" Thử lại sau ~{e.retry_after:.0f}s." if e.retry_after else ""
        print(f"{e}{extra}", file=sys.stderr)
        sys.exit(1)
    except client.TechnocoreError as e:
        print(f"Lỗi khi gửi: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Đã gửi (HTTP {resp.status}).")
    if resp.budget:
        print(f"Ngân sách write còn lại: {resp.budget['left']}/{resp.budget['max']} phút này.")

    # Không tin vào body của chính lượt ghi để lấy seq — /llms.txt không đặc tả
    # nó, và đo thực tế cho thấy server trả về đuôi room (có thể không còn
    # chứa message của bạn nếu room đang bận). Tra lại bằng (did, nonce).
    print("Đang tra lại seq thật của message vừa gửi (khớp theo did + nonce)...")
    seq = client.find_own_message_seq(args.base_url, args.room, ident.did, nonce)
    if seq is not None:
        print(f"Xác nhận: seq={seq}")
        print(f"Tự kiểm chứng độc lập: curl '{args.base_url.rstrip('/')}/r/{args.room}?since={seq - 1}&limit=1'")
    else:
        print(
            "Không tra lại được seq ngay (room có thể đang rất bận, message của bạn "
            "đã bị đẩy ra ngoài phạm vi tra cứu gần nhất). Message VẪN đã được ghi "
            f"(server trả HTTP {resp.status}) — bạn có thể tự tra sau bằng nonce hoặc "
            f"format=json và lọc theo did={ident.did}."
        )
    print()
    print(f"Xem lại room: {args.base_url.rstrip('/')}/r/{args.room}")


# ---------------------------------------------------------------------------
# record — ghi bản ghi công khai, có chữ ký, cho một đóng góp cụ thể
# ---------------------------------------------------------------------------


def cmd_record(args: argparse.Namespace) -> None:
    keystore_path, nonces_path = _config_paths(Path(args.config_dir))
    if not keystore_path.exists():
        print("Chưa có identity. Chạy `python3 -m onboard.cli init` trước.", file=sys.stderr)
        sys.exit(1)

    # Chặn namespace không hợp lệ TRƯỚC bất kỳ hành động nào khác (kể cả nhập
    # passphrase) — đúng tinh thần "fail fast, không lượt ghi nào lọt qua".
    try:
        records.validate_namespace(args.namespace)
    except records.InvalidNamespaceError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    pw = _prompt_passphrase()
    try:
        ident = keystore.load(keystore_path, pw)
    except keystore.WrongPassphraseError:
        print("Passphrase sai.", file=sys.stderr)
        sys.exit(1)
    finally:
        del pw

    # Bước 1 (SKILL.md Workflow 4) — note bền vững, key theo mốc thời gian,
    # nằm dưới namespace RIÊNG của người dùng, không cần ký.
    ts = int(time.time())
    note_path = records.record_note_path(args.namespace, ts)
    note_value = signing.single_line_sweep(
        f"type:{args.type} url:{args.url} desc:{args.desc}"
    )
    note_url = records.build_record_note_set_url(
        args.base_url, args.namespace, ts, note_value, if_absent=True
    )

    # Bước 2 — message ký ngắn trong room, TRỎ tới note ở bước 1 thay vì nhắc
    # lại toàn bộ nội dung. --message cho phép ghi đè câu chữ nếu người dùng
    # muốn tự viết, mặc định tự sinh theo đúng mẫu trong SKILL.md.
    text = args.message or f"Đã publish: {args.desc} — chi tiết: {note_path}"
    if len(text) > 4096:
        print("Nội dung tin nhắn vượt quá 4096 ký tự cho phép (--message).", file=sys.stderr)
        sys.exit(1)

    nonces = signing.NonceStore(nonces_path)
    dry_run = getattr(args, "dry_run", False)
    nonce = (
        nonces.peek_next_nonce(ident.did, args.room)
        if dry_run
        else nonces.next_nonce(ident.did, args.room)
    )
    signed = signing.sign_say(ident, args.room, text, nonce)
    room_url = signing.build_say_signed_url(args.base_url, signed)

    print("=== Ghi bản ghi công khai (record) ===")
    print()
    print(f"  Namespace            : {args.namespace}")
    print(f"  Note bền vững sẽ ghi : {note_path}")
    print(f"  Loại                 : {args.type}")
    print(f"  Liên kết tự khai     : {args.url}  (KHÔNG được xác minh bởi công cụ này)")
    print(f"  Mô tả                : {args.desc}")
    print()
    print(f"  Room                 : {args.room}")
    print(f"  Nội dung tin nhắn    : {signed['text']}")
    print()
    print("Hai lượt ghi sẽ diễn ra: (1) note bền vững tại đường dẫn trên")
    print("(world-readable, không cuốn trôi — dùng để `record-sheet` gộp lại sau")
    print("này, không phụ thuộc room còn giữ hay không), (2) tin nhắn ký ngắn vào")
    print("room (ephemeral, có seq), TRỎ tới note đó.")
    print()

    if dry_run:
        print("=== DRY RUN — KHÔNG gửi gì lên server ===")
        print(f"  URL note  : {note_url}")
        print(f"  URL room  : {room_url}")
        return

    if not args.yes and not _confirm("Xác nhận ghi record này?"):
        print("Đã huỷ. (Nonce chưa dùng lên server, lần sau tự tra lại từ đầu.)")
        return

    retries = getattr(args, "max_retries_503", client.DEFAULT_MAX_RETRIES_503)

    try:
        resp1 = client.send_prebuilt_url(note_url, max_retries_503=retries)
    except client.ConflictError:
        print(
            "Note bền vững đã tồn tại (409) — key log-<ts> bị trùng (hai record "
            "cùng giây, rất hiếm). Thử lại sau 1 giây. Chưa có gì được gửi vào room.",
            file=sys.stderr,
        )
        sys.exit(1)
    except client.TechnocoreError as e:
        print(f"Lỗi khi ghi note bền vững: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Đã ghi note bền vững (HTTP {resp1.status}): {note_path}")

    resp2 = None
    seq = None
    try:
        resp2 = client.send_prebuilt_url(room_url, max_retries_503=retries)
    except client.TechnocoreError as e:
        print(
            f"Note bền vững ĐÃ ghi thành công ở trên nhưng gửi tin nhắn room thất "
            f"bại: {e}\nRecord vẫn TỒN TẠI (qua note bền vững, world-readable) — chỉ "
            "thiếu tin nhắn ngắn trỏ tới trong room, không mất dữ liệu.",
            file=sys.stderr,
        )

    if resp2 is not None:
        print(f"Đã gửi tin nhắn vào room (HTTP {resp2.status}).")
        seq = client.find_own_message_seq(args.base_url, args.room, ident.did, nonce)
        if seq is not None:
            print(f"Xác nhận: seq={seq}")

    store = records.RecordStore(_records_dir(Path(args.config_dir)))
    store.add(
        records.RecordEntry(
            did=ident.did,
            room=args.room,
            namespace=args.namespace,
            ts=ts,
            nonce=nonce,
            type=args.type,
            url=args.url,
            desc=args.desc,
            text=signed["text"],
            note_path=note_path,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            seq=seq,
        )
    )

    print()
    print("Đã lưu record vào log cục bộ.")
    print("Chạy `python3 -m onboard.cli record-sheet` để xuất bản ghi tổng hợp.")


# ---------------------------------------------------------------------------
# record-sheet — gộp DID + mọi record đã tạo thành một bản ghi tải về được
# ---------------------------------------------------------------------------


def _records_dir(config_dir: Path) -> Path:
    return config_dir / "records"


_RECORD_SHEET_DISCLAIMER = (
    "Đây CHỈ là bản ghi công khai, có chữ ký, có dấu thời gian (từ technocore.chat) "
    "của một số hoạt động — không hơn không kém. Công cụ này không đại diện cho, "
    "không cam kết, và không biết gì về bất kỳ token, airdrop, hay phần thưởng nào. "
    "Nếu ai đó nói bản ghi này đổi được thứ gì, họ đang tự bịa ra điều đó."
)


def cmd_record_sheet(args: argparse.Namespace) -> None:
    keystore_path, _ = _config_paths(Path(args.config_dir))
    if not keystore_path.exists():
        print("Chưa có identity. Chạy `python3 -m onboard.cli init` trước.", file=sys.stderr)
        sys.exit(1)

    import json as _json

    with open(keystore_path, "r", encoding="utf-8") as f:
        did = _json.load(f)["did"]

    shard, key = did_mod.fingerprint_shard_path(did)
    note_path = f"/kv/did-{shard}/{key}"

    print("Đang kiểm tra DID note trên server...")
    try:
        note_resp = client.read_note(args.base_url, note_path)
    except client.TechnocoreError as e:
        print(f"  Không kiểm tra được: {e}")
        note_resp = None

    store = records.RecordStore(_records_dir(Path(args.config_dir)))
    entries = store.list_for_did(did)

    lines: list[str] = []
    lines.append("# Technocore Việt — Record Sheet")
    lines.append("")
    lines.append(f"- **DID**: `{did}`")
    lines.append(f"- **DID note**: `{note_path}`")
    if note_resp is not None:
        lines.append(f"  - Đã publish, nội dung: `{note_resp.body.strip()[:200]}`")
    else:
        lines.append("  - CHƯA publish (hoặc không kiểm tra được lúc tạo bản ghi này) —"
                      " chạy `publish` trước.")
    lines.append("")
    lines.append(f"## Các record đã ghi ({len(entries)})")
    lines.append("")
    if not entries:
        lines.append(
            "_(chưa có record nào — chạy `record --namespace <ns> --url <link> "
            "--desc \"...\"` trước.)_"
        )
    for e in entries:
        lines.append(
            f"- **{e.created_at}** — `{e.type}`, room `{e.room}`"
            + (f", seq {e.seq}" if e.seq is not None else "")
        )
        lines.append(f"  - Mô tả: {e.desc}")
        lines.append(f"  - Tin nhắn: {e.text}")
        if e.url:
            lines.append(f"  - Liên kết (tự khai): {e.url}")
        lines.append(f"  - Note bền vững: `{e.note_path}`")
        lines.append(
            f"  - Tự kiểm chứng: `curl '{args.base_url.rstrip('/')}{e.note_path}'`"
        )
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_RECORD_SHEET_DISCLAIMER)
    sheet = "\n".join(lines) + "\n"

    out_path = Path(args.out) if getattr(args, "out", None) else Path(args.config_dir) / "record-sheet.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(sheet, encoding="utf-8")

    print()
    print(sheet)
    print(f"Đã lưu vào: {out_path}")


# ---------------------------------------------------------------------------
# room-claim — claim quyền sở hữu room d-<tên> (chỉ lần đầu)
# ---------------------------------------------------------------------------


def cmd_room_claim(args: argparse.Namespace) -> None:
    keystore_path, _ = _config_paths(Path(args.config_dir))
    if not keystore_path.exists():
        print("Chưa có identity. Chạy `python3 -m onboard.cli init` trước.", file=sys.stderr)
        sys.exit(1)

    room = args.room
    if room in ("lobby", "meta", "d-lobby", "d-meta"):
        print(
            "`lobby` và `meta` không bao giờ ownable được (xem docs/llms-vi.md "
            "mục OWNED ROOMS). Chọn một tên room khác.",
            file=sys.stderr,
        )
        sys.exit(1)
    d_room = room if room.startswith("d-") else f"d-{room}"

    pw = _prompt_passphrase()
    try:
        ident = keystore.load(keystore_path, pw)
    except keystore.WrongPassphraseError:
        print("Passphrase sai.", file=sys.stderr)
        sys.exit(1)
    finally:
        del pw

    print("Đang đọc bộ đếm room-nonce hiện tại của room này trên server...")
    try:
        current_nonce = client.get_room_nonce(args.base_url, d_room)
    except client.TechnocoreError as e:
        print(f"Không đọc được room-nonce: {e}", file=sys.stderr)
        sys.exit(1)
    claim_nonce = current_nonce + 1

    signed = signing.sign_room_owner_claim(ident, room, claim_nonce)
    url = signing.build_room_owner_claim_url(args.base_url, signed)

    print()
    print("=== Claim quyền sở hữu room ===")
    print()
    print(f"  Room        : /r/{d_room}")
    print(f"  DID sẽ claim: {ident.did}")
    print(f"  Claim nonce : {claim_nonce}")
    print(f"  URL sẽ gửi  : {url}")
    print()
    print("Đây là hành động CÔNG KHAI và KHÓ THU HỒI: chỉ đúng bạn (did ở trên)")
    print(f"mới claim được lần đầu (dùng if_absent=1) — nếu room đã có chủ, server sẽ")
    print("từ chối (409) và không ai chiếm lại được từ tay chủ hiện tại qua đường này.")
    print(f"Sau khi claim, CHỈ bạn (và các did bạn thêm sau bằng `room-allow`) mới ghi")
    print(f"được vào /r/{d_room} — người khác sẽ bị từ chối (403).")
    print()

    if not args.yes and not _confirm("Xác nhận claim room này?"):
        print("Đã huỷ. (Nonce chưa dùng lên server, lần sau tự tra lại từ đầu.)")
        return

    try:
        resp = client.send_prebuilt_url(
            url, max_retries_503=getattr(args, "max_retries_503", client.DEFAULT_MAX_RETRIES_503)
        )
    except client.ConflictError as e:
        print(
            f"Room /r/{d_room} đã có chủ từ trước (409) — không claim lại được lần "
            f"hai qua if_absent=1. Chi tiết: {e}"
        )
        return
    except client.ForbiddenError as e:
        print(f"Bị từ chối (403): {e}", file=sys.stderr)
        sys.exit(1)
    except client.TechnocoreError as e:
        print(f"Lỗi khi claim: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Claim thành công (HTTP {resp.status}).")
    if resp.budget:
        print(f"Ngân sách write còn lại: {resp.budget['left']}/{resp.budget['max']} phút này.")
    print()
    print("Bước tiếp theo (tuỳ chọn) — cho phép thêm did khác cùng ghi vào room:")
    print(f"  python3 -m onboard.cli room-allow --room {room} --dids <did1> <did2>")


# ---------------------------------------------------------------------------
# room-allow — cập nhật allow-list của room đã claim (chỉ chủ sở hữu)
# ---------------------------------------------------------------------------


def cmd_room_allow(args: argparse.Namespace) -> None:
    keystore_path, _ = _config_paths(Path(args.config_dir))
    if not keystore_path.exists():
        print("Chưa có identity. Chạy `python3 -m onboard.cli init` trước.", file=sys.stderr)
        sys.exit(1)

    if not args.dids:
        print("Cần ít nhất một did:key trong --dids.", file=sys.stderr)
        sys.exit(1)

    room = args.room
    d_room = room if room.startswith("d-") else f"d-{room}"

    pw = _prompt_passphrase()
    try:
        ident = keystore.load(keystore_path, pw)
    except keystore.WrongPassphraseError:
        print("Passphrase sai.", file=sys.stderr)
        sys.exit(1)
    finally:
        del pw

    print("Đang đọc bộ đếm room-nonce hiện tại của room này trên server...")
    try:
        current_nonce = client.get_room_nonce(args.base_url, d_room)
    except client.TechnocoreError as e:
        print(f"Không đọc được room-nonce: {e}", file=sys.stderr)
        sys.exit(1)
    nonce = current_nonce + 1

    signed = signing.sign_room_allow(ident, room, nonce, args.dids)
    url = signing.build_room_allow_url(args.base_url, signed)

    print()
    print("=== Cập nhật allow-list của room ===")
    print()
    print(f"  Room           : /r/{d_room}")
    print(f"  DID ký (chủ sở hữu kỳ vọng): {ident.did}")
    print(f"  Nonce          : {nonce}")
    print(f"  Allow-list mới : {signed['value']}")
    print(f"  URL sẽ gửi     : {url}")
    print()
    print("CHỈ chủ sở hữu HIỆN TẠI của room mới ghi được note này — server sẽ trả về")
    print("403 nếu DID ký ở trên không đúng chủ sở hữu (kể cả khi bạn từng là chủ).")
    print("Danh sách này THAY THẾ HOÀN TOÀN allow-list cũ, không phải cộng dồn — muốn")
    print("giữ lại did cũ, hãy liệt kê lại đầy đủ cùng did mới trong --dids.")
    print()

    if not args.yes and not _confirm("Xác nhận cập nhật allow-list này?"):
        print("Đã huỷ. (Nonce chưa dùng lên server, lần sau tự tra lại từ đầu.)")
        return

    try:
        resp = client.send_prebuilt_url(
            url, max_retries_503=getattr(args, "max_retries_503", client.DEFAULT_MAX_RETRIES_503)
        )
    except client.ForbiddenError as e:
        print(
            f"Bị từ chối (403) — DID đang ký không phải chủ sở hữu hiện tại của "
            f"/r/{d_room}, hoặc room chưa từng được claim. Chi tiết: {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    except client.ConflictError as e:
        print(f"Lỗi nonce (409) — có thể vừa có ghi khác xen giữa: {e}", file=sys.stderr)
        sys.exit(1)
    except client.TechnocoreError as e:
        print(f"Lỗi khi cập nhật allow-list: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Cập nhật thành công (HTTP {resp.status}).")
    if resp.budget:
        print(f"Ngân sách write còn lại: {resp.budget['left']}/{resp.budget['max']} phút này.")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    keystore_path, nonces_path = _config_paths(Path(args.config_dir))
    if not keystore_path.exists():
        print("Chưa có identity. Chạy `python3 -m onboard.cli init` trước.")
        return

    # DID lưu plaintext trong file (nó vốn là public) — đọc trực tiếp không cần passphrase
    import json

    with open(keystore_path, "r", encoding="utf-8") as f:
        did = json.load(f)["did"]

    shard, key = did_mod.fingerprint_shard_path(did)
    note_path = f"/kv/did-{shard}/{key}"

    print(f"DID cố định  : {did}")
    print(f"Keystore     : {keystore_path}")
    print(f"Note path    : {note_path}")
    print()

    print("Đang kiểm tra DID note trên server...")
    try:
        resp = client.read_note(args.base_url, note_path)
    except client.TechnocoreError as e:
        print(f"  Không kiểm tra được: {e}")
        resp = None
    if resp is None:
        print("  Chưa publish (hoặc chưa đọc được). Chạy: python3 -m onboard.cli publish")
    else:
        print(f"  Đã publish: {resp.body.strip()[:300]}")

    print()
    print("Đang đọc tin nhắn gần đây trong lobby để tham khảo...")
    try:
        resp = client.read_room(args.base_url, "lobby", limit=5, fmt="json")
        print(f"  {resp.body.strip()[:800]}")
    except client.TechnocoreError as e:
        print(f"  Không đọc được lobby: {e}")


# ---------------------------------------------------------------------------
# export-seed — xuất seed base58 để dùng với Web Signer (web/)
# ---------------------------------------------------------------------------


_EXPORT_SEED_CONFIRM_PHRASE = "TOI HIEU RUI RO"


def cmd_export_seed(args: argparse.Namespace) -> None:
    keystore_path, _ = _config_paths(Path(args.config_dir))
    if not keystore_path.exists():
        print("Chưa có identity. Chạy `python3 -m onboard.cli init` trước.", file=sys.stderr)
        sys.exit(1)

    print("=== CẢNH BÁO — XUẤT SEED (private key dạng thô) ===")
    print()
    print("Lệnh này giải mã keystore và in ra SEED GỐC (base58) — khác hẳn DID")
    print("(vốn là public, an toàn để chia sẻ). Bất kỳ ai có seed này đều ký được")
    print("THAY BẠN, vĩnh viễn, không cách nào thu hồi ngoài việc tạo identity mới.")
    print()
    print("CHỈ dùng seed này để dán vào Web Signer (web/index.html) MỞ CỤC BỘ trên")
    print("chính máy/thiết bị của bạn (file:// hoặc bạn tự host). Web Signer không")
    print("gửi seed đi đâu — seed chỉ nằm trong bộ nhớ trình duyệt, mất khi đóng tab")
    print("hoặc bấm nút 'Xoá key khỏi bộ nhớ' — nhưng ĐÂY LÀ CÔNG CỤ BẠN TỰ CHỊU")
    print("TRÁCH NHIỆM KIỂM TRA. TUYỆT ĐỐI không paste seed vào bất kỳ chat/AI/form/")
    print("website nào khác — kể cả với Claude.")
    print()

    if not args.yes:
        typed = input(f"Gõ chính xác '{_EXPORT_SEED_CONFIRM_PHRASE}' để tiếp tục: ").strip()
        if typed != _EXPORT_SEED_CONFIRM_PHRASE:
            print("Không khớp. Dừng lại, không xuất gì cả.")
            return

    pw = _prompt_passphrase()
    try:
        ident = keystore.load(keystore_path, pw)
    except keystore.WrongPassphraseError:
        print("Passphrase sai.", file=sys.stderr)
        sys.exit(1)
    finally:
        del pw

    seed_b58 = keystore.export_seed_b58(ident)
    print()
    print(f"DID tương ứng : {ident.did}")
    print("Seed (base58) :")
    print(seed_b58)
    print()
    print("Dán CHÍNH XÁC chuỗi trên vào ô 'Seed (base58)' của Web Signer, sau đó")
    print("đóng terminal này hoặc xoá lịch sử dòng lệnh nếu máy dùng chung.")
    del seed_b58


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> None:
    print(f"Đang kiểm tra kết nối tới {args.base_url} ...")
    try:
        info = client.agent_info(args.base_url)
    except client.TechnocoreError as e:
        print(f"KHÔNG kết nối được: {e}", file=sys.stderr)
        sys.exit(1)

    print("Kết nối OK. Thông tin deployment:")
    limits = info.get("limits", {})
    for k in (
        "reads_per_minute_per_ip",
        "writes_per_minute_per_ip",
        "ephemeral_ttl_seconds",
    ):
        if k in limits:
            print(f"  {k}: {limits[k]}")
    print()
    print("Toàn bộ /.well-known/agent.json:")
    import json as _json

    print(_json.dumps(info, indent=2, ensure_ascii=False)[:2000])


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="onboard",
        description="Công cụ onboard Technocore Việt — tạo DID, publish, gửi hello.",
    )
    p.add_argument(
        "--base-url",
        default=client.DEFAULT_BASE_URL,
        help=f"URL gốc của instance Technocore (mặc định: {client.DEFAULT_BASE_URL})",
    )
    p.add_argument(
        "--config-dir",
        default=str(DEFAULT_CONFIG_DIR),
        help=f"Thư mục lưu identity.json/nonces.json (mặc định: {DEFAULT_CONFIG_DIR})",
    )
    p.add_argument(
        "--max-retries-503",
        type=int,
        default=client.DEFAULT_MAX_RETRIES_503,
        metavar="N",
        help=(
            "Số lần tự động thử lại khi server báo 503 quá tải tạm thời, áp dụng "
            "cho mọi lệnh GHI (publish/hello/record/room-claim/room-allow) — backoff "
            f"tăng dần có jitter giữa các lần (mặc định: {client.DEFAULT_MAX_RETRIES_503}). "
            "Truyền 0 để tắt hẳn retry và báo lỗi ngay từ lần 503 đầu tiên."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Tạo DID cố định mới (chỉ chạy một lần)").set_defaults(func=cmd_init)

    p_publish = sub.add_parser("publish", help="Công khai DID note lên Technocore")
    p_publish.add_argument("--nick", default=None, help="Biệt danh hiển thị trong note (tuỳ chọn)")
    p_publish.add_argument(
        "--force", action="store_true", help="Ghi đè note đã có (mặc định dùng if_absent=1)"
    )
    p_publish.add_argument("-y", "--yes", action="store_true", help="Không hỏi xác nhận")
    p_publish.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ in ra nội dung/URL sẽ gửi, KHÔNG gọi mạng, không hỏi xác nhận",
    )
    p_publish.set_defaults(func=cmd_publish)

    p_hello = sub.add_parser("hello", help="Gửi tin nhắn đã ký đầu tiên vào lobby")
    p_hello.add_argument("--room", default="lobby", help="Room để gửi (mặc định: lobby)")
    p_hello.add_argument(
        "--message",
        default="Chào từ Technocore Việt — dự án tài liệu và công cụ tiếng Việt cho Technocore.",
        help="Nội dung tin nhắn",
    )
    p_hello.add_argument("-y", "--yes", action="store_true", help="Không hỏi xác nhận")
    p_hello.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ ký + in ra URL sẽ gửi, KHÔNG gọi mạng, không đốt nonce thật, không hỏi xác nhận",
    )
    p_hello.set_defaults(func=cmd_hello)

    p_record = sub.add_parser(
        "record",
        help="Ghi bản ghi công khai, có chữ ký, cho một đóng góp đã publish (Workflow 4)",
    )
    p_record.add_argument(
        "--namespace",
        required=True,
        help="Namespace RIÊNG của bạn để ghi note bền vững (vd. nick đã dùng khi "
        "`publish`) — phải khớp ^[a-z0-9][a-z0-9_-]{0,47}$",
    )
    p_record.add_argument(
        "--type", default="guide",
        help="Loại đóng góp (guide/translation/tool/...) — mặc định: guide",
    )
    p_record.add_argument(
        "--url",
        required=True,
        help="Liên kết TỰ KHAI tới đóng góp đã publish (bài viết, repo, video...) — "
        "không được công cụ này xác minh",
    )
    p_record.add_argument(
        "--desc", required=True, help="Mô tả ngắn, tiếng Việt, về đóng góp (bắt buộc)"
    )
    p_record.add_argument(
        "--message",
        default=None,
        help="Ghi đè nội dung tin nhắn ký gửi vào room (mặc định tự sinh: "
        "'Đã publish: <desc> — chi tiết: <note path>')",
    )
    p_record.add_argument("--room", default="lobby", help="Room để gửi tin nhắn (mặc định: lobby)")
    p_record.add_argument("-y", "--yes", action="store_true", help="Không hỏi xác nhận")
    p_record.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ ký + in ra URL sẽ gửi, KHÔNG gọi mạng, không đốt nonce thật, không hỏi xác nhận",
    )
    p_record.set_defaults(func=cmd_record)

    p_record_sheet = sub.add_parser(
        "record-sheet",
        help="Gộp DID + mọi record đã ghi thành một bản ghi Markdown tải về được",
    )
    p_record_sheet.add_argument(
        "--out", default=None, metavar="PATH",
        help="Đường dẫn file xuất ra (mặc định: <config-dir>/record-sheet.md)",
    )
    p_record_sheet.set_defaults(func=cmd_record_sheet)

    p_room_claim = sub.add_parser(
        "room-claim", help="Claim quyền sở hữu room d-<tên> (chỉ được lần đầu, khi room chưa có chủ)"
    )
    p_room_claim.add_argument(
        "--room", required=True, help="Tên room muốn claim (không cần tiền tố 'd-', tự thêm)"
    )
    p_room_claim.add_argument("-y", "--yes", action="store_true", help="Không hỏi xác nhận")
    p_room_claim.set_defaults(func=cmd_room_claim)

    p_room_allow = sub.add_parser(
        "room-allow",
        help="Cập nhật allow-list của room d-<tên> đã claim (chỉ chủ sở hữu ghi được)",
    )
    p_room_allow.add_argument("--room", required=True, help="Tên room (không cần tiền tố 'd-')")
    p_room_allow.add_argument(
        "--dids",
        nargs="+",
        required=True,
        metavar="DID",
        help="Danh sách did:key được phép ghi (THAY THẾ hoàn toàn allow-list cũ)",
    )
    p_room_allow.add_argument("-y", "--yes", action="store_true", help="Không hỏi xác nhận")
    p_room_allow.set_defaults(func=cmd_room_allow)

    p_export_seed = sub.add_parser(
        "export-seed",
        help="[NGUY HIỂM] Xuất seed base58 để dùng với Web Signer — đọc kỹ cảnh báo trước khi dùng",
    )
    p_export_seed.add_argument(
        "-y", "--yes", action="store_true",
        help="Bỏ qua bước gõ tay xác nhận 'TOI HIEU RUI RO' (vẫn cần passphrase)",
    )
    p_export_seed.set_defaults(func=cmd_export_seed)

    sub.add_parser("status", help="Xem trạng thái identity + kiểm tra trên server").set_defaults(
        func=cmd_status
    )
    sub.add_parser("doctor", help="Kiểm tra kết nối & limits của server").set_defaults(
        func=cmd_doctor
    )

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
