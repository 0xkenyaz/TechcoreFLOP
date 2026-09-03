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

// Mock fetch phân biệt theo URL: request /r/<room>?...format=json (dùng để
// tra lại #seq thật sau khi gửi) trả về JSON room giả lập chứa đúng message
// vừa "gửi" — cấu hình qua window.__mockShareState do test set trước khi
// bấm nút Gửi. Mọi request khác (say-signed, publish...) trả 'ok (mock)'
// như cũ.
window.__fetchCalls = [];
window.fetch = async (url) => {
  const urlStr = String(url);
  window.__fetchCalls.push(urlStr);
  window.__lastFetchUrl = urlStr;
  if (urlStr.includes('/r/') && urlStr.includes('format=json') && window.__mockShareState) {
    const { did, nonce, room } = window.__mockShareState;
    return {
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          messages: [{ from: did, nonce, seq: 1072034, room, text: 'mock', ts: 0 }],
        }),
    };
  }
  return {
    ok: true,
    status: 200,
    text: async () => 'ok (mock)',
  };
};

// Mock window.open để bắt tham số bấm nút "Chia sẻ lên X" mà không mở tab thật.
window.__openCalls = [];
window.open = (url, target, features) => {
  window.__openCalls.push({ url: String(url), target, features });
  return null;
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
const sayTextEl = window.document.getElementById('say-text');
check('Ô "Nội dung tin nhắn" được điền sẵn gợi ý khi trang tải', sayTextEl.value.length > 0);

const prefilled = sayTextEl.value;
window.document.getElementById('say-random-btn').dispatchEvent(new window.Event('click'));
check('Nút "🎲 Gợi ý khác" đổi được nội dung (hoặc trùng ngẫu nhiên, không bắt buộc khác)', sayTextEl.value.length > 0);
void prefilled; // (chấp nhận trùng do random — chỉ cần không rỗng và không lỗi)

window.document.getElementById('say-room').value = 'technocore';
window.document.getElementById('say-text').value = 'Xin chào từ smoke test'; // ghi đè gợi ý — người dùng tự sửa được, đúng như thiết kế
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

// Cấu hình mock: lấy đúng nonce vừa ký từ canonical string hiện trên preview,
// để mock /r/<room>?format=json trả về message khớp (did, nonce) thật.
const canonicalText = sayPreview.querySelector('.f-canonical').textContent;
const nonceFromCanonical = canonicalText.split('|')[1];
window.__mockShareState = {
  did: 'did:key:z6MkvPtGBr5fxPyQ7YY4EEhmxmLvEVStGc8EsaMLgU28GEUY',
  nonce: nonceFromCanonical,
  room: 'technocore',
};

const shareXBtn = window.document.getElementById('share-x-btn');
check('Nút "Chia sẻ lên X" khoá trước khi gửi tin nhắn nào', shareXBtn.disabled === true);

sendBtn.dispatchEvent(new window.Event('click'));
await new Promise((r) => setTimeout(r, 50));
check(
  'fetch (mock) được gọi với URL say-signed',
  window.__fetchCalls.some((u) => u.includes('/say-signed/'))
);
check(
  'fetch (mock) được gọi để tra lại #seq (format=json)',
  window.__fetchCalls.some((u) => u.includes('format=json'))
);
check('Nút "Chia sẻ lên X" mở khoá sau khi tra được #seq thật', shareXBtn.disabled === false);

shareXBtn.dispatchEvent(new window.Event('click'));
check('Bấm "Chia sẻ lên X" gọi window.open đúng 1 lần', window.__openCalls.length === 1);
const openedUrl = window.__openCalls[0]?.url || '';
check('URL mở ra là X/Twitter intent tweet', openedUrl.startsWith('https://twitter.com/intent/tweet?text='));
const shareText = decodeURIComponent(openedUrl.split('text=')[1] || '');
check('Nội dung chia sẻ có đúng #seq tra được', shareText.includes('#1072034'));
check('Nội dung chia sẻ có đúng DID', shareText.includes('did:key:z6MkvPtGBr5fxPyQ7YY4EEhmxmLvEVStGc8EsaMLgU28GEUY'));
check('Nội dung chia sẻ nhắc @flop_labs', shareText.includes('@flop_labs'));
check('Nội dung chia sẻ phản ánh đúng room thật đã ký (technocore)', shareText.includes('the technocore room as #'));

// --- Test 4: forget xoá sạch state ---
window.document.getElementById('forget-btn').dispatchEvent(new window.Event('click'));
await new Promise((r) => setTimeout(r, 20));
check('DID reset về placeholder sau Quên khoá', didOut.textContent === '(chưa có identity)');
check('Seed input bị xoá sau Quên khoá', seedInput.value === '');
check('Tab bị khoá lại sau Quên khoá', Array.from(tabs).every((t) => t.disabled));
check('Nút "Chia sẻ lên X" khoá lại sau Quên khoá (không còn liên quan identity cũ)', shareXBtn.disabled === true);

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
