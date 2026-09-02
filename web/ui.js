import * as app from './app.js';

// ---------------------------------------------------------------------------
// State — CHỈ tồn tại trong bộ nhớ tab này. Không localStorage, không
// sessionStorage, không cookie. Mất khi đóng tab hoặc bấm "Quên khoá".
// ---------------------------------------------------------------------------

let identity = null; // { seed: Uint8Array, did: string } | null

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const el = {
  baseUrl: $('#base-url'),
  seedInput: $('#seed-input'),
  deriveBtn: $('#derive-btn'),
  forgetBtn: $('#forget-btn'),
  didOut: $('#did-out'),
  identityCard: $('#identity-card'),
  lockedNotice: $('#locked-notice'),
  panels: $('#panels'),
  tabs: $$('.tab-btn'),
};

function setLocked(locked) {
  el.panels.classList.toggle('is-locked', locked);
  el.lockedNotice.style.display = locked ? '' : 'none';
  el.forgetBtn.disabled = locked;
}

function showStatus(node, msg, kind = 'info') {
  node.textContent = msg;
  node.className = `status status-${kind}`;
}

// ---------------------------------------------------------------------------
// Derive / forget identity
// ---------------------------------------------------------------------------

el.deriveBtn.addEventListener('click', async () => {
  const seedB58 = el.seedInput.value.trim();
  if (!seedB58) {
    showStatus(el.didOut, 'Dán seed (base58) trước đã.', 'error');
    return;
  }
  try {
    identity = await app.identityFromSeedB58(seedB58);
    el.didOut.textContent = identity.did;
    el.identityCard.classList.add('is-unlocked');
    setLocked(false);
    for (const t of el.tabs) t.disabled = false;
  } catch (e) {
    showStatus(el.didOut, `Không derive được: ${e.message}`, 'error');
  }
});

el.forgetBtn.addEventListener('click', () => {
  app.forgetIdentity(identity);
  identity = null;
  el.seedInput.value = '';
  el.didOut.textContent = '(chưa có identity)';
  el.identityCard.classList.remove('is-unlocked');
  setLocked(true);
  for (const t of el.tabs) t.disabled = true;
  for (const p of $$('.panel')) p.querySelectorAll('.preview').forEach((n) => (n.hidden = true));
});

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

for (const btn of el.tabs) {
  btn.addEventListener('click', () => {
    for (const b of el.tabs) b.classList.toggle('is-active', b === btn);
    for (const p of $$('.panel')) p.classList.toggle('is-active', p.id === `panel-${btn.dataset.tab}`);
  });
}

// ---------------------------------------------------------------------------
// Helper: gọi mạng trực tiếp bằng GET tới URL đã build sẵn. Trả {ok, status, body}.
// Nếu bị CORS chặn, ném lỗi — nơi gọi hiển thị gợi ý dùng curl/mở tay.
// ---------------------------------------------------------------------------

async function sendGet(url) {
  const resp = await fetch(url, { method: 'GET' });
  const body = await resp.text();
  return { ok: resp.ok, status: resp.status, body };
}

async function fetchRoomNonce(baseUrl, dRoom) {
  const url = `${baseUrl.replace(/\/+$/, '')}/kv/room-nonce/${app.pyQuote(dRoom)}`;
  const resp = await fetch(url);
  if (resp.status === 404) return 0;
  if (!resp.ok) throw new Error(`Đọc room-nonce thất bại: HTTP ${resp.status}`);
  const text = await resp.text();
  const m = text.match(/^\s*(\d+)/);
  return m ? parseInt(m[1], 10) : 0;
}

function requireIdentity() {
  if (!identity) throw new Error('Chưa derive identity. Dán seed rồi bấm "Derive DID" trước.');
  return identity;
}

// ---------------------------------------------------------------------------
// Tab: say-signed (gửi tin nhắn)
// ---------------------------------------------------------------------------

