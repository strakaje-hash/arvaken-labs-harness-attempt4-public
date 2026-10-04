# Signing keys

The cloak-bundle path is the one channel through which we can change what runs on every user's machine.
Its trust model is deliberately small.

```
root key (offline / hardware)
   └─ signs ─▶ bundle-signing key certificate  (purpose: cloak-bundle, 90-day validity)
                    └─ signs ─▶ bundle manifest (bundle.json; issued_at is stamped at signing)
   └─ signs ─▶ revocation list (keys/revocations.json; carries its own not_after, ~90 days)
```

Clients pin **only the root public key** (`packages/bundles/keys/root.pub`) at build time.

## Two semantics that are design decisions

**Certificate validity is evaluated once, at acceptance.** When a client first verifies a bundle it records
the acceptance instant. The certificate must be valid at that instant (5-minute clock skew tolerated) and the
manifest's signed `issued_at` must fall inside the key's validity window. From then on the bundle is judged at
its acceptance instant, never re-checked against the wall clock: a laptop offline for 91 days keeps its cloak.
Shipped bundles count as accepted at build time (the build is the trust act). Only *new* acceptances see
`cert-expired`; the fix for those is a re-signed bundle from the registry.

**Revocation freshness is last-known-good with a bounded staleness window.** The root-signed revocation list
carries `not_after` (90 days from signing; renewed in the same root session as every key issuance). Policy:

| Situation | Already-accepted bundle | Accepting a new bundle |
| --- | --- | --- |
| newest known list within `not_after` + 7 days | works | allowed |
| list older than that, registry unreachable | works | refused (`revocations-stale`) until the registry is reachable |
| no root-signed list known at all | works | refused |
| a valid list revokes the bundle's key | refused | refused |

Revocation is **sticky**: a key seen revoked in any valid list stays revoked on that client, so a later list
that omits it (by mistake or by an attacker holding only the bundle key) cannot roll the revocation back.
The failure mode this chooses: an offline user is never bricked, and a revoked key is only ever missed by
clients that have not synced since the revocation, which is the same set no other policy could reach either.
The cost: a root session at least every 90 days, or the registry and every client refuse new bundles. The
API logs an error when its list is stale and a warning 14 days before.

## What each event costs

| Event | Action | Client impact |
| --- | --- | --- |
| Bundle key expires (every 90 days) | `pnpm issue-key --days 90` (also renews the revocation list), re-sign the current bundle, deploy | none |
| Bundle key lost | issue a new key, re-sign, deploy | none |
| Bundle key leaked | `pnpm revoke <key_id> "<reason>"`, issue a new key, re-sign, deploy | clients refuse the leaked key on their next check, permanently |
| Revocation list about to expire, no key event due | `pnpm refresh-revocations` in a root session, deploy | none |
| Root key lost | `pnpm ceremony-root --force`; rebuild and redistribute every client | every user must update the app |
| Root key leaked | same as lost, and treat every bundle issued since the leak as hostile | same |

The root therefore does three things only: issue certificates, sign revocation lists, and get renewed on a
90-day cadence. It must not sit on a developer laptop once a single external user exists: root compromise at
that point means a reinstall for everyone.

## Root ceremony

1. Two people present. Run `pnpm --filter @mark/bundles ceremony-root` on a machine that is offline or freshly imaged. It writes the root pair and the first (empty) revocation list.
2. Write the private key to two offline media (paper and an encrypted USB), stored in two separate places.
3. Import the private key into the root signer you will use: a hardware token or a KMS with a **non-exportable** key.
   Delete `keys/root.key`. From then on `MARK_ROOT_SIGNER=cmd:<command>` points at that signer.
4. Commit `keys/root.pub` and `keys/revocations.json`. The root id (`keyId`, first 16 hex of SHA-256) is printed by the ceremony and appears in every certificate.
5. Issue the first bundle key: `pnpm issue-key --days 90`. Put the next root session (≤ 90 days out) in a calendar that two people see.

Status: the repository currently carries a **development root** generated on a laptop with `keys/root.key`
still on disk. This is acceptable with zero external users and must be replaced by a real ceremony before
the first one.

## External signers (KMS or hardware)

A signer is any command that reads the message bytes on stdin and prints the **raw 64-byte Ed25519 signature as
hex** on stdout. Set `MARK_ROOT_SIGNER` / `MARK_BUNDLE_SIGNER` to `cmd:<command>`. `issue-key --public <hex>`
certifies a key whose private half never touches this repo.

Google Cloud KMS supports Ed25519 with non-exportable keys (`EC_SIGN_ED25519`). A wrapper looks like:

```bash
#!/usr/bin/env bash
# kms-sign.sh: stdin = message, stdout = raw signature hex
set -euo pipefail
tmp=$(mktemp); sig=$(mktemp)
cat > "$tmp"
gcloud kms asymmetric-sign --project "$PROJECT" --location "$LOCATION" --keyring mark --key bundle-signing --version "$VERSION" \
  --input-file "$tmp" --signature-file "$sig" >/dev/null
xxd -p "$sig" | tr -d '\n'
rm -f "$tmp" "$sig"
```

Then `MARK_BUNDLE_SIGNER="cmd:./kms-sign.sh"`. `signManifest` refuses a signer whose output does not verify
under the certified public key, so a miswired signer fails loudly instead of producing an unverifiable bundle.

YubiKey: an OpenPGP Ed25519 key produces OpenPGP signature packets, not raw signatures, so a wrapper has to
unwrap the packet (or use PIV/FIDO tooling that exposes raw signing). Not provided here; the contract above is
all the wrapper has to satisfy. AWS KMS does not offer Ed25519 as of this writing.

## Files

| Path | Committed | Purpose |
| --- | --- | --- |
| `keys/root.pub` | yes | the trust anchor every client pins |
| `keys/root.key` | never | ceremony output; moved offline then deleted |
| `keys/certs/<key_id>.json` | yes | root-signed bundle-key certificates |
| `keys/active-key` | yes | which certified key `pnpm sign` uses |
| `keys/bundle-<key_id>.key` | never | development bundle keys |
| `keys/revocations.json` | yes | root-signed revocation list with `not_after`, served by the API at `/v1/keys` |
