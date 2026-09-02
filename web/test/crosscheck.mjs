import './_env_shim.mjs';
import * as app from '../app.js';

let failed = 0;
function check(name, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  if (g !== w) {
    failed++;
    console.log(`✗ ${name}\n    got : ${g}\n    want: ${w}`);
  } else {
    console.log(`✓ ${name}`);
  }
}

// ---- 1. base58 encode/decode round-trip + fixed vector from Python run ----
{
  const seedB58 = 'CmHDMpfQgx6hiQbYz7wr4PPVahFde3csVHaAM3JqsVSv';
  const wantDid = 'did:key:z6MkvPtGBr5fxPyQ7YY4EEhmxmLvEVStGc8EsaMLgU28GEUY';
  const decoded = app.b58decode(seedB58);
  check('b58decode length', decoded.length, 32);
  const reencoded = app.b58encode(decoded);
  check('b58encode(b58decode(x)) round-trip', reencoded, seedB58);

  const identity = await app.identityFromSeedB58(seedB58);
  check('DID derived from Python-generated seed', identity.did, wantDid);
}

// ---- 2. single_line_sweep ----
{
  check('sweep: plain text unchanged', app.singleLineSweep('hello world'), 'hello world');
  check('sweep: trims edges', app.singleLineSweep('  hi  '), 'hi');
  // \u0000 (Cc), \u200b ZERO WIDTH SPACE (Cf), \u2028 LINE SEPARATOR (Zl)
  check(
    'sweep: control/format/line-sep chars -> space',
    app.singleLineSweep('a\u0000b\u200bc\u2028d'),
    'a b c d'
  );
}

// ---- 3. pyQuote vs known Python urllib.parse.quote(s, safe='') outputs ----
{
  check('pyQuote: unreserved untouched', app.pyQuote('abcXYZ019_.-~'), 'abcXYZ019_.-~');
  check('pyQuote: space -> %20 (not +)', app.pyQuote('a b'), 'a%20b');
  check('pyQuote: slash encoded (safe empty)', app.pyQuote('a/b'), 'a%2Fb');
  // Vietnamese: "Đã" -> UTF-8 bytes 0xC4 0x90 0xC3 0xA3
  check('pyQuote: UTF-8 multibyte uppercase hex', app.pyQuote('Đã'), '%C4%90%C3%A3');
}

// ---- 4. did:key derivation known vector (from Python public_key_to_did in a
//         prior interactive check) ----
{
  // 32-byte all-zero-ish deterministic pubkey isn't realistic (ed25519 pubkeys
  // aren't arbitrary), so instead we derive both sides from the SAME seed and
  // compare — this is the meaningful cross-check, done above in section 1.
  console.log('(did:key vector cross-checked via seed in section 1)');
}

// ---- 5. record note path / URL building ----
{
  const url = app.buildRecordNoteSetUrl(
    'http://127.0.0.1:9999',
    'nguyenvana',
    1788316948,
    'type:guide url:https://example.com desc:Hướng dẫn',
    true
  );
  check(
    'buildRecordNoteSetUrl',
    url,
    'http://127.0.0.1:9999/kv/nguyenvana/log-1788316948/set/type%3Aguide%20url%3Ahttps%3A%2F%2Fexample.com%20desc%3AH%C6%B0%E1%BB%9Bng%20d%E1%BA%ABn?if_absent=1'
  );
  check('recordNotePath', app.recordNotePath('nguyenvana', 1788316948), '/kv/nguyenvana/log-1788316948');
}

// ---- 6. say-signed sign + URL build (full pipeline, self-consistent: verify
//         signature validates against the derived public key) ----
{
  const seedB58 = 'CmHDMpfQgx6hiQbYz7wr4PPVahFde3csVHaAM3JqsVSv';
  const identity = await app.identityFromSeedB58(seedB58);
  const signed = await app.signSay(identity, 'lobby', 'Chào từ web signer', 12345);
  check('signSay canonical text swept (no leading/trailing ws)', signed.text, 'Chào từ web signer');
  check('signSay did', signed.did, identity.did);
  const url = app.buildSaySignedUrl('http://127.0.0.1:9999', signed);
  const expectedPrefix = 'http://127.0.0.1:9999/r/lobby/say-signed/';
  check('buildSaySignedUrl prefix', url.startsWith(expectedPrefix), true);

  // Verify signature cryptographically (independent of any Python output —
  // this proves signSay() produces a VALID Ed25519 signature over the exact
  // canonical string the server checks).
  const ed = await import('../vendor/noble-ed25519.js');
  const pub = await ed.getPublicKey(identity.seed);
  const canonical = new TextEncoder().encode(`lobby|12345|Chào từ web signer`);
  let b64 = signed.sig.replace(/-/g, '+').replace(/_/g, '/');
  while (b64.length % 4 !== 0) b64 += '=';
  const sigBytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const valid = await ed.verify(sigBytes, canonical, pub);
  check('signSay signature verifies against derived pubkey', valid, true);
}

console.log('');
if (failed > 0) {
  console.log(`FAILED: ${failed} check(s)`);
  process.exit(1);
} else {
  console.log('Tất cả cross-check PASS.');
}