{
  const room = $('#say-room');
  const text = $('#say-text');
  const previewBtn = $('#say-preview-btn');
  const sendBtn = $('#say-send-btn');
  const confirmBox = $('#say-confirm');
  const preview = $('#say-preview');
  const status = $('#say-status');
  let lastSigned = null;
  let lastUrl = null;

  previewBtn.addEventListener('click', async () => {
    try {
      const ident = requireIdentity();
      const nonce = Date.now();
      const signed = await app.signSay(ident, room.value.trim() || 'lobby', text.value, nonce);
      const url = app.buildSaySignedUrl(el.baseUrl.value.trim(), signed);
      lastSigned = signed;
      lastUrl = url;
      preview.hidden = false;
      preview.querySelector('.f-canonical').textContent = `${signed.room}|${signed.nonce}|${signed.text}`;
      preview.querySelector('.f-sig').textContent = signed.sig;
      preview.querySelector('.f-url').textContent = url;
      confirmBox.checked = false;
      sendBtn.disabled = true;
      showStatus(status, 'Đã ký cục bộ — kiểm tra nội dung rồi tick xác nhận để gửi.', 'info');
    } catch (e) {
      showStatus(status, e.message, 'error');
    }
  });

  confirmBox.addEventListener('change', () => {
    sendBtn.disabled = !confirmBox.checked || !lastUrl;
  });

  sendBtn.addEventListener('click', async () => {
    try {
      showStatus(status, 'Đang gửi...', 'info');
      const r = await sendGet(lastUrl);
      showStatus(status, `HTTP ${r.status}: ${r.body.slice(0, 300)}`, r.ok ? 'success' : 'error');
    } catch (e) {
      showStatus(
        status,
        `Gửi trực tiếp thất bại (có thể do CORS): ${e.message}. Copy URL ở trên và mở bằng ` +
          'curl hoặc dán vào thanh địa chỉ trình duyệt thay thế.',
        'error'
      );
    }
  });
}

// ---------------------------------------------------------------------------
// Tab: publish DID note
// ---------------------------------------------------------------------------

{
  const nick = $('#pub-nick');
  const force = $('#pub-force');
  const previewBtn = $('#pub-preview-btn');
  const sendBtn = $('#pub-send-btn');
  const confirmBox = $('#pub-confirm');
  const preview = $('#pub-preview');
  const status = $('#pub-status');
  let lastUrl = null;

  previewBtn.addEventListener('click', async () => {
    try {
      const ident = requireIdentity();
      const [shard, key] = await app.fingerprintShardPath(ident.did);
      const parts = [ident.did];
      if (nick.value.trim()) parts.push(`nick:${nick.value.trim()}`);
      parts.push('lang:vi');
      const value = app.singleLineSweep(parts.join(' '));
      const url = app.buildDidNoteSetUrl(el.baseUrl.value.trim(), shard, key, value, !force.checked);
      lastUrl = url;
      preview.hidden = false;
      preview.querySelector('.f-path').textContent = `/kv/did-${shard}/${key}`;
      preview.querySelector('.f-value').textContent = value;
      preview.querySelector('.f-url').textContent = url;
      confirmBox.checked = false;
      sendBtn.disabled = true;
      showStatus(status, 'DID note KHÔNG cần chữ ký (world-writable) — kiểm tra rồi xác nhận.', 'info');
    } catch (e) {
      showStatus(status, e.message, 'error');
    }
  });

  confirmBox.addEventListener('change', () => {
    sendBtn.disabled = !confirmBox.checked || !lastUrl;
  });

  sendBtn.addEventListener('click', async () => {
    try {
      showStatus(status, 'Đang gửi...', 'info');
      const r = await sendGet(lastUrl);
      showStatus(status, `HTTP ${r.status}: ${r.body.slice(0, 300)}`, r.ok ? 'success' : 'error');
    } catch (e) {
      showStatus(status, `Gửi trực tiếp thất bại (có thể do CORS): ${e.message}.`, 'error');
    }
  });
}

// ---------------------------------------------------------------------------
// Tab: record (Workflow 4) — hai bước: note bền vững, rồi tin nhắn ký trỏ tới
// ---------------------------------------------------------------------------

