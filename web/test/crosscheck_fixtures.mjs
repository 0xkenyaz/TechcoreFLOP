import './_env_shim.mjs';
import { readFileSync } from 'node:fs';
import * as app from '../app.js';

const fx = JSON.parse(readFileSync(new URL('./py_fixtures.json', import.meta.url)));

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

const identity = await app.identityFromSeedB58(fx.seed_b58);
check('DID from Python seed', identity.did, fx.did);

const [shard, key] = await app.fingerprintShardPath(fx.did);
check('fingerprint shard', shard, fx.shard);
check('fingerprint key', key, fx.key);

// say-signed — SAME nonce/room/text as Python fixture must give the SAME
// signature (Ed25519 is deterministic: same key+message -> same sig).
{
  const signed = await app.signSay(identity, fx.signed_say.room, 'Xin chào  \u200bthế giới', fx.signed_say.nonce);
  check('signSay text (swept)', signed.text, fx.signed_say.text);
  check('signSay sig == Python sig (deterministic Ed25519)', signed.sig, fx.signed_say.sig);
  const url = app.buildSaySignedUrl('http://X', signed);
  check('say_url == Python say_url', url, fx.say_url);
}

// room-owner-claim
{
  const signed = await app.signRoomOwnerClaim(identity, 'phong-thu-nghiem', fx.signed_claim.claim_nonce);
  check('claim sig == Python sig', signed.sig, fx.signed_claim.sig);
  const url = app.buildRoomOwnerClaimUrl('http://X', signed);
  check('claim_url == Python claim_url', url, fx.claim_url);
}

// room-allow
{
  const dids = fx.signed_allow.value.split(' ');
  const signed = await app.signRoomAllow(identity, 'phong-thu-nghiem', fx.signed_allow.nonce, dids);
  check('allow sig == Python sig', signed.sig, fx.signed_allow.sig);
  const url = app.buildRoomAllowUrl('http://X', signed);
  check('allow_url == Python allow_url', url, fx.allow_url);
}

// DID note (unsigned, just URL building)
{
  const value = app.singleLineSweep(`${fx.did} nick:tester lang:vi`);
  const url = app.buildDidNoteSetUrl('http://X', fx.shard, fx.key, value, true);
  check('did_note_url == Python did_note_url', url, fx.did_note_url);
}

// record note (unsigned, just URL building)
{
  const value = app.singleLineSweep('type:guide url:https://a.b desc:mô tả');
  const url = app.buildRecordNoteSetUrl('http://X', 'tester-ns', 1700000000, value, true);
  check('rec_note_url == Python rec_note_url', url, fx.rec_note_url);
}

console.log('');
if (failed > 0) {
  console.log(`FAILED: ${failed} check(s)`);
  process.exit(1);
} else {
  console.log('Tất cả cross-check với fixture Python thật PASS (chữ ký + URL byte-for-byte).');
}
