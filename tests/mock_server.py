"""Mock Technocore server cho eval SKILL.md — chạy local, không đụng technocore.chat thật.

Mô phỏng đủ các endpoint mà onboard/client.py + cli.py thực sự gọi:
  GET /.well-known/agent.json
  GET /kv/<ns>/<key>                       (đọc note, 404 nếu chưa có)
  GET /kv/<ns>/<key>/set/<value>[?if_absent=1]
  GET /r/<room>?limit=N&format=json        (đọc room)
  GET /r/<room>/say-signed/<did>/<sig>/<nonce>/<text>
  GET /kv/room-owners/<d-room>/set-signed/<did>/<sig>/<claim_nonce>/<did>[?if_absent=1]
  GET /kv/room-allow/<d-room>/set-signed/<did>/<sig>/<nonce>/<value>

Mock KHÔNG verify chữ ký thật (giống nhánh say-signed) — chỉ mô phỏng đúng
NGỮ NGHĨA nonce/ownership mà /llms.txt mô tả (mục OWNED ROOMS), để test CLI
room-claim/room-allow bắt được lỗi logic (nonce trùng/thấp hơn, claim hộ did
khác, ghi allow-list khi không phải chủ sở hữu...) mà không cần crypto thật.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE = {"notes": {}, "rooms": {"lobby": []}}
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, status, body, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p != ""]
        qs = urllib.parse.parse_qs(parsed.query)

        with _lock:
            if parsed.path == "/.well-known/agent.json":
                info = {
                    "name": "mock-technocore",
                    "limits": {
                        "reads_per_minute_per_ip": 100,
                        "writes_per_minute_per_ip": 20,
                        "ephemeral_ttl_seconds": 3600,
                    },
                }
                return self._send(200, json.dumps(info))

            if parts[:1] == ["kv"] and len(parts) == 3 and parts[2] not in ("set",):
                ns, key = parts[1], parts[2]
                note_key = f"{ns}/{key}"
                if note_key in STATE["notes"]:
                    return self._send(200, STATE["notes"][note_key] + " # budget: 87 of 100 reads left this minute")
                return self._send(404, "note not found")

            if parts[:1] == ["kv"] and len(parts) >= 4 and parts[3] == "set":
                ns, key = parts[1], parts[2]
                value = urllib.parse.unquote(parts[4]) if len(parts) > 4 else ""
                note_key = f"{ns}/{key}"
                if_absent = qs.get("if_absent", ["0"])[0] == "1"
                if if_absent and note_key in STATE["notes"]:
                    return self._send(409, f"current value: {STATE['notes'][note_key]}")
                STATE["notes"][note_key] = value
                return self._send(200, f"ok, set {ns}/{key} # budget: 19 of 20 writes left this minute")

            if (
                parts[:1] == ["kv"]
                and len(parts) >= 7
                and parts[1] in ("room-owners", "room-allow")
                and parts[3] == "set-signed"
            ):
                # /kv/<room-owners|room-allow>/<d-room>/set-signed/<did>/<sig>/<nonce>/<value>
                ns, d_room = parts[1], parts[2]
                did = urllib.parse.unquote(parts[4])
                nonce = int(parts[6])
                value = urllib.parse.unquote(parts[7]) if len(parts) > 7 else ""

                nonce_key = f"room-nonce/{d_room}"
                owner_key = f"room-owners/{d_room}"
                current_nonce = int(STATE["notes"].get(nonce_key, "0"))

                if nonce <= current_nonce:
                    return self._send(
                        409,
                        f"stale nonce ({nonce}) — current room-nonce for {d_room} is {current_nonce}",
                    )

                if ns == "room-owners":
                    # Claim ban đầu: phải ký bởi chính did đang được lưu làm chủ sở hữu.
                    if_absent = qs.get("if_absent", ["0"])[0] == "1"
                    if if_absent and owner_key in STATE["notes"]:
                        return self._send(
                            409, f"current value: {STATE['notes'][owner_key]}"
                        )
                    if value != did:
                        return self._send(
                            403,
                            "room-owners claim phải ký bởi đúng did:key đang được claim, "
                            f"không phải did khác (did={did}, value={value})",
                        )
                    STATE["notes"][owner_key] = did
                    STATE["notes"][nonce_key] = str(nonce)
                    return self._send(
                        200, f"ok, claimed {d_room} # budget: 19 of 20 writes left this minute"
                    )
                else:  # room-allow — chỉ chủ sở hữu HIỆN TẠI mới ghi được
                    current_owner = STATE["notes"].get(owner_key)
                    if current_owner is None:
                        return self._send(403, f"room {d_room} chưa được claim, chưa có chủ sở hữu")
                    if current_owner != did:
                        return self._send(
                            403,
                            f"chỉ chủ sở hữu ({current_owner}) mới ghi được room-allow, "
                            f"không phải {did}",
                        )
                    STATE["notes"][f"room-allow/{d_room}"] = value
                    STATE["notes"][nonce_key] = str(nonce)
                    return self._send(
                        200,
                        f"ok, allow-list updated {d_room} # budget: 19 of 20 writes left this minute",
                    )

            if parts[:1] == ["r"] and len(parts) == 2:
                room = parts[1]
                msgs = STATE["rooms"].setdefault(room, [])
                limit = int(qs.get("limit", ["20"])[0])
                fmt = qs.get("format", ["text"])[0]
                shown = msgs[-limit:]
                if fmt == "json":
                    envelope = {
                        "room": room,
                        "count": len(shown),
                        "first_seq": shown[0]["seq"] if shown else None,
                        "last_seq": shown[-1]["seq"] if shown else None,
                        "messages": shown,
                    }
                    body = json.dumps(envelope, ensure_ascii=False)
                else:
                    def _short(did_str):
                        tail = did_str.split(":")[-1]
                        return f"<{tail[:8]}...>" if did_str.startswith("did:key:") else f"<~{did_str}>"

                    body = "\n".join(f"[{m['seq']}] {_short(m['from'])}: {m['text']}" for m in shown)
                return self._send(200, body + "\n# budget: 91 of 100 reads left this minute")

            if len(parts) >= 3 and parts[0] == "r" and parts[2] == "say-signed":
                # /r/<room>/say-signed/<did>/<sig>/<nonce>/<text>
                room = parts[1]
                did = urllib.parse.unquote(parts[3])
                nonce = parts[5]
                text = urllib.parse.unquote(parts[6]) if len(parts) > 6 else ""
                msgs = STATE["rooms"].setdefault(room, [])
                # mô phỏng 422: nội dung trùng với message gần nhất
                if msgs and msgs[-1]["text"] == text and msgs[-1]["from"].startswith("did:"):
                    return self._send(422, "duplicate content, refused for 8 more seconds")
                seq = len(msgs) + 1
                # Lưu DID ĐẦY ĐỦ — theo RENDERING trong llms.txt thật: bản text
                # rút gọn thành <z6Mk...>, nhưng ?format=json mang DID đầy đủ
                # trong trường `from`. Rút gọn chỉ áp dụng lúc render text (xem
                # do_GET nhánh đọc room bên dưới).
                msgs.append({"seq": seq, "from": did, "text": text, "nonce": nonce})
                return self._send(200, f"ok, seq={seq} # budget: 18 of 20 writes left this minute")

            return self._send(404, "unmapped path")


def start() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


if __name__ == "__main__":
    s = start()
    print(f"mock server on http://127.0.0.1:{s.server_port}")
    while True:
        time.sleep(1)
