"""C2 (attempt 4): the operator of record comes from the provider's own identity, never from configuration; a container on
AWS with no reachable metadata reads unresolved, never laptop; every fact carries its source; every call is declared."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from mark_platform.operator import check_operator_binding, detect_provider, resolve_operator
from mark_platform.permitted_calls import CallGate, load_declaration

DOC = {"accountId": "123456789012", "region": "eu-west-1", "instanceId": "i-0abc123def456", "architecture": "x86_64", "imageId": "ami-1"}
DOC_BYTES = json.dumps(DOC, indent=2).encode()


@pytest.fixture(scope="module")
def signer():
    """A test 'AWS': an RSA key and a self-signed certificate the test vendors as the regional certificate."""
    import datetime as dt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "test provider")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)).not_valid_after(dt.datetime(2036, 1, 1, tzinfo=dt.timezone.utc)).sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM)


def _certs(tmp_path, pem, region="eu-west-1"):
    d = tmp_path / "certs"
    d.mkdir(exist_ok=True)
    (d / f"{region}.rsa.pem").write_bytes(pem)
    return d


def _dmi(tmp_path, vendor="Amazon EC2", tag="i-0abc123def456"):
    d = tmp_path / "dmi"
    d.mkdir(exist_ok=True)
    (d / "sys_vendor").write_text(vendor + "\n")
    (d / "board_asset_tag").write_text(tag + "\n")
    return d


@pytest.fixture
def imds(signer):
    """A fake IMDSv2 + STS on one loopback server: the endpoint the code reads is the base URL the test hands it (R9)."""
    key, pem = signer
    state = {"doc": DOC_BYTES, "sig": base64.b64encode(key.sign(DOC_BYTES, padding.PKCS1v15(), hashes.SHA256())).decode(), "role": "harness-role", "sts_account": "123456789012", "hits": [], "token_ok": True}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body, ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):
            state["hits"].append(("PUT", self.path))
            if self.path == "/latest/api/token" and state["token_ok"]:
                return self._send(200, b"tok-123")
            self._send(403, b"")

        def do_GET(self):
            state["hits"].append(("GET", self.path))
            if self.headers.get("X-aws-ec2-metadata-token") != "tok-123":
                return self._send(401, b"")
            if self.path == "/latest/dynamic/instance-identity/document":
                return self._send(200, state["doc"], "application/json")
            if self.path == "/latest/dynamic/instance-identity/signature":
                return self._send(200, state["sig"].encode())
            if self.path == "/latest/meta-data/iam/security-credentials/":
                return self._send(200, (state["role"] or "").encode())
            if self.path == f"/latest/meta-data/iam/security-credentials/{state['role']}":
                return self._send(200, json.dumps({"AccessKeyId": "AKIATEST", "SecretAccessKey": "secret", "Token": "sess"}).encode(), "application/json")
            self._send(404, b"")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            state["hits"].append(("POST", self.path, body.decode(), self.headers.get("Authorization", "")[:24]))
            xml = f'<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><GetCallerIdentityResult><Arn>arn:aws:sts::{state["sts_account"]}:assumed-role/harness-role/i-0abc</Arn><UserId>AROA:i-0abc</UserId><Account>{state["sts_account"]}</Account></GetCallerIdentityResult></GetCallerIdentityResponse>'
            self._send(200, xml.encode(), "text/xml")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, state, pem
    srv.shutdown()


def _gate(tmp_path, fake_host: str | None = None):
    """The repo's declaration as the gate reads it. With `fake_host`, the same declaration with the provider's addresses (the
    link-local identity endpoint, the regional STS host) replaced by the fake provider's loopback address -- the one
    substitution the test makes, stated here; nothing else in the declaration changes."""
    from mark_platform.permitted_calls import DEFAULT_DECLARATION

    if fake_host is None:
        return CallGate(load_declaration(), tmp_path / "harness-calls.jsonl")
    body = json.loads(DEFAULT_DECLARATION.read_text(encoding="utf-8"))
    for c in body["calls"]:
        if c["id"].startswith("aws."):
            c["host"] = fake_host
    p = tmp_path / "permitted-calls.test.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return CallGate(load_declaration(p), tmp_path / "harness-calls.jsonl")


def _resolve(tmp_path, imds_base, pem, **kw):
    # defaults built only when not given: setdefault would write the eu-west-1 certificate even when a test hands its own directory
    if "dmi_dir" not in kw:
        kw["dmi_dir"] = _dmi(tmp_path)
    kw.setdefault("rp_env", tmp_path / "no-rp-file")
    if "certs_dir" not in kw:
        kw["certs_dir"] = _certs(tmp_path, pem)
    kw.setdefault("sts_base", imds_base + "/")
    return resolve_operator(_gate(tmp_path, fake_host="127.0.0.1"), imds_base=imds_base, hostname="laptop-x", **kw)


def test_the_real_declaration_refuses_the_identity_calls_anywhere_but_the_link_local_endpoint(tmp_path):
    """R10 on the substitution above: under the repo's own declaration a loopback 'provider' is refused before any socket."""
    from mark_platform.permitted_calls import CallNotPermitted

    with pytest.raises(CallNotPermitted, match="declared for PUT 169.254.169.254/latest/api/token"):
        _gate(tmp_path).call("aws.imds.token", "PUT", "http://127.0.0.1:1/latest/api/token")


