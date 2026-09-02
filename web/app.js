/*
 * app.js — logic THUẦN (không đụng DOM) của Web Signer.
 *
 * Đây là bản JS song song với ba module Python: identity/did.py,
 * identity/signing.py, onboard/records.py — MỌI hàm ở đây phải cho kết quả
 * byte-for-byte giống hệt bản Python tương ứng (đã cross-verify, xem
 * web/README.md mục "Đã kiểm chứng"). Nếu sửa bên Python, PHẢI sửa lại
 * tương ứng ở đây và chạy lại script cross-check.
 *
 * Không có bất kỳ `fetch`/network call nào trong file này — chỉ tính toán
 * và trả về chuỗi/URL. Việc gọi mạng (nếu người dùng chọn gửi) nằm ở ui.js,
 * tách biệt rõ ràng khỏi phần crypto.
 */

import * as ed from './vendor/noble-ed25519.js';

// ---------------------------------------------------------------------------
// base58 (Bitcoin alphabet) — khớp thư viện Python `base58` (b58encode/b58decode)
// ---------------------------------------------------------------------------

const B58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const B58_MAP = (() => {
  const m = new Map();
  for (let i = 0; i < B58_ALPHABET.length; i++) m.set(B58_ALPHABET[i], i);
  return m;
})();

export function b58encode(bytes) {
  let zeros = 0;
  while (zeros < bytes.length && bytes[zeros] === 0) zeros++;

  let num = 0n;
  for (const b of bytes) num = (num << 8n) | BigInt(b);

  let out = '';
  while (num > 0n) {
    const rem = num % 58n;
    num = num / 58n;
    out = B58_ALPHABET[Number(rem)] + out;
  }
  return '1'.repeat(zeros) + out;
}

export class Base58DecodeError extends Error {}

export function b58decode(str) {
  if (str.length === 0) return new Uint8Array(0);
  let zeros = 0;
  while (zeros < str.length && str[zeros] === '1') zeros++;

  let num = 0n;
  for (let i = zeros; i < str.length; i++) {
    const ch = str[i];
    const val = B58_MAP.get(ch);
    if (val === undefined) {
      throw new Base58DecodeError(`Ký tự base58 không hợp lệ: ${JSON.stringify(ch)}`);
    }
    num = num * 58n + BigInt(val);
  }

  const bodyBytes = [];
  while (num > 0n) {
    bodyBytes.unshift(Number(num & 0xffn));
    num >>= 8n;
  }
  return new Uint8Array([...new Array(zeros).fill(0), ...bodyBytes]);
}

// ---------------------------------------------------------------------------
// hex <-> bytes (tiện ích nhỏ)
// ---------------------------------------------------------------------------

export function bytesToHex(bytes) {
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
}

// ---------------------------------------------------------------------------
// single-line sweep — khớp identity... không, khớp identity/signing.py:
// single_line_sweep(). Python quét theo unicodedata.category thuộc
// {Cc,Cf,Cs,Co,Zl,Zp} -> thay bằng khoảng trắng, rồi .strip(). Dùng Unicode
// property escapes của JS regex (\p{Cc} v.v., cờ `u`) để bám sát ĐÚNG cùng
// một bảng phân loại Unicode General_Category, thay vì tự liệt kê range tay
// (dễ sai/lệch phiên bản Unicode).
// ---------------------------------------------------------------------------

const SWEEP_RE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Zl}\p{Zp}]/gu;

export function singleLineSweep(text) {
  return text.replace(SWEEP_RE, ' ').trim();
}

// ---------------------------------------------------------------------------
// percent-encoding khớp urllib.parse.quote(s, safe='') của Python: chỉ
// [A-Za-z0-9_.-~] không bị encode, encode trên BYTES UTF-8, hex CHỮ HOA.
// encodeURIComponent của JS KHÔNG khớp (nó chừa lại !~*'() không encode) nên
// không dùng trực tiếp được — phải tự viết.
// ---------------------------------------------------------------------------

const PY_QUOTE_SAFE_RE = /[A-Za-z0-9_.\-~]/;

