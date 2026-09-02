// Giả lập tối thiểu môi trường trình duyệt cho Node CHỈ để chạy test —
// KHÔNG dùng file này ở production, web/index.html không import nó.
// `self` không tồn tại global trong Node; noble-ed25519.js kiểm tra
// `typeof self === 'object' && 'crypto' in self` để chọn Web Crypto API,
// vốn Node >=19 đã có sẵn dưới `globalThis.crypto`.
globalThis.self = globalThis;