def test_on_aws_the_three_facts_come_from_the_signed_document_and_sts_corroborates_the_account(tmp_path, imds, monkeypatch):
    base, state, pem = imds
    # configuration is present and must reach nothing (R10)
    monkeypatch.setenv("AWS_ACCOUNT_ID", "111111111111")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    rec = _resolve(tmp_path, base, pem)
    assert rec["schema"] == "mark.run-operator/1" and rec["kind"] == "aws" and rec["resolved"] is True and rec["tenant"] == "aws:123456789012" and rec["reason"] is None
    facts = {f["name"]: f for f in rec["facts"]}
    assert facts["account_id"]["value"] == "123456789012" and facts["region"]["value"] == "eu-west-1" and facts["instance_id"]["value"] == "i-0abc123def456"
    for n in ("account_id", "region", "instance_id"):
        assert facts[n]["source"]["call"] == "aws.imds.identity_document" and facts[n]["source"]["endpoint"].endswith("/latest/dynamic/instance-identity/document")
        assert facts[n]["verified"]["ok"] is True and facts[n]["verified"]["digest"] == "sha256" and facts[n]["verified"]["by"] == "aws.imds.identity_signature"
    assert facts["caller_arn"]["value"].startswith("arn:aws:sts::123456789012:") and facts["caller_arn"]["corroborates"] == "account_id" and facts["caller_arn"]["role"] == "harness-role"
    assert "111111111111" not in json.dumps(rec) and "us-west-2" not in json.dumps(rec)
    # every call went through the gate: declared, counted, the STS request SigV4-signed
    made = rec["permitted_calls"]["made"]
    assert made == {"aws.imds.identity_document": 1, "aws.imds.identity_signature": 1, "aws.imds.role_credentials": 2, "aws.imds.token": 1, "aws.sts.get_caller_identity": 1}
    sts_hit = next(h for h in state["hits"] if h[0] == "POST")
    assert sts_hit[2] == "Action=GetCallerIdentity&Version=2011-06-15" and sts_hit[3].startswith("AWS4-HMAC-SHA256")
    assert rec["before_first_cell"] is True and rec["detection"]["is_ec2"] is True
    # A9c: a decimal string, because this record is signed and a monotonic reading's size is the host's uptime
    assert isinstance(rec["asked_at"]["mono_ns"], str) and int(rec["asked_at"]["mono_ns"]) > 0


def test_dmi_says_ec2_and_metadata_is_unreachable_reads_unresolved_never_laptop(tmp_path, imds):
    base, state, pem = imds
    state["token_ok"] = False
    rec = _resolve(tmp_path, base, pem)
    assert rec["kind"] == "aws" and rec["resolved"] is False and rec["tenant"] == "unresolved" and rec["reason"].startswith("imds_unreachable")
    assert not any(f["name"] == "hostname_hash" for f in rec["facts"])


def test_a_document_whose_signature_does_not_verify_is_unresolved_with_the_facts_kept_and_marked(tmp_path, imds):
    base, state, pem = imds
    state["doc"] = json.dumps({**DOC, "accountId": "999999999999"}).encode()   # the document changed; the signature did not
    rec = _resolve(tmp_path, base, pem)
    assert rec["resolved"] is False and rec["tenant"] == "unresolved" and rec["reason"].startswith("identity_document_unverified")
    facts = {f["name"]: f for f in rec["facts"]}
    assert facts["account_id"]["value"] == "999999999999" and facts["account_id"]["verified"]["ok"] is False
    assert "999999999999" not in rec["tenant"]