{
  const namespace = $('#rec-namespace');
  const type = $('#rec-type');
  const url_ = $('#rec-url');
  const desc = $('#rec-desc');
  const room = $('#rec-room');
  const previewBtn = $('#rec-preview-btn');
  const sendBtn = $('#rec-send-btn');
  const confirmBox = $('#rec-confirm');
  const preview = $('#rec-preview');
  const status = $('#rec-status');
  let lastNoteUrl = null;
  let lastRoomUrl = null;
  let lastNotePath = null;

  previewBtn.addEventListener('click', async () => {
    try {
      const ident = requireIdentity();
      app.validateNamespace(namespace.value.trim());
      const ts = Math.floor(Date.now() / 1000);
      const noteValue = app.singleLineSweep(
        `type:${type.value.trim() || 'guide'} url:${url_.value.trim()} desc:${desc.value.trim()}`
      );
      const notePath = app.recordNotePath(namespace.value.trim(), ts);
      const noteUrl = app.buildRecordNoteSetUrl(el.baseUrl.value.trim(), namespace.value.trim(), ts, noteValue, true);

      const msgText = `Đã publish: ${desc.value.trim()} — chi tiết: ${notePath}`;
      const nonce = Date.now();
      const signed = await app.signSay(ident, room.value.trim() || 'lobby', msgText, nonce);
      const roomUrl = app.buildSaySignedUrl(el.baseUrl.value.trim(), signed);

      lastNoteUrl = noteUrl;
      lastRoomUrl = roomUrl;
      lastNotePath = notePath;

      preview.hidden = false;
      preview.querySelector('.f-note-path').textContent = notePath;
      preview.querySelector('.f-note-value').textContent = noteValue;
      preview.querySelector('.f-note-url').textContent = noteUrl;
      preview.querySelector('.f-msg-text').textContent = signed.text;
      preview.querySelector('.f-room-url').textContent = roomUrl;
      confirmBox.checked = false;
      sendBtn.disabled = true;
      showStatus(status, 'Hai lượt ghi sẽ diễn ra tuần tự: note bền vững, rồi tin nhắn ký trỏ tới.', 'info');
    } catch (e) {
      showStatus(status, e.message, 'error');
    }
  });

  confirmBox.addEventListener('change', () => {
    sendBtn.disabled = !confirmBox.checked || !lastNoteUrl;
  });

  sendBtn.addEventListener('click', async () => {
    try {
      showStatus(status, 'Đang ghi note bền vững...', 'info');
      const r1 = await sendGet(lastNoteUrl);
      if (!r1.ok) {
        showStatus(status, `Ghi note thất bại (HTTP ${r1.status}): ${r1.body.slice(0, 200)}`, 'error');
        return;
      }
      showStatus(status, `Đã ghi note (${lastNotePath}). Đang gửi tin nhắn vào room...`, 'info');
      const r2 = await sendGet(lastRoomUrl);
      showStatus(
        status,
        `Note: OK. Tin nhắn room: HTTP ${r2.status}: ${r2.body.slice(0, 200)}`,
        r2.ok ? 'success' : 'error'
      );
    } catch (e) {
      showStatus(status, `Gửi trực tiếp thất bại (có thể do CORS): ${e.message}.`, 'error');
    }
  });
}

// ---------------------------------------------------------------------------
// Tab: room (claim / allow) — nâng cao
// ---------------------------------------------------------------------------

