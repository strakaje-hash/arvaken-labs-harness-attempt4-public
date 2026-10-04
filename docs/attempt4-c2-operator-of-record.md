# C2 — Operator of record: design for approval

**Status:** approved 2026-09-21 (the three open questions ruled, benchmarks/attempt4-freeze-notes.md) and built the same day; §9 says what
exists and where it differs from the design above.

## 1. The rule this implements

> The instrument states where it ran from the provider's own identity, never from its configuration.

Two records of one fact from two independent sources (platform decision D33): the platform records where it *deployed* the
runner (`runner_deployment`, Phase 5 — it did the deploying); the instrument stamps where the measurement *executed*, from
evidence the environment's owner gave it when asked. The two are compared literally; a mismatch is an integrity finding; an
`unresolved` stamp is never matched.

## 2. What the manifest carries

A top-level block in the signed manifest, beside `account` (A5), schema `mark.run-operator/1`:

```json
"operator": {
  "schema": "mark.run-operator/1",
  "tenant": "aws:123456789012",
  "kind": "aws",
  "resolved": true,
  "reason": null,
  "asked_at": {"iso": "2026-10-02T09:14:03.118Z", "mono_ns": 1234567890},
  "before_first_cell": true,
  "facts": [
    {"name": "account_id", "value": "123456789012",
     "source": {"call": "aws.imds.identity_document", "endpoint": "http://169.254.169.254/latest/dynamic/instance-identity/document", "read_at": "…"},
     "verified": {"by": "aws.imds.identity_signature", "against": "aws-regional-cert:eu-west-1:sha256:…", "ok": true}},
    {"name": "region",      "value": "eu-west-1",  "source": {"call": "aws.imds.identity_document", "…": "…"}},
    {"name": "instance_id", "value": "i-0abc…",     "source": {"call": "aws.imds.identity_document", "…": "…"}},
    {"name": "caller_arn",  "value": "arn:aws:sts::123456789012:assumed-role/…", "source": {"call": "aws.sts.get_caller_identity", "…": "…"}, "corroborates": "account_id"}
  ],
  "permitted_calls": {"declaration_sha256": "…", "made": ["aws.imds.token", "aws.imds.identity_document", "aws.imds.identity_signature", "aws.sts.get_caller_identity"]}
}
```

`tenant` is the literal the platform's `runner_deployment.tenant` carries, so the comparison is string equality:

| where the run executed | `tenant` | `kind` | facts, each with its source |
| --- | --- | --- | --- |
| an AWS instance | `aws:<account_id>` | `aws` | account_id, region, instance_id from the IMDSv2 instance-identity document, its signature verified; caller_arn from STS `GetCallerIdentity` when instance-role credentials exist (corroborates account_id) |
| a rented pod, Lab run | `lab:arvaken` | `pod` | provider, pod id from the provider's own metadata (§6, open); the Lab attribution is the bench spec's study set, stated as such |
| a laptop | `local:<hostname_hash12>` | `laptop` | hostname hash from the OS, and nothing else |
| provider detected, identity not obtainable | `unresolved` | the detected kind | `reason` says which call failed and how; the facts obtained so far stay, marked unverified |

Never a source of any fact: `AWS_ACCOUNT_ID`, `AWS_REGION`, `AWS_DEFAULT_REGION`, `RUNPOD_*`, `~/.aws/config`, the bench spec,
the registry. Those are what the harness was told. A test sets every one of them and proves none reaches the record (§7).

## 3. When, and how it is bound

- **At run open, before any cell.** `open_run` resolves the operator before the mock world, the proxies and the first
  calibration replication start; `before_first_cell` is not a flag someone sets, it is the position of the call in
  `open_run`, and the `run_open` ledger record — the first record of the chain — carries the whole block. A run that
  started somewhere cannot finish claiming somewhere else because the claim is chained before anything else happened.
- **At close**, the manifest copies the block from the context; **at `run sign`** the block is compared to the `run_open`
  record's, field by field, and a difference refuses the signature — the same shape as A5's account check, placed beside it.
- **Re-decision never recomputes it.** It is a fact about the run's start; it cannot be asked again.

## 4. The instrument's own manifest of permitted calls

The harness makes outbound calls of its own — today the vLLM `/metrics` sampler and the OTLP exporter, plus the run's own
components (mock world, proxies) on loopback. None is declared anywhere. C2 must not add a fourth undeclared one.

