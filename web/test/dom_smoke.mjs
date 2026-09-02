// Smoke test CHỈ để dev — không phải code sản phẩm, không được index.html
// import. Tải index.html thật vào jsdom, chạy ui.js thật (module thật, có
// fetch bị mock), bấm nút thật, và assert DOM thay đổi đúng như kỳ vọng.
// Mục tiêu: bắt lỗi runtime (ReferenceError, DOM selector sai, event chưa
// gắn...) mà `node --check` (chỉ kiểm tra cú pháp) không thấy được.
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, '..');
const html = readFileSync(path.join(webDir, 'index.html'), 'utf-8');

let failed = 0;
function check(name, cond) {
  if (!cond) {
    failed++;
    console.log(`✗ ${name}`);
  } else {
    console.log(`✓ ${name}`);
  }
}

const dom = new JSDOM(html, {
  url: pathToFileURL(path.join(webDir, 'index.html')).href,
  runScripts: 'dangerously',
  resources: 'usable',
  pretendToBeVisual: true,
});

const { window } = dom;

// jsdom có window.crypto.getRandomValues() sẵn nhưng THIẾU crypto.subtle
// (chỉ property getter, không gán đè được cả object) — bổ sung .subtle từ
// Node's webcrypto thật, đúng những gì browser thật có sẵn. btoa/atob jsdom
// đã có sẵn, không cần patch.
window.crypto.subtle = globalThis.crypto.subtle;
window.fetch = async (url) => {
  window.__lastFetchUrl = String(url);
  return {
    ok: true,
    status: 200,
    text: async () => 'ok (mock)',
  };
};

// jsdom chưa hỗ trợ import ES module qua <script type="module"> khi
// runScripts qua constructor cho local file:// theo cách đáng tin cậy giữa
// các phiên bản — thay vì phụ thuộc vào việc đó, import trực tiếp module
// thật (ui.js) vào NGỮ CẢNH cửa sổ jsdom bằng cách gán các global cần thiết
// rồi import động, đúng cách Node module resolution hoạt động (khác cách
// trình duyệt resolve <script type="module">, nhưng chạy CÙNG MỘT source
// file thật, không phải bản giả lập).
globalThis.window = window;
globalThis.document = window.document;
globalThis.self = window;
globalThis.fetch = window.fetch;
globalThis.CustomEvent = window.CustomEvent;

await import(pathToFileURL(path.join(webDir, 'ui.js')).href);

// --- Test 1: trạng thái ban đầu phải bị khoá ---
const tabs = window.document.querySelectorAll('.tab-btn');
check('Tab bị disable khi chưa có identity', Array.from(tabs).every((t) => t.disabled));
check('#panels có class is-locked lúc khởi động', window.document.getElementById('panels').classList.contains('is-locked'));

// --- Test 2: derive DID từ seed hợp lệ (seed cố định, DID đã biết từ Python) ---
const seedInput = window.document.getElementById('seed-input');
const deriveBtn = window.document.getElementById('derive-btn');
seedInput.value = 'CmHDMpfQgx6hiQbYz7wr4PPVahFde3csVHaAM3JqsVSv';
deriveBtn.dispatchEvent(new window.Event('click'));
// click handler là async — đợi một tick microtask cho promise resolve
await new Promise((r) => setTimeout(r, 50));

const didOut = window.document.getElementById('did-out');
check(
  'DID hiển thị đúng sau khi derive',
  didOut.textContent === 'did:key:z6MkvPtGBr5fxPyQ7YY4EEhmxmLvEVStGc8EsaMLgU28GEUY'
);
check('Tab được mở khoá sau khi derive', !Array.from(tabs).some((t) => t.disabled));
check('#panels không còn is-locked', !window.document.getElementById('panels').classList.contains('is-locked'));

// --- Test 3: tab "Tin nhắn" — ký & xem trước ---
window.document.getElementById('say-room').value = 'lobby';
window.document.getElementById('say-text').value = 'Xin chào từ smoke test';
window.document.getElementById('say-preview-btn').dispatchEvent(new window.Event('click'));
await new Promise((r) => setTimeout(r, 50));

const sayPreview = window.document.getElementById('say-preview');
check('Preview say hiện ra sau khi ký', sayPreview.hidden === false);
const sig = sayPreview.querySelector('.f-sig').textContent;
check('Có chữ ký base64url không rỗng', typeof sig === 'string' && sig.length > 20);
const sendBtn = window.document.getElementById('say-send-btn');
check('Nút Gửi vẫn disabled khi chưa tick xác nhận', sendBtn.disabled === true);

const confirmBox = window.document.getElementById('say-confirm');
confirmBox.checked = true;
confirmBox.dispatchEvent(new window.Event('change'));
check('Nút Gửi được mở khoá sau khi tick xác nhận', sendBtn.disabled === false);

sendBtn.dispatchEvent(new window.Event('click'));
await new Promise((r) => setTimeout(r, 50));
check('fetch (mock) được gọi với URL say-signed', String(window.__lastFetchUrl || '').includes('/say-signed/'));

// --- Test 4: forget xoá sạch state ---
window.document.getElementById('forget-btn').dispatchEvent(new window.Event('click'));
await new Promise((r) => setTimeout(r, 20));
check('DID reset về placeholder sau Quên khoá', didOut.textContent === '(chưa có identity)');
check('Seed input bị xoá sau Quên khoá', seedInput.value === '');
check('Tab bị khoá lại sau Quên khoá', Array.from(tabs).every((t) => t.disabled));

// --- Test 5: "Tạo seed mới" — luồng cho người chưa từng dùng CLI ---
const newSeedBtn = window.document.getElementById('new-seed-btn');
const newSeedModal = window.document.getElementById('new-seed-modal');
const newSeedOut = window.document.getElementById('new-seed-out');
const newSeedConfirm = window.document.getElementById('new-seed-confirm');
const newSeedUseBtn = window.document.getElementById('new-seed-use-btn');

newSeedBtn.dispatchEvent(new window.Event('click'));
check('Modal tạo seed mới hiện ra', newSeedModal.hidden === false);
check('Seed mới được điền sẵn vào ô hiển thị', newSeedOut.value.length > 0);
check('Nút "Dùng seed này ngay" vẫn khoá khi chưa tick xác nhận', newSeedUseBtn.disabled === true);

const generatedSeed = newSeedOut.value;
newSeedConfirm.checked = true;
newSeedConfirm.dispatchEvent(new window.Event('change'));
check('Nút "Dùng seed này ngay" mở khoá sau khi tick xác nhận', newSeedUseBtn.disabled === false);

newSeedUseBtn.dispatchEvent(new window.Event('click'));
await new Promise((r) => setTimeout(r, 50));
check('Modal đóng lại sau khi dùng seed', newSeedModal.hidden === true);
check('Ô Seed chính được điền đúng seed vừa tạo', seedInput.value === generatedSeed);
check('DID được derive thành công từ seed mới tạo', didOut.textContent.startsWith('did:key:z'));
check('Tab được mở khoá sau khi dùng seed mới', !Array.from(tabs).some((t) => t.disabled));

console.log('');
if (failed > 0) {
  console.log(`FAILED: ${failed} check(s)`);
  process.exit(1);
} else {
  console.log('DOM smoke test: tất cả PASS.');
}
