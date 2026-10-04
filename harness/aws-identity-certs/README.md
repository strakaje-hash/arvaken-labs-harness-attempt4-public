# AWS regional public certificates for instance identity documents

One `<region>.rsa.pem` per region: the **RSA** certificate AWS publishes for verifying the base64-encoded signature at
`/latest/dynamic/instance-identity/signature` (the DSA/PKCS7 and RSA-2048/CMS variants are not vendored; the harness
verifies the base64 signature in-process with `cryptography`, PKCS#1 v1.5, SHA-256 then SHA-1, and records which digest
verified). `mark_platform.operator` reads the certificate for the region the document names; a region with no file here is
`unresolved: no_vendored_certificate_for_region`.

**Source.** https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/regions-certs.html, fetched 2026-09-21
(page sha256 `79244e4e931483fafec4654e868e8e74d10585024e4bfc3ee48197fa24503ce1`), parsed by region section (`id="<region>-cert"`) and certificate tab ("RSA").
36 regions.

**Refresh procedure (founder ruling 2026-09-21: "by a recorded procedure with a diff, same as the pin lists").**
1. Fetch the page again to a scratch file; record its sha256 here.
2. Re-run the parser (the script is in benchmarks/attempt4-freeze-notes.md, C2 entry) into a scratch directory.
3. `diff -r` the scratch directory against this one; every changed or added certificate is listed in the commit message
   with its region and the certificate subject/validity from `openssl x509 -noout -subject -dates`.
4. Regenerate `SHA256SUMS`; `sha256sum -c SHA256SUMS` must pass; `test_operator.py` reads the file for the region it fakes.
5. Commit the certificates, `SHA256SUMS` and this README in one commit.

**Integrity.** `SHA256SUMS` beside the files; the manifest pins the declaration of permitted calls, and each operator fact
records the sha256 of the certificate it verified against, so a bundle names the exact certificate a reader must check.