export function pyQuote(str) {
  const bytes = new TextEncoder().encode(str);
  let out = '';
  for (const b of bytes) {
    const ch = String.fromCharCode(b);
    if (b < 128 && PY_QUOTE_SAFE_RE.test(ch)) {
      out += ch;
    } else {
      out += '%' + b.toString(16).toUpperCase().padStart(2, '0');
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// base64url không padding — khớp identity/signing.py:_b64url_unpadded()
// ---------------------------------------------------------------------------

export function b64urlUnpadded(bytes) {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// ---------------------------------------------------------------------------
// did:key — khớp identity/did.py
// ---------------------------------------------------------------------------

const MULTICODEC_ED25519_PUB = new Uint8Array([0xed, 0x01]);
const DID_KEY_PREFIX = 'did:key:z';

export async function publicKeyToDid(publicKeyBytes) {
  if (publicKeyBytes.length !== 32) {
    throw new Error(`Ed25519 public key phải 32 byte, nhận ${publicKeyBytes.length}`);
  }
  const payload = new Uint8Array(2 + 32);
  payload.set(MULTICODEC_ED25519_PUB, 0);
  payload.set(publicKeyBytes, 2);
  return DID_KEY_PREFIX + b58encode(payload);
}

export async function fingerprint(did) {
  const bytes = new TextEncoder().encode(did);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return bytesToHex(new Uint8Array(digest)).slice(0, 16);
}

export async function fingerprintShardPath(did) {
  const fp = await fingerprint(did);
  return [fp.slice(0, 2), fp.slice(2)];
}

// ---------------------------------------------------------------------------
// Identity trong bộ nhớ — CHỈ tồn tại trong biến JS tạm thời của trang này.
// Không bao giờ persist (không localStorage/sessionStorage/cookie) — người
// gọi (ui.js) chịu trách nhiệm giữ nó trong 1 biến module-level và xoá khi
// người dùng bấm "Xoá key khỏi bộ nhớ" hoặc đóng tab.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Tạo seed MỚI hoàn toàn trong trình duyệt — dùng CSPRNG của Web Crypto
// (crypto.getRandomValues), KHÔNG phải Math.random(). Tương đương về mặt
// entropy với việc Python gọi os.urandom(32) trong `onboard.cli init`, chỉ
// khác nơi chạy. Không network, không lưu đĩa — người gọi (ui.js) chịu
// trách nhiệm buộc người dùng xác nhận đã lưu seed trước khi dùng tiếp.
// ---------------------------------------------------------------------------

export function generateSeedB58() {
  const seed = new Uint8Array(32);
  crypto.getRandomValues(seed);
  const seedB58 = b58encode(seed);
  seed.fill(0); // xoá bản nháp ngay, identityFromSeedB58() sẽ decode lại từ chuỗi b58
  return seedB58;
}

export async function identityFromSeedB58(seedB58) {
  const seed = b58decode(seedB58.trim());
  if (seed.length !== 32) {
    throw new Error(`Seed phải giải mã base58 ra đúng 32 byte, nhận ${seed.length} byte.`);
  }
  const publicKey = await ed.getPublicKey(seed);
  const did = await publicKeyToDid(publicKey);
  return { seed, did };
}

export function forgetIdentity(identity) {
  // Không có cách "xoá" tuyệt đối một giá trị khỏi bộ nhớ JS (GC không đảm
  // bảo), nhưng ghi đè các byte về 0 trước khi bỏ tham chiếu vẫn tốt hơn là
  // im lặng chờ GC — cùng tinh thần `del seed` phía Python.
  if (identity && identity.seed) identity.seed.fill(0);
}

// ---------------------------------------------------------------------------
// Ký tin nhắn room (say-signed) — khớp identity/signing.py: sign_say(),
// build_say_signed_url()
// ---------------------------------------------------------------------------

export async function signSay(identity, room, text, nonce) {
  const sweptText = singleLineSweep(text);
  const canonical = new TextEncoder().encode(`${room}|${nonce}|${sweptText}`);
  const signature = await ed.sign(canonical, identity.seed);
  return {
    did: identity.did,
    room,
    nonce,
    text: sweptText,
    sig: b64urlUnpadded(signature),
  };
}

export function buildSaySignedUrl(baseUrl, signed) {
  const room = pyQuote(signed.room);
  const did = pyQuote(signed.did);
  const sig = pyQuote(signed.sig);
  const nonce = String(signed.nonce);
  const text = pyQuote(signed.text);
  return `${baseUrl.replace(/\/+$/, '')}/r/${room}/say-signed/${did}/${sig}/${nonce}/${text}`;
}

// ---------------------------------------------------------------------------
// DID note (publish) — khớp identity/signing.py: build_did_note_set_url()
// (note THƯỜNG, không cần ký)
// ---------------------------------------------------------------------------

export function buildDidNoteSetUrl(baseUrl, shard, key, value, ifAbsent = false) {
  const path = `/kv/did-${pyQuote(shard)}/${pyQuote(key)}/set/${pyQuote(value)}`;
  let url = `${baseUrl.replace(/\/+$/, '')}${path}`;
  if (ifAbsent) url += '?if_absent=1';
  return url;
}

// ---------------------------------------------------------------------------
// Record (Workflow 4) — khớp onboard/records.py
// ---------------------------------------------------------------------------

const NAMESPACE_RE = /^[a-z0-9][a-z0-9_-]{0,47}$/;

export class InvalidNamespaceError extends Error {}

export function validateNamespace(namespace) {
  if (!namespace || !NAMESPACE_RE.test(namespace)) {
    throw new InvalidNamespaceError(
      `Namespace không hợp lệ: ${JSON.stringify(namespace)} — phải khớp mẫu ` +
      "^[a-z0-9][a-z0-9_-]{0,47}$ (chữ thường/số, có thể chứa '-'/'_', " +
      'bắt đầu bằng chữ hoặc số, tối đa 48 ký tự).'
    );
  }
}

export function recordKey(ts) {
  return `log-${ts}`;
}

export function recordNotePath(namespace, ts) {
  return `/kv/${namespace}/${recordKey(ts)}`;
}

export function buildRecordNoteSetUrl(baseUrl, namespace, ts, value, ifAbsent = true) {
  const path = `/kv/${pyQuote(namespace)}/${pyQuote(recordKey(ts))}/set/${pyQuote(value)}`;
  let url = `${baseUrl.replace(/\/+$/, '')}${path}`;
  if (ifAbsent) url += '?if_absent=1';
  return url;
}

// ---------------------------------------------------------------------------
// room-claim / room-allow — khớp identity/signing.py
// ---------------------------------------------------------------------------

export async function signRoomOwnerClaim(identity, room, claimNonce) {
  const dRoom = room.startsWith('d-') ? room : `d-${room}`;
  const canonical = new TextEncoder().encode(`room-owners|${dRoom}|${claimNonce}|${identity.did}`);
  const signature = await ed.sign(canonical, identity.seed);
  return { did: identity.did, room: dRoom, claimNonce, sig: b64urlUnpadded(signature) };
}

export function buildRoomOwnerClaimUrl(baseUrl, signed) {
  const room = pyQuote(signed.room);
  const did = pyQuote(signed.did);
  const sig = pyQuote(signed.sig);
  const nonce = String(signed.claimNonce);
  const sameDid = pyQuote(signed.did);
  return `${baseUrl.replace(/\/+$/, '')}/kv/room-owners/${room}/set-signed/${did}/${sig}/${nonce}/${sameDid}?if_absent=1`;
}

export async function signRoomAllow(identity, room, nonce, allowedDids) {
  const dRoom = room.startsWith('d-') ? room : `d-${room}`;
  const value = allowedDids.join(' ');
  const canonical = new TextEncoder().encode(`room-allow|${dRoom}|${nonce}|${value}`);
  const signature = await ed.sign(canonical, identity.seed);
  return { did: identity.did, room: dRoom, nonce, value, sig: b64urlUnpadded(signature) };
}

export function buildRoomAllowUrl(baseUrl, signed) {
  const room = pyQuote(signed.room);
  const did = pyQuote(signed.did);
  const sig = pyQuote(signed.sig);
  const nonce = String(signed.nonce);
  const value = pyQuote(signed.value);
  return `${baseUrl.replace(/\/+$/, '')}/kv/room-allow/${room}/set-signed/${did}/${sig}/${nonce}/${value}`;
}
