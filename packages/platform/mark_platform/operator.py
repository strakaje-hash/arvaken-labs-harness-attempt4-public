"""Operator of record (attempt 4, C2): where the run executed, from the provider's own identity, never from configuration.

Founder ruling 2026-09-21: "A tenant is whatever the environment's owner says it is when asked: on AWS, the account id from
the identity endpoint, the region and instance id from instance metadata -- three facts, each with its source recorded. On a
rented pod for a Lab run, the pod provider's identity and id from its own metadata. On a laptop, the hostname hash and
nothing pretending to be more. Never the account id from a config file, never the region from an environment variable."

The record is resolved in `open_run` before any cell, chained in the `run_open` ledger record, copied into the signed
manifest and compared at `run sign`. Its `tenant` is the literal the platform's `runner_deployment.tenant` carries:
`aws:<account>` | `lab:arvaken` | `local:<hostname_hash>` | `unresolved`.

Detection order, and why a container on AWS never reads "laptop": the kernel's DMI table names the hypervisor before any
call is made; if it says EC2 and the identity endpoint cannot be reached, the record is `unresolved: imds_unreachable`.
Every outbound call goes through the harness's permitted-calls gate.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import platform
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from mark_ledger.canonical import sha256_hex
from mark_ledger.keys import iso, now_utc
from mark_timing import mono_ns

from .permitted_calls import CallGate, CallNotPermitted

OPERATOR_SCHEMA = "mark.run-operator/1"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CERTS_DIR = REPO_ROOT / "harness" / "aws-identity-certs"
IMDS_BASE = "http://169.254.169.254"
DMI_DIR = Path("/sys/class/dmi/id")
RUNPOD_ENV_FILE = Path("/etc/rp_environment")
LAB_TENANT = "lab:arvaken"
_ACCOUNT = re.compile(r"^\d{12}$")


def _stamp() -> dict[str, Any]:
    # A9c: a decimal string, not an integer. This record is the one part of a signed manifest that carries a
    # monotonic reading, and a monotonic reading's magnitude is the host's uptime -- an integer here signs on a
    # laptop up three days and is refused on a pod up four months. The string travels exactly on both.
    return {"iso": iso(now_utc()), "mono_ns": str(mono_ns())}


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def detect_provider(dmi_dir: Path = DMI_DIR, rp_env: Path = RUNPOD_ENV_FILE) -> dict[str, Any]:
    """What the kernel and the filesystem say about the host BEFORE any call: the hypervisor's DMI strings, the pod provider's file."""
    dmi = {k: _read(dmi_dir / k) for k in ("sys_vendor", "product_name", "board_asset_tag", "product_uuid")}
    vendor = (dmi.get("sys_vendor") or "").lower()
    tag = dmi.get("board_asset_tag") or ""
    is_ec2 = "amazon" in vendor or tag.startswith("i-")
    return {"dmi": dmi, "is_ec2": is_ec2, "runpod_env_file": rp_env.exists(), "dmi_source": str(dmi_dir), "runpod_env_path": str(rp_env)}


# ---- AWS: the identity document, its signature, and the caller the credentials belong to ----

def _verify_document(doc: bytes, signature_b64: str, cert_pem: bytes) -> dict[str, Any]:
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    cert = x509.load_pem_x509_certificate(cert_pem)
    key = cert.public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        return {"ok": False, "reason": "vendored certificate is not RSA"}
    sig = base64.b64decode(signature_b64.strip())
    for digest in (hashes.SHA256(), hashes.SHA1()):
        try:
            key.verify(sig, doc, padding.PKCS1v15(), digest)
            return {"ok": True, "digest": digest.name, "certificate_sha256": sha256_hex(cert_pem), "certificate_subject": cert.subject.rfc4514_string(), "key_bits": key.key_size}
        except InvalidSignature:
            continue
    return {"ok": False, "reason": "signature does not verify against the vendored certificate (SHA256 and SHA1, PKCS#1 v1.5)", "certificate_sha256": sha256_hex(cert_pem)}


def _sigv4_headers(method: str, url: str, body: bytes, *, region: str, service: str, access_key: str, secret_key: str, token: str | None, when: dt.datetime) -> dict[str, str]:
    """AWS Signature Version 4 for one request, written here so the harness needs no SDK to ask the provider who it is."""
    u = urlsplit(url)
    host = u.netloc
    amz_date = when.strftime("%Y%m%dT%H%M%SZ")
    date = when.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-date": amz_date, "content-type": "application/x-www-form-urlencoded; charset=utf-8"}
    if token:
        headers["x-amz-security-token"] = token
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers))
    canonical_request = "\n".join([method.upper(), quote(u.path or "/", safe="/"), u.query, canonical_headers, signed, payload_hash])
    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])

    def _hmac(k: bytes, msg: str) -> bytes:
        return hmac.new(k, msg.encode(), hashlib.sha256).digest()

    k_signing = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), date), region), service), "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    out = {k: v for k, v in headers.items() if k != "host"}
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    return out


def _fact(name: str, value: Any, call: str, endpoint: str, read_at: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"name": name, "value": value, "source": {"call": call, "endpoint": endpoint, "read_at": read_at}, **extra}


def resolve_aws(gate: CallGate, *, imds_base: str = IMDS_BASE, certs_dir: Path = DEFAULT_CERTS_DIR, sts_base: str | None = None, timeout_s: float = 2.0) -> dict[str, Any]:
    """The three facts from the signed identity document, and the caller from STS when the instance has a role."""
    facts: list[dict[str, Any]] = []
    tok = gate.call("aws.imds.token", "PUT", f"{imds_base}/latest/api/token", headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}, timeout_s=timeout_s)
    if not tok.ok:
        return {"resolved": False, "reason": f"imds_unreachable: the identity endpoint did not answer the IMDSv2 token request ({tok.error or tok.status})", "facts": facts}
    h = {"X-aws-ec2-metadata-token": tok.text().strip()}
    doc = gate.call("aws.imds.identity_document", "GET", f"{imds_base}/latest/dynamic/instance-identity/document", headers=h, timeout_s=timeout_s)
    if not doc.ok:
        return {"resolved": False, "reason": f"imds_unreachable: no identity document ({doc.error or doc.status})", "facts": facts}
    try:
        body = json.loads(doc.text())
    except ValueError:
        return {"resolved": False, "reason": "identity_document_unreadable: the identity endpoint returned something that is not the document", "facts": facts}
    read_at = {"iso": iso(now_utc()), "mono_ns": str(doc.ended)}   # A9c
    ep_doc = f"{imds_base}/latest/dynamic/instance-identity/document"
    account, region, instance = body.get("accountId"), body.get("region"), body.get("instanceId")
    sig = gate.call("aws.imds.identity_signature", "GET", f"{imds_base}/latest/dynamic/instance-identity/signature", headers=h, timeout_s=timeout_s)
    verified: dict[str, Any]
    cert_p = certs_dir / f"{region}.rsa.pem" if region else None
    if not sig.ok:
        verified = {"ok": False, "reason": f"no signature from the identity endpoint ({sig.error or sig.status})"}
    elif cert_p is None or not cert_p.exists():
        verified = {"ok": False, "reason": f"no_vendored_certificate_for_region: {region!r} has no certificate under {certs_dir}"}
    else:
        verified = _verify_document(doc.body, sig.text(), cert_p.read_bytes())
    verified["by"] = "aws.imds.identity_signature"
    for name, value in (("account_id", account), ("region", region), ("instance_id", instance)):
        facts.append(_fact(name, value, "aws.imds.identity_document", ep_doc, read_at, verified=verified))
    if not verified["ok"]:
        return {"resolved": False, "reason": f"identity_document_unverified: {verified['reason']}", "facts": facts}
    if not (isinstance(account, str) and _ACCOUNT.match(account)) or not region or not instance:
        return {"resolved": False, "reason": "identity_document_incomplete: a verified document without account, region and instance id", "facts": facts}
    # the caller the credentials belong to, when the instance has a role: corroborates the account id
    roles = gate.call("aws.imds.role_credentials", "GET", f"{imds_base}/latest/meta-data/iam/security-credentials/", headers=h, timeout_s=timeout_s)
    role = roles.text().strip().splitlines()[0].strip() if roles.ok and roles.text().strip() else None
    if role:
        creds_r = gate.call("aws.imds.role_credentials", "GET", f"{imds_base}/latest/meta-data/iam/security-credentials/{role}", headers=h, timeout_s=timeout_s)
        try:
            creds = json.loads(creds_r.text()) if creds_r.ok else {}
        except ValueError:
            creds = {}
        if creds.get("AccessKeyId") and creds.get("SecretAccessKey"):
            url = sts_base or f"https://sts.{region}.amazonaws.com/"
            payload = b"Action=GetCallerIdentity&Version=2011-06-15"
            headers = _sigv4_headers("POST", url, payload, region=region, service="sts", access_key=creds["AccessKeyId"], secret_key=creds["SecretAccessKey"], token=creds.get("Token"), when=dt.datetime.now(dt.timezone.utc))
            sts = gate.call("aws.sts.get_caller_identity", "POST", url, headers=headers, content=payload, timeout_s=max(timeout_s, 5.0))
            sts_read = {"iso": iso(now_utc()), "mono_ns": str(sts.ended)}   # A9c
            if sts.ok:
                try:
                    root = ET.fromstring(sts.text())
                    ns = {"s": "https://sts.amazonaws.com/doc/2011-06-15/"}
                    sts_account = (root.findtext(".//s:Account", namespaces=ns) or root.findtext(".//Account") or "").strip()
                    arn = (root.findtext(".//s:Arn", namespaces=ns) or root.findtext(".//Arn") or "").strip()
                except ET.ParseError:
                    sts_account, arn = "", ""
                facts.append(_fact("caller_arn", arn or None, "aws.sts.get_caller_identity", url, sts_read, role=role, corroborates="account_id", sts_account=sts_account or None))
                if sts_account and sts_account != account:
                    return {"resolved": False, "reason": f"account_disagreement: the identity document says {account}, STS says {sts_account}", "facts": facts}
            else:
                facts.append(_fact("caller_arn", None, "aws.sts.get_caller_identity", url, sts_read, role=role, corroborates="account_id", error=sts.error or f"status {sts.status}"))
    return {"resolved": True, "reason": None, "facts": facts, "tenant": f"aws:{account}"}


# ---- the record ----

def resolve_operator(gate: CallGate, *, study_set: str | None = None, imds_base: str = IMDS_BASE, dmi_dir: Path = DMI_DIR, rp_env: Path = RUNPOD_ENV_FILE,
                     certs_dir: Path = DEFAULT_CERTS_DIR, sts_base: str | None = None, hostname: str | None = None, timeout_s: float = 2.0) -> dict[str, Any]:
    """Where this run executes, resolved now. Reads the kernel's DMI table and the provider's file first, then asks the provider
    through the permitted-calls gate; never an environment variable or a config file."""
    asked_at = _stamp()
    det = detect_provider(dmi_dir, rp_env)
    rec: dict[str, Any] = {"schema": OPERATOR_SCHEMA, "asked_at": asked_at, "before_first_cell": True, "detection": det, "study_set": study_set}
    if det["is_ec2"]:
        try:
            aws = resolve_aws(gate, imds_base=imds_base, certs_dir=certs_dir, sts_base=sts_base, timeout_s=timeout_s)
        except CallNotPermitted as e:
            aws = {"resolved": False, "reason": f"call_not_permitted: {e}", "facts": []}
        rec.update(kind="aws", resolved=aws["resolved"], reason=aws["reason"], facts=aws["facts"], tenant=aws.get("tenant") if aws["resolved"] else "unresolved")
    elif det["runpod_env_file"]:
        # founder ruling: a root-writable file inside the container is something the process could have written itself; the pod
        # id is recorded as what it is -- a claim from a file, unverified -- and the tenant is the Lab attribution
        text = _read(rp_env) or ""
        m = re.search(r"RUNPOD_POD_ID=['\"]?([A-Za-z0-9_-]+)", text)
        facts = [_fact("pod_id", m.group(1) if m else None, "file", str(rp_env), asked_at, provider="runpod", verified={"ok": False, "reason": "a file inside the container, writable by root; not the provider's identity"}),
                 _fact("hostname_hash", sha256_hex(hostname or platform.node())[:12], "os", "platform.node()", asked_at)]
        rec.update(kind="pod", resolved=True, reason=None, facts=facts, tenant=LAB_TENANT)
    else:
        rec.update(kind="laptop", resolved=True, reason=None, tenant=f"local:{sha256_hex(hostname or platform.node())[:12]}",
                   facts=[_fact("hostname_hash", sha256_hex(hostname or platform.node())[:12], "os", "platform.node()", asked_at)])
    rec["permitted_calls"] = gate.summary()
    return rec


def run_open_operator(run_dir: str | Path) -> dict[str, Any] | None:
    """The operator block the run_open record carries, or None when the run predates C2 (or has no run_open record)."""
    from mark_ledger.store import Ledger

    run_dir = Path(run_dir)
    led = Ledger(run_dir / "ledger")
    try:
        chains = [d.stem for d in (run_dir / "ledger" / "chains").glob("*.jsonl")] if (run_dir / "ledger" / "chains").is_dir() else []
    except OSError:
        chains = []
    for chain in chains:
        for r in led.records(chain):
            if r.kind == "run_open":
                try:
                    return json.loads(led.get_object(r.content_hash).decode("utf-8")).get("operator")
                except (OSError, ValueError):
                    return None
    return None


def check_operator_binding(manifest_operator: dict[str, Any] | None, run_open_operator: dict[str, Any] | None) -> list[str]:
    """At sign: the manifest's operator block must be the run_open record's, field by field. A run that started somewhere cannot
    finish claiming somewhere else. Both absent is a bundle from before C2 and is not a difference."""
    if manifest_operator is None and run_open_operator is None:
        return []
    if manifest_operator is None:
        return ["the manifest carries no operator block but the run_open record does"]
    if run_open_operator is None:
        return ["the manifest carries an operator block but the run_open record does not: it was not resolved at open"]
    diffs = []
    for k in sorted(set(manifest_operator) | set(run_open_operator)):
        if k == "permitted_calls":
            continue   # the count grows through the run; the declaration hash is compared
        if manifest_operator.get(k) != run_open_operator.get(k):
            diffs.append(f"{k}: manifest {json.dumps(manifest_operator.get(k), sort_keys=True)[:80]} vs run_open {json.dumps(run_open_operator.get(k), sort_keys=True)[:80]}")
    mp, rp = (manifest_operator.get("permitted_calls") or {}).get("declaration"), (run_open_operator.get("permitted_calls") or {}).get("declaration")
    if mp != rp:
        diffs.append("permitted_calls.declaration: the declaration changed between open and close")
    return diffs