- `harness/permitted-calls.json`, checked in, hashed, pinned in the manifest (`pins.permitted_calls_sha256`). One entry per
  call the harness may make: `id`, `purpose`, `method`, `host`, `path` (pattern), `read_only: true`, `credentials`
  (`none` | `instance_role`), `records` (the fields read into the record), `since` (the attempt that added it).
- `mark_platform.permitted_calls.call(id, …)` is the only way the harness issues such a request. An `id` not in the
  declaration raises `CallNotPermitted` before a socket opens; a declaration entry that is not `read_only` is refused at load
  (v1 of the declaration cannot express a write); every call made is appended to `harness-calls.jsonl` with its stamp, status
  and the sha256 of the response, and the manifest counts them under `operator.permitted_calls.made`.
- The declaration lists the identity calls (`aws.imds.token` PUT — IMDSv2's token request is the one non-GET, read-only by
  AWS's definition; `aws.imds.identity_document`; `aws.imds.identity_signature`; `aws.sts.get_caller_identity`; the pod
  provider's call once §6 is ruled) **and the two calls the harness already makes** (`vllm.metrics`, `otlp.export`), so the
  declaration is an inventory, not a fiction. Migrating `serving.py`'s sampler under `call()` is in scope; the OTLP exporter
  is a library the gate can declare but not route, and the declaration says so in its entry.
- Not on the declaration and never added: anything with a body the provider stores, anything authenticated with a secret
  the harness holds in a file. `GetCallerIdentity` is signed with the instance role's temporary credentials from IMDS (SigV4
  written in-house, ~60 lines, no boto3), which is the provider handing the instrument its own identity, not a secret the
  harness was configured with.

## 5. Detection order, and why a container on AWS never reads "laptop"

1. **Provider from the kernel, not from a call.** `/sys/class/dmi/id/sys_vendor` (`Amazon EC2`) and `board_asset_tag`
   (`i-…`) are the hypervisor's statement exposed by the kernel. If DMI says EC2, the run is on AWS whatever else happens.
2. **AWS:** IMDSv2 token (PUT, TTL 60 s) → instance-identity document and its RSA-SHA256 signature → verified against the
   AWS regional public certificate vendored under `harness/aws-identity-certs/<region>.pem` (public, published by AWS; the
   vendored set is hashed and pinned) → `account_id`, `region`, `instance_id`. Then, if IMDS serves role credentials, STS
   `GetCallerIdentity` → `caller_arn` and a second `account_id`; disagreement between the two is `unresolved` with both
   values in the reason. IMDS unreachable on a host DMI calls EC2 (hop limit 1 inside a container is the usual cause) is
   `unresolved: imds_unreachable`, never `local:`.
3. **Pod:** the provider's own metadata (§6). Detected by the provider-written file or endpoint the ruling names.
4. **Laptop:** neither detected → `local:<hostname_hash12>`; `facts` carries the hostname hash and its source (`platform.node()`).
5. Every step has a bounded timeout (2 s per call, 10 s total); a timeout is a reason, not a hang.

## 6. Open for the founder's ruling