{
  const claimRoom = $('#room-claim-name');
  const claimPreviewBtn = $('#room-claim-preview-btn');
  const claimSendBtn = $('#room-claim-send-btn');
  const claimConfirm = $('#room-claim-confirm');
  const claimPreview = $('#room-claim-preview');
  const claimStatus = $('#room-claim-status');
  let claimUrl = null;

  claimPreviewBtn.addEventListener('click', async () => {
    try {
      const ident = requireIdentity();
      const name = claimRoom.value.trim();
      const dRoom = name.startsWith('d-') ? name : `d-${name}`;
      const current = await fetchRoomNonce(el.baseUrl.value.trim(), dRoom);
      const nonce = current + 1;
      const signed = await app.signRoomOwnerClaim(ident, name, nonce);
      const url = app.buildRoomOwnerClaimUrl(el.baseUrl.value.trim(), signed);
      claimUrl = url;
      claimPreview.hidden = false;
      claimPreview.querySelector('.f-nonce').textContent = String(nonce);
      claimPreview.querySelector('.f-url').textContent = url;
      claimConfirm.checked = false;
      claimSendBtn.disabled = true;
      showStatus(claimStatus, `Nonce đọc từ server: ${current} → dùng ${nonce}. Chỉ thành công NẾU room chưa có chủ.`, 'info');
    } catch (e) {
      showStatus(claimStatus, e.message, 'error');
    }
  });

  claimConfirm.addEventListener('change', () => {
    claimSendBtn.disabled = !claimConfirm.checked || !claimUrl;
  });

  claimSendBtn.addEventListener('click', async () => {
    try {
      showStatus(claimStatus, 'Đang gửi...', 'info');
      const r = await sendGet(claimUrl);
      showStatus(claimStatus, `HTTP ${r.status}: ${r.body.slice(0, 300)}`, r.ok ? 'success' : 'error');
    } catch (e) {
      showStatus(claimStatus, `Gửi trực tiếp thất bại (có thể do CORS): ${e.message}.`, 'error');
    }
  });

  const allowRoom = $('#room-allow-name');
  const allowDids = $('#room-allow-dids');
  const allowPreviewBtn = $('#room-allow-preview-btn');
  const allowSendBtn = $('#room-allow-send-btn');
  const allowConfirm = $('#room-allow-confirm');
  const allowPreview = $('#room-allow-preview');
  const allowStatus = $('#room-allow-status');
  let allowUrl = null;

  allowPreviewBtn.addEventListener('click', async () => {
    try {
      const ident = requireIdentity();
      const name = allowRoom.value.trim();
      const dRoom = name.startsWith('d-') ? name : `d-${name}`;
      const dids = allowDids.value.split(/\s+/).filter(Boolean);
      if (dids.length === 0) throw new Error('Cần ít nhất một did:key trong allow-list.');
      const current = await fetchRoomNonce(el.baseUrl.value.trim(), dRoom);
      const nonce = current + 1;
      const signed = await app.signRoomAllow(ident, name, nonce, dids);
      const url = app.buildRoomAllowUrl(el.baseUrl.value.trim(), signed);
      allowUrl = url;
      allowPreview.hidden = false;
      allowPreview.querySelector('.f-nonce').textContent = String(nonce);
      allowPreview.querySelector('.f-value').textContent = signed.value;
      allowPreview.querySelector('.f-url').textContent = url;
      allowConfirm.checked = false;
      allowSendBtn.disabled = true;
      showStatus(
        allowStatus,
        `Nonce đọc từ server: ${current} → dùng ${nonce}. Danh sách này THAY THẾ hoàn toàn allow-list cũ. Chỉ chủ sở hữu hiện tại mới ghi được (server trả 403 nếu không phải).`,
        'info'
      );
    } catch (e) {
      showStatus(allowStatus, e.message, 'error');
    }
  });

  allowConfirm.addEventListener('change', () => {
    allowSendBtn.disabled = !allowConfirm.checked || !allowUrl;
  });

  allowSendBtn.addEventListener('click', async () => {
    try {
      showStatus(allowStatus, 'Đang gửi...', 'info');
      const r = await sendGet(allowUrl);
      showStatus(allowStatus, `HTTP ${r.status}: ${r.body.slice(0, 300)}`, r.ok ? 'success' : 'error');
    } catch (e) {
      showStatus(allowStatus, `Gửi trực tiếp thất bại (có thể do CORS): ${e.message}.`, 'error');
    }
  });
}

// ---------------------------------------------------------------------------
// Trạng thái khoá ban đầu
// ---------------------------------------------------------------------------

setLocked(true);
for (const t of el.tabs) t.disabled = true;
