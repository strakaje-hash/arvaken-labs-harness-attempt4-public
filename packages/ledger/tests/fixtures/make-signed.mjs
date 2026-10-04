// Produces ts-signed.json: a throwaway root, a certificate and a signed object made by packages/core, which the
// Python ledger must verify (tests/test_keys.py). Deterministic keys (fixed seeds) so a regeneration is a no-op diff.
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { canonicalJson, fileSigner, issueKeyCert, keyId, KEY_CERT_SCHEMA, publicKeyOf, REVOCATIONS_SCHEMA, signRevocations, signObject } from '../../../core/dist/index.js';

const here = dirname(fileURLToPath(import.meta.url));
const rootPriv = '11'.repeat(32);
const keyPriv = '22'.repeat(32);
const rootPub = publicKeyOf(rootPriv);
const keyPub = publicKeyOf(keyPriv);
const cert = { schema: KEY_CERT_SCHEMA, key_id: keyId(keyPub), public_key: keyPub, purpose: 'gate', not_before: '2026-09-01T00:00:00Z', not_after: '2026-12-01T00:00:00Z', root_id: keyId(rootPub), notes: 'test fixture, not a real key' };
const signedCert = await issueKeyCert(cert, fileSigner(rootPriv));
const revocations = await signRevocations({ schema: REVOCATIONS_SCHEMA, issued_at: '2026-09-01T00:00:00Z', not_after: '2026-12-01T00:00:00Z', root_id: keyId(rootPub), revoked: [] }, fileSigner(rootPriv));
const object = { issued_at: '2026-09-10T00:00:00Z', gate: 'ks.latency', thresholds: { max_ms: 250.5, min_replications: 20 }, labels: ['pass', 'fail'], note: 'unicode é中 😀 "quoted"' };
const signed = await signObject(object, signedCert, fileSigner(keyPriv));
const out = { root_public_key: rootPub, root_id: keyId(rootPub), purpose: 'gate', at: '2026-09-10T00:00:00Z', revocations, signed: { schema: 'mark.signed-object/1', ...signed }, canonical: canonicalJson(object) };
writeFileSync(join(here, 'ts-signed.json'), JSON.stringify(out, null, 1) + '\n');
console.log('wrote ts-signed.json', keyId(rootPub));