- **Pod identity on Runpod.** Runpod exposes no metadata endpoint I can find. What the provider writes is
  `/etc/rp_environment` (root-owned, written at container start, the source the harness's envscrub deliberately drops) and
  the `RUNPOD_*` variables derived from it; the GraphQL API's `myself { pods { id } }` needs the API key the pod deliberately
  scrubs (freeze notes, attempt 3). Options: (a) read `/etc/rp_environment` as the provider's file — provider-written, but
  env-shaped, and writable by root in the container; (b) treat Runpod as identity-unresolvable and stamp Lab runs
  `lab:arvaken` with `kind: pod`, `facts` = the provider file's pod id marked `unverified`, `resolved: true` only for the Lab
  attribution; (c) ask Runpod. My recommendation is (b) with the file's contents recorded and marked, because (a) would
  make a root-writable file "the provider's identity", which is the configuration the rule excludes.
- **Vendoring AWS's regional public certificates** so the identity document verifies offline. Phase 5 already vendors AWS
  authorization data; this is the same shape. The alternative — trusting TLS to STS alone — verifies the channel, not the
  document; I recommend vendoring.
- **`lab:arvaken` is an attribution, not a provider fact.** The bench spec's study set says the run is the Lab's; the pod
  facts beside it are the provider's. The record states the two apart (`tenant` vs `facts`); confirm that is the intended
  reading, since a Lab run on AWS would then read `aws:<account>` with `study_set: labs` beside it, not `lab:arvaken`.

## 7. Tests, written from the failures

- **R10, configuration is not identity:** `AWS_ACCOUNT_ID`, `AWS_REGION`, `RUNPOD_POD_ID` set, no IMDS, no DMI → `local:`;
  DMI says EC2, no IMDS → `unresolved: imds_unreachable`, and the env's account id appears nowhere in the record.
- **The fake provider (R9):** a local IMDSv2 server the code reaches through the endpoint it reads (`MARK_IMDS_BASE`, test
  only, refused off the laptop) serving a document signed by a test key whose certificate the test vendors; the record
  carries the three facts with their sources and `verified.ok`; a document whose signature does not verify → `unresolved:
  identity_document_unverified`; STS and IMDS disagreeing → `unresolved` with both values.
- **Permitted calls:** an id not on the declaration is refused before any socket (a listener that must never see a
  connection proves it); a declaration entry with `read_only: false` refuses the load; every call made appears in
  `harness-calls.jsonl` and in the manifest count.
- **Binding:** the `run_open` record carries the block; a manifest whose `operator` is edited while the ledger is untouched is
  refused at `run sign` with the field named; redecide leaves it byte-identical.
- **Positive control:** the laptop pipeline stamps `local:<hash>` on every bundle from the commit that lands this.

## 8. What the platform does with it (Phase 5, not this item)

`runner_deployment.tenant == operator.tenant` → the run is presentable as that customer's evidence; `unresolved` → refused
to match; inequality → an integrity finding on the run, surfaced, never silently resolved to either side.

## 9. Built (2026-09-21)

- `mark_platform/permitted_calls.py` and `harness/permitted-calls.json` — the gate and the declaration (§4), nine calls: the five
  AWS identity calls, the three model-server calls the harness already made (`vllm.models`, `vllm.version`, `vllm.metrics`),
  and `otlp.export` declared as unroutable with its note. `serving.py`'s record and sampler go through the gate. The
  declaration's sha256 is pinned as `pins.permitted_calls_sha256`; every call lands in `<run_dir>/harness-calls.jsonl`
  (`vllm.metrics` logs its first and is counted thereafter) and is counted in `operator.permitted_calls.made`.
- `mark_platform/operator.py` — `resolve_operator` (§2, §5): DMI first, then the provider through the gate; AWS from the
  IMDSv2 identity document verified in-process against `harness/aws-identity-certs/<region>.rsa.pem` (RSA, PKCS#1 v1.5,
  SHA-256 then SHA-1, the digest recorded), STS `GetCallerIdentity` signed with an in-house SigV4 when the instance has a
  role, `account_disagreement` when the two differ; Runpod as ruled (`lab:arvaken`, `kind: pod`, the file's pod id marked
  unverified); laptop as `local:<hostname_hash12>`. `run_open_operator` and `check_operator_binding` for sign.
- `harness/aws-identity-certs/` — 36 regions' RSA certificates parsed from AWS's page (README records the page sha256 and the
  refresh procedure; `SHA256SUMS` beside them; a test parses every file, checks the sums and the validity window).
- Runner: resolved first in `open_run`, before the mock world or a proxy exists; the `run_open` record carries the block;
  `close_run` writes it to results and the manifest (`operator`, beside `account`) with the calls counted; `run sign`
  refuses a manifest whose block differs from `run_open`'s (`permitted_calls.made` excepted, the declaration hash compared),
  placed beside the account check; redecide leaves it byte-identical. The report prints one line: tenant, kind, when,
  each fact with its source and whether verified, the declaration hash and the calls made.
- **Differences from the design.** The signature endpoint used is `/latest/dynamic/instance-identity/signature` (the base64
  RSA one), not `rsa2048` (CMS, which `cryptography` cannot verify in-process); AWS's RSA certificates are 1024-bit, which
  the record states (`key_bits`). `MARK_IMDS_BASE` was not added: tests inject the fake provider through `open_run`'s
  `operator_probe` and a stated substitution of the provider's addresses in a copy of the declaration, and an R10 test
  shows the repo's own declaration refuses a loopback provider before any socket.