def test_no_vendored_certificate_for_the_region_is_unresolved_by_name(tmp_path, imds):
    base, state, pem = imds
    other = tmp_path / "certs-other"
    other.mkdir()
    (other / "us-east-1.rsa.pem").write_bytes(pem)
    rec = _resolve(tmp_path, base, pem, certs_dir=other)
    assert rec["resolved"] is False and "no_vendored_certificate_for_region: 'eu-west-1'" in rec["reason"]


def test_sts_disagreeing_with_the_document_is_unresolved_with_both_values(tmp_path, imds):
    base, state, pem = imds
    state["sts_account"] = "222222222222"
    rec = _resolve(tmp_path, base, pem)
    assert rec["resolved"] is False and rec["reason"] == "account_disagreement: the identity document says 123456789012, STS says 222222222222"


def test_without_an_instance_role_the_document_alone_resolves_and_says_no_caller(tmp_path, imds):
    base, state, pem = imds
    state["role"] = ""
    rec = _resolve(tmp_path, base, pem)
    assert rec["resolved"] is True and rec["tenant"] == "aws:123456789012"
    assert not any(f["name"] == "caller_arn" for f in rec["facts"]) and rec["permitted_calls"]["made"]["aws.imds.role_credentials"] == 1


def test_a_runpod_file_is_a_claim_from_a_file_marked_unverified_and_the_tenant_is_the_lab_attribution(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNPOD_POD_ID", "env-should-not-be-read")
    rp = tmp_path / "rp_environment"
    rp.write_text("RUNPOD_POD_ID='pod-abc123'\nRUNPOD_DC_ID='EU-RO-1'\n")
    rec = resolve_operator(_gate(tmp_path), study_set="labs", dmi_dir=_dmi(tmp_path, vendor="QEMU", tag=""), rp_env=rp, hostname="pod-host")
    assert rec["kind"] == "pod" and rec["tenant"] == "lab:arvaken" and rec["resolved"] is True and rec["study_set"] == "labs"
    pod = next(f for f in rec["facts"] if f["name"] == "pod_id")
    assert pod["value"] == "pod-abc123" and pod["provider"] == "runpod" and pod["source"]["call"] == "file" and pod["verified"]["ok"] is False and "writable by root" in pod["verified"]["reason"]
    assert "env-should-not-be-read" not in json.dumps(rec) and rec["permitted_calls"]["made"] == {}


def test_a_laptop_reads_the_hostname_hash_and_nothing_pretending_to_be_more(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCOUNT_ID", "111111111111")
    rec = resolve_operator(_gate(tmp_path), dmi_dir=tmp_path / "no-dmi", rp_env=tmp_path / "no-rp", hostname="laptop-x")
    from mark_ledger.canonical import sha256_hex

    assert rec["kind"] == "laptop" and rec["tenant"] == f"local:{sha256_hex('laptop-x')[:12]}" and rec["resolved"] is True
    assert [f["name"] for f in rec["facts"]] == ["hostname_hash"] and rec["facts"][0]["source"] == {"call": "os", "endpoint": "platform.node()", "read_at": rec["asked_at"]}
    assert rec["permitted_calls"]["made"] == {} and rec["detection"]["is_ec2"] is False
    assert detect_provider(tmp_path / "no-dmi", tmp_path / "no-rp")["dmi"]["sys_vendor"] is None


def test_the_binding_check_names_the_field_that_moved_and_ignores_the_growing_call_count():
    a = {"schema": "mark.run-operator/1", "tenant": "aws:1", "kind": "aws", "facts": [], "permitted_calls": {"declaration": {"sha256": "d"}, "made": {}}}
    b = dict(a, permitted_calls={"declaration": {"sha256": "d"}, "made": {"vllm.metrics": 40}})
    assert check_operator_binding(b, a) == []
    assert check_operator_binding(dict(b, tenant="aws:2"), a) == ["tenant: manifest \"aws:2\" vs run_open \"aws:1\""]
    assert check_operator_binding(dict(b, permitted_calls={"declaration": {"sha256": "e"}, "made": {}}), a) == ["permitted_calls.declaration: the declaration changed between open and close"]
    assert check_operator_binding(None, None) == [] and check_operator_binding(None, a) and check_operator_binding(b, None)


def test_every_vendored_aws_certificate_parses_is_rsa_matches_its_checksum_and_is_valid_today():
    """The vendored set is what the identity document is verified against; a file that does not parse, is not the RSA
    variant, or has left its validity window would make every AWS run on that region unresolved -- say so here first."""
    import datetime as dt
    import hashlib

    from mark_platform.operator import DEFAULT_CERTS_DIR

    sums = dict(reversed(l.split("  ", 1)) for l in (DEFAULT_CERTS_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines() if l.strip())
    pems = sorted(DEFAULT_CERTS_DIR.glob("*.rsa.pem"))
    assert len(pems) >= 30 and {p.name for p in pems} == set(sums), "every certificate is in SHA256SUMS and vice versa"
    today = dt.datetime.now(dt.timezone.utc)
    for p in pems:
        assert hashlib.sha256(p.read_bytes()).hexdigest() == sums[p.name], p.name
        c = x509.load_pem_x509_certificate(p.read_bytes())
        # two subject forms on AWS's page: "O=Amazon Web Services LLC" and "CN=ec2.amazonaws.com,O=Amazon.com Inc."
        assert isinstance(c.public_key(), rsa.RSAPublicKey) and "Amazon" in c.subject.rfc4514_string(), (p.name, c.subject.rfc4514_string())
        assert c.not_valid_before_utc <= today <= c.not_valid_after_utc, f"{p.name}: outside its validity window ({c.not_valid_after_utc.date()}); refresh by the README procedure"
    assert (DEFAULT_CERTS_DIR / "eu-west-1.rsa.pem").exists() and (DEFAULT_CERTS_DIR / "us-east-1.rsa.pem").exists()
    assert "Refresh procedure" in (DEFAULT_CERTS_DIR / "README.md").read_text(encoding="utf-8")


# ---- A9c: this record is the one part of a signed manifest that carries a clock reading --------------------------
def test_no_stamp_in_the_operator_record_is_an_integer(tmp_path):
    """**Where the defect was.** C2 wrote `mono_ns` as an integer here on 2026-09-21 and every laptop test passed,
    because a monotonic clock's magnitude is the host's uptime: 3.3e14 here, 1.4e16 on a pod up four months. A9's
    magnitude rule would have fired for the first time at Phase 0 signing, on the pod. This asserts the shape, which
    is the same on every host."""
    from mark_ledger.canonical import integer_timestamp

    rec = resolve_operator(_gate(tmp_path), dmi_dir=tmp_path / "no-dmi", rp_env=tmp_path / "no-rp", hostname="laptop-x")
    assert integer_timestamp(rec) is None, integer_timestamp(rec)
    assert isinstance(rec["asked_at"]["mono_ns"], str)
    assert all(isinstance(f["source"]["read_at"]["mono_ns"], str) for f in rec["facts"])


def test_a_manifest_carrying_this_record_signs_at_a_pod_magnitude_stamp(tmp_path):
    """The end-to-end shape, through the real builder and the real chokepoint: the reading that a pod actually
    produced in attempt 3 (2.09e16) now signs, and the integer form of the same reading is refused by name."""
    from datetime import timedelta

    import pytest as _pytest
    from mark_ledger.canonical import NotPortable
    from mark_ledger.keys import (KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, iso, key_id, now_utc)
    from mark_ledger.manifest import build_manifest, sign_manifest

    rec = resolve_operator(_gate(tmp_path), dmi_dir=tmp_path / "no-dmi", rp_env=tmp_path / "no-rp", hostname="laptop-x")
    rec["asked_at"]["mono_ns"] = "20923021892223845"           # what a pod that ran attempt 3 actually read

    root_priv, root_pub = generate_keypair()
    priv, pub = generate_keypair()
    cert = issue_key_cert({"schema": KEY_CERT_SCHEMA, "key_id": key_id(pub), "public_key": pub,
                           "purpose": "run-manifest", "root_id": key_id(root_pub),
                           "not_before": iso(now_utc() - timedelta(days=1)),
                           "not_after": iso(now_utc() + timedelta(days=30))}, root_priv)
    pins = {"image_digest": "sha256:x", "lockfile_sha256": "y", "engine_version": "v", "repo_commit": "c"}
    def _m(op):
        return build_manifest(run_id="r", benchmark=None, pins=pins, environment={}, started_at="2026-09-21T00:00:00Z",
                              ended_at="2026-09-21T00:01:00Z", chain_id="c", chain_root="r", results_hash="h", operator=op)

    assert sign_manifest(_m(rec), priv, cert)                   # the string form signs
    import copy
    bad = copy.deepcopy(rec)
    bad["asked_at"]["mono_ns"] = 20923021892223845              # the form C2 shipped
    with _pytest.raises(NotPortable) as caught:
        sign_manifest(_m(bad), priv, cert)
    assert caught.value.path == "$.operator.asked_at.mono_ns"
