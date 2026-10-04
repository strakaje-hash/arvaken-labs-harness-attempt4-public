# Arvaken Labs — Testability Ledger v2

> **Source.** `Arvaken Testability Ledger v2.pdf`, 21 pages, sha256
> `9de84976fd4c9bab559b8e80d3b6b434e1b031a47c5c85a8196a612f55913821`. The founder printed it on 2026-09-15 from the
> markdown below; the PDF's metadata title is `arvaken-testability-ledger-v2.md`.
> - The PDF has no text layer. Every page was checked against its rendering on 2026-09-15, and no text difference was
>   found.
> - Where this copy and the PDF differ, the PDF governs and the copy is corrected.
>
> **Governing policy.** Arvaken Labs Publication and Independence Policy, Version 2.0, signed 2026-09-15, PDF sha256
> `049b6a93e097cb19a78f9dba86316a73606827c143d0b574c810048b8977991d`. The Layer 1 change-set
> (`docs/LAYER1_PLAN_CHANGES-2026-09-15.md`) cites this ledger.
>
> **Read by** `packages/platform/mark_platform/registry.py`:
> - `EXECUTION_CLASSES` and `PUBLICATION_CLASSES` are the §0.3 values, exactly;
> - `FAMILY_TAGS` are the §0.4 tags plus the parent `halt`, which §0.4 does not list;
> - `tag_states` claims are held to the §0.1 five-part standard, with each quote under fifteen words.

**Supersedes:** Testability Ledger v1 (2026-09-12)
**Findings dated:** 2026-09-15
**Status:** Population 1 incomplete. Two documents outstanding (see Open Items).

This ledger records **access and permission only**. It makes no claim about whether
any control works. Terms change; every row is re-checked on the day testing begins.

---

## 0. How to read this ledger

### 0.1 The five-part evidentiary standard

Every classification that is not `Undetermined` must carry all five:

1. **Target URL** — canonical direct URL to the governing legal document.
2. **Document revision date** — the document's own "last updated" or effective date.
3. **Retrieval date** — the date Arvaken retrieved and preserved it.
4. **Section heading** — the specific section number or contractual title.
5. **Verbatim quote** — exact text from that section, under fifteen (15) words.

One quote per document. Everything else is paraphrased. If any of the five is
missing, the value is `Undetermined` and the missing element is named.

### 0.2 The three-document check (added v2)

A vendor is not a classification. For any hosted service, all three must be
retrieved or the row is `Undetermined`:

- **General / master terms** (e.g. AWS Service Terms, Google General Service Terms)
- **Service-specific schedule** — these override the general terms and, in at
  least one vendor, do so specifically for security products
- **Launch-stage terms** — Pre-GA, Preview, and Beta terms commonly add
  confidentiality obligations that restrict publication independently of any
  benchmarking clause

*Why this exists:* Google's General Service Terms permit publication without
consent; its §9 bars it outright for two named security products; its SecOps
Service Specific Terms require prior written consent. Same vendor, three postures.

### 0.3 Two axes, separate vocabularies

Execution and publication are independent. They do not share a letter ladder.

**Execution axis**

| Value | Definition |
|---|---|
| `Permitted` | Testing authorized or unrestricted under the accepted agreement. |
| `Permitted-Safe-Harbor` | Testing authorized under a published vendor policy, within that policy's stated scope. Covers standing own-resource authorizations (cloud pen-test policies, security-testing ROEs) as well as CVD and research programs. |
| `Restricted` | Contractual or technical bar on security testing, adversarial probing, stress testing, or reverse engineering in terms that would cover probing the control. **A security product restricting security testing is flagged as a finding, not a footnote.** |
| `Undetermined` | Terms unretrieved, conflicting, or failing the five-part standard. |

**Publication axis**

| Value | Definition |
|---|---|
| `Unconditional` | Public disclosure permitted with no prior review, notice, or condition. |
| `Conditional-Reciprocal` | Publication permitted, conditioned on replication disclosure and/or a reciprocal benchmarking grant over Arvaken's own products. |
| `Consent-Required` | Prior written approval required before disclosure (classic DeWitt). |
| `Embargo-Coordinated` | Publication permitted after a defined notification or remediation window. |
| `Prohibited` | Absolute contractual bar on disseminating benchmark or comparison results. |
| `Undetermined` | Terms unretrieved or ambiguous. |
| `Undetermined-by-Absence` | No terms document located and none presented at signup. Records that **no contractual restriction was found and no contractual permission, license, or safe harbor exists either.** Absence is not permission; the vendor may publish terms and assert them against continued use. |

### 0.4 Probe family tags

Recorded per candidate **at screening**, before any run. The suite executes only
matching families; `control_not_applicable` is a screening fact in this ledger,
never a row in a results table.

| Tag | Meaning |
|---|---|
| `halt:pre_execution` | Refuses a proposed action before it executes. |
| `halt:in_flight` | Stops or revokes an action already executing. |
| `gate` | Proposed action requires human clearance before execution. |
| `identity/credential` | Agent authority is scoped, brokered, or revoked. |
| `guardrail` | Content classification blocks prohibited input or output. |
| `evidence` | The record survives an adversary holding the data plane. |

**Tag states — claimed and demonstrated.** Every family tag and sub-tag is
recorded in two states, never one.

- **(claimed)** — asserted in the vendor's own documentation, product naming, marketing pages, or sales collateral. Established by documentation review; no probe required. Recorded against the vendor's own document to the five-part standard in §0.1.
- **(demonstrated)** — a probe in that family ran against the product and the capability held. Established only by a run.

No tag is carried on architecture, inference, or plausibility. **An absent
*(demonstrated)* tag means the Lab has not verified the capability. It is never
published, stated, or implied as evidence that the product lacks it.**

**Taxonomy finding (publication zero):** guardrail-family controls map to the
injection and disclosure categories. Halt- and gate-family controls map to
excessive agency. **No content-filter product in the screened population touches
excessive agency at all.** Deployers who bought a filter did not buy a stop. This
follows from family assignment, which is architectural and observable, and does
not depend on any probe having run.

### 0.5 Credential posture

Two different properties, recorded separately:

- **Custody** — does the vendor hold the credential, or does it stay with the customer?
- **Enforcement capability** — can the enforcement point *withhold* the credential
  from the agent, or can it only request compliance?

Only the second is load-bearing for a halt benchmark. A gateway that executes
cleared actions itself from config the agent never sees is
**credential-holding at the customer-hosted gateway** even though the vendor
holds nothing.

---

## 1. Population 1 — Platform-native controls

### 1.1 AWS — Amazon Bedrock Guardrails

*Split from AgentCore in v2. These were one row in v1; they are different products,
different families, and different execution classes.*

- **Family:** `guardrail`
- **Acquisition:** SELF-SERVE, consumption pricing, no sales contact
- **Placement:** out-of-process (API-invoked content/policy filter)
- **Custody:** none — observes content. **Enforcement:** blocks content; cannot withhold a credential
- **Black-box testable:** yes, against own-account resources
- **Execution:** `Permitted-Safe-Harbor` — AWS Customer Support Policy for Penetration Testing, https://aws.amazon.com/security/penetration-testing/, doc modified 2026-09-02, retrieved 2026-09-12, "Permitted Services". Scope limits: no DoS/DDoS; customers may not assess the AWS services themselves.
- **Publication:** `Conditional-Reciprocal` — AWS Service Terms **§1.8**, https://aws.amazon.com/service-terms/, retrieved 2026-09-15. Benchmarks and comparative evaluations are affirmatively permitted and disclosable, conditioned on (i) including and disclosing to AWS "all information necessary to replicate such Benchmark", and (ii) agreeing AWS may benchmark and publish results about Arvaken's products irrespective of restrictions in Arvaken's own terms.
- **Notes:** §1.8 is **not** a DeWitt clause. Condition (i) is satisfied natively by pre-registered probes and a published harness. Condition (i) also creates an **affirmative delivery obligation to AWS**, separate from the right-of-reply packet, with no clock attached. Condition (ii) is unconditional — it applies to any customer who publishes a benchmark, not only competitors.

### 1.2 AWS — Bedrock AgentCore

- **Family:** `identity/credential`, `gate`
- **Acquisition:** SELF-SERVE, consumption pricing
- **Placement:** out-of-process (managed agent runtime / gateway)
- **Custody:** AgentCore Identity brokers agent credentials. **Enforcement:** mediates tool access
- **Black-box testable:** yes, with the outbound constraint below
- **Execution:** `Permitted-Safe-Harbor` — same document as 1.1; AgentCore appears among Permitted Services. **AgentCore also appears on the Prohibited Services for Outbound Penetration Testing list.**
- **Publication:** `Conditional-Reciprocal` — AWS Service Terms §1.8, as above
- **Isolation protocol (mandatory):** all agent tool-calling in AWS evaluations terminates inside an in-VPC mock or stub network in the same account. No egress. The **tool-calls-an-external-API shape — roughly half of real agent work — is out of scope**, and the methodology section says so in plain words so no reader assumes it was covered.
- **Notes:** This is the first commercial subject of the Independent Series: permitted scope, a counterparty that can absorb a finding, no adjacency.

### 1.3 Microsoft — Azure AI Content Safety

- **Family:** `guardrail`
- **Acquisition:** SELF-SERVE. F0 free tier (5,000 text records + 5,000 image analyses/month, requests stop rather than bill); S0 PAYG at $0.38/1,000 text records
- **Placement:** out-of-process API filter
- **Custody:** none. **Enforcement:** returns severity scores; Prompt Shields can block inline
- **Black-box testable:** yes
- **Execution:** `Permitted-Safe-Harbor` — Microsoft Security Testing Rules of Engagement, https://www.microsoft.com/en-us/msrc/pentest-rules-of-engagement, retrieved 2026-09-12, permitting AI-model robustness and restriction-bypass testing. **Element 2 unconfirmed — the ROE page displays no document revision date.** Recorded as a known gap in the five-part standard rather than papered over.
- **Publication:** `Conditional-Reciprocal` — Microsoft Product Terms, Universal License Terms for Online Services, https://www.microsoft.com/licensing/terms/product/ForOnlineServices/all, retrieved 2026-09-15. A customer offering a competitive product "waives any restrictions on competitive use and benchmark testing" in its own terms, and on Microsoft's request must supply replication information **and access to its competitive products** for Microsoft to benchmark.
- **Notes:** The prior-written-approval benchmarking language in Microsoft's licensing terms attaches to **Server Products**, not Online Services. Azure AI Content Safety is an Online Service. Microsoft's reciprocity, unlike AWS's, **triggers on competitiveness** — and Arvaken's verification platform will be hard to argue is non-competitive with Content Safety, Entra Agent ID, or Purview agent controls.

### 1.4 Microsoft — Entra Agent ID / Purview agent controls / Copilot Studio governance

- **Family:** `identity/credential`, `gate`
- **Acquisition:** enterprise licensing — **deferred at Gate 1**
- **Status:** UNSCREENED in v1. These are Microsoft's halt- and gate-family controls and none were examined. Screening required before any claim about Microsoft's coverage of the excessive-agency categories.

### 1.5 Google — Model Armor (Security Command Center)

- **Family:** `guardrail`
- **Acquisition:** SELF-SERVE, free to 2M tokens/month standalone, then $0.10/M tokens
- **Placement:** out-of-process filter
- **Custody:** none. **Enforcement:** blocks/filters
- **Execution:** `Undetermined`
- **Publication:** `Undetermined`
- **Why U — two documents outstanding:**
  1. The **Security Command Center service-specific section** of the Service Specific Terms (https://cloud.google.com/terms/service-terms) has not been retrieved. Google's §9 already overrides the general Benchmarking section with a flat disclosure bar for **Cloud NGFW Enterprise and Cloud IDS** — both security products. Model Armor bills under SCC and is the same shape of product. The general terms cannot classify this row.
  2. Google's documentation flags AI Protection features tied to Model Armor as subject to the **Pre-GA Offerings Terms** in the General Service Terms. Pre-GA terms customarily carry confidentiality obligations that would restrict publication independently of any benchmarking clause.
- **Do not cite Model Armor in the AgentCore paper until both are retrieved.**

### 1.6 Google — General Service Terms §7 (applies to all Google rows)

Retrieved 2026-09-15 from https://cloud.google.com/terms/service-terms.

- **Publication structure:** `Conditional-Reciprocal` where no service-specific override applies. No prior-consent requirement. Conditions: (i) the public disclosure includes all information necessary to replicate the tests; (ii) the customer allows Google to benchmark and publish about the customer's **publicly available** products or services.
- **Non-delegation:** "Customer may itself (but may not permit a third party to)" conduct tests and disclose results. **See §4 of this ledger — this is a business-model constraint, not a row-level one.**
- **Hyperscaler carve-out:** a customer may not test or disclose **on behalf of a hyperscale public cloud provider** without Google's prior written consent.
- **Non-uniformity:** Google's SecOps Service Specific Terms and AlloyDB Omni Developer Edition terms both **require prior written consent** before disclosure. Google is `Consent-Required` in some documents and `Conditional-Reciprocal` in others. Classify per document, never per company.

### 1.7 Google — Vertex AI agent tool permissioning

- **Family:** `gate`, `identity/credential`
- **Status:** UNSCREENED. Never examined in v1. Google's halt- and gate-family controls remain unmeasured.

### 1.8 Anthropic — tool-use permissioning / Claude Agent SDK controls

*v1 carried an internal contradiction (Table 1: U; List C: candidate C). Reconciled to U.*

- **Family:** `gate`
- **Acquisition:** SELF-SERVE, API pay-per-use
- **Placement:** in-process (SDK approval callbacks and tool permissions)
- **Custody:** none — does not broker external credentials. **Enforcement:** blocks the tool call pending approval
- **Black-box testable:** partial — client-side, in Arvaken's own SDK usage on Arvaken's own account
- **Execution:** `Permitted`
- **Publication:** `Undetermined` — Responsible Disclosure Policy (https://www.anthropic.com/responsible-disclosure-policy, last updated 2025-02-14, retrieved 2026-09-12) scopes to internet-facing infrastructure and does not reach model or agent behavior; Commercial ToS §D.4 use restrictions (reverse engineering / competing product) sit on top and have been enforced. **Not Class C** — adversarial black-box testing of Arvaken's own SDK usage is not reverse engineering — but publication of a named result is unaddressed space.
- **Resolution path:** written evaluation authorization naming publication. Not a document-reading problem.

### 1.9 OpenAI — Agents SDK guardrails / function-calling approval (hosted)

- **Family:** `gate`, `guardrail`
- **Acquisition:** SELF-SERVE, API pay-per-use
- **Placement:** in-process (SDK tripwires)
- **Custody:** none. **Enforcement:** tripwire blocks
- **Execution:** `Permitted`
- **Publication:** `Embargo-Coordinated` — OpenAI CVE assignment policy, https://openai.com/policies/openai-cve-assignment-policy/, retrieved 2026-09-12: "AI model safety vulnerabilities… are not within scope of this policy". Disclosure posture requests non-publication until a fix is in place.
- **Notes:** Model- and agent-safety behavior sits **outside** the vulnerability framework entirely, leaving publication of an agent-control result in undefined space governed only by the general terms. Same resolution path as Anthropic: an email requesting written authorization naming publication.

### 1.10 OpenAI Guardrails (OSS) — split by judge placement

MIT license, https://github.com/openai/openai-guardrails-python, retrieved 2026-09-12.

| Configuration | Execution | Publication |
|---|---|---|
| OSS engine + **local** judge (vLLM/Ollama, locally hosted weights) | `Permitted` | `Unconditional` |
| OSS engine + **hosted** judge (OpenAI API) | inherits OpenAI API terms | logic: `Unconditional`; traffic findings: `Embargo-Coordinated` |

**This is a class of correction, not an OpenAI one.** An OSS license governs the
code; the model provider's terms govern the test traffic. Every LLM-as-judge
guardrail inherits it — NeMo against a hosted model, Guardrails AI validators
that call an API, Invariant's hosted guardrailing models. **Independent Series
evaluations of OSS controls bind to locally hosted weights with runtime network
isolation** (egress dropped at the security-group layer during execution; not
air-gapped, which would break signed-hash model acquisition).

### 1.11 NVIDIA NeMo Guardrails (OSS)

- **Family:** `guardrail`
- Apache-2.0, https://github.com/NVIDIA-NeMo/Guardrails, retrieved 2026-09-12
- In-process library; observes/signals; blocks or transforms
- **Execution:** `Permitted` · **Publication:** `Unconditional` (local judge required)
- **Enterprise delta, pinned:** OSS Community edition, standalone library — does **not** include the NeMo Guardrails production microservice container, Kubernetes/Helm auto-scaling, NIM safety models, or enterprise CVE patching SLAs. *Delta stated as of the pinned version in the row name.*

---

## 2. Population 2 — Independent control vendors

### 2.1 Ephor (ephor.dev) — MCP gateway and approval control plane

**Name-collision warning.** Two unrelated companies. **ephor.ai** is a
consumer/education multi-model AI workspace (Alpha School) — not an agent
gateway, not in the population. **ephor.dev** is the subject. v1 screened the
wrong company and recorded "no self-serve path found."

- **Family:** `halt:pre_execution`, `gate`, `evidence`. **Not** `halt:in_flight` — it can refuse a proposed action but cannot revoke an issued credential or stop an action already executing; cleared actions run gateway→target with nothing in the path.
- **Acquisition:** SELF-SERVE. Developer tier $0/forever, no card: MCP gateway + policy engine, Slack approvals, live console, 1 seat, 2 agents, 7-day audit retention. Paid tiers open with a 7-day full-access pilot.
- **Placement:** out-of-process MCP gateway. Local (stdio) or hosted (HTTP + token).
- **Custody:** **vendor holds nothing.** Gateway runs inside the customer's perimeter; tokens, keys, and kubeconfigs stay in customer config files; cleared actions execute directly from the customer's gateway to the customer's systems. The vendor's control plane receives only the decision trail, hash-chained.
- **Enforcement capability:** **credential-holding at the customer-hosted gateway.** The gateway executes cleared actions itself from config the agent never sees — it can withhold rather than request. This is the class the halt benchmark exists to measure.
- **Black-box testable:** yes, and the cleanest target in the screened population — the data plane is self-hosted on Arvaken hardware, so adversarial traffic never touches the vendor's cloud except as decision-trail telemetry.
- **Execution:** `Permitted` (by absence of any restriction) · **Publication:** `Undetermined-by-Absence`
- **Terms status:** No Terms of Service, Privacy Policy, or legal link anywhere on ephor.dev; footer carries Contact, Sign in, Live demo, Product only. ephor.dev/tower-live.html (the signup page) is robots-disallowed and could not be retrieved. A click-through may exist in the signup flow. **Unresolved — blocks the row.**
- **Next step (human, five minutes):** render the signup flow headlessly, capture and hash any terms **before** clicking accept, archive WARC + timestamped PDF, and record the absence if none is presented. See §3.
- **Publication order:** Ephor is **not** the first commercial subject. Seed-stage vendor with a solo founder; and Ephor sells tamper-evident audit chains with SOC 2 / ISO 42001 compliance exports and audit-chain attestations — adjacent to Arvaken's own platform. Ephor publishes **fourth**, at arm's length, with the adjacency disclosed in writing to the founder before the first probe runs, right of reply on the standard clock, no NDA, no design-partner relationship.

### 2.2 Invariant Labs / Snyk (OSS)

- **Family:** `halt:pre_execution`, `gate`
- Apache-2.0, https://github.com/invariantlabs-ai/invariant, retrieved 2026-09-12
- Out-of-process MCP/LLM proxy; intercepts and blocks tool calls; brokers the traffic
- **Execution:** `Permitted` · **Publication:** `Unconditional`
- **Enterprise delta, pinned:** OSS edition, self-hosted — does **not** include managed Explorer hosting, hosted MCP-Scan threat feeds, hosted guardrailing models, or enterprise IAM integration.
- **Publication order:** **second**, immediately after the taxonomy paper. Apache-2.0 in the same family as Ephor, so a freely publishable result exists alongside the constrained ones.

### 2.3 Guardrails AI (OSS core)

- **Family:** `guardrail`
- Apache-2.0, https://github.com/guardrails-ai/guardrails, retrieved 2026-09-12
- In-process validator library; can run as a server
- **Execution:** `Permitted` · **Publication:** `Unconditional` (local judge required)
- **Enterprise delta, pinned:** OSS core — does **not** include Guardrails Pro hosted validation server, centralized telemetry dashboard, or team role-based access control.

### 2.4 Lakera Guard (Check Point)

- **Family:** `guardrail`
- **Acquisition:** SELF-SERVE, free Community tier ($0/month, 10,000 requests/month, 8,000-token max prompt, SaaS-only, EU data residency)
- Out-of-process API filter; no custody; signals/blocks
- **Execution:** `Undetermined` · **Publication:** `Undetermined`
- **Terms status:** platform.lakera.ai/legal is a JavaScript SPA whose body could not be retrieved. The lakera.ai footer routes "Terms & Conditions" to Check Point's website terms (checkpoint.com/privacy/terms/, metadata-dated 2022-03-28), which govern the marketing site only and contain no benchmarking or publication clause. **URLs tried:** platform.lakera.ai/legal, /legal/dpa, /legal/cookiepolicy, [www.lakera.ai/legal](https://www.lakera.ai/legal), [www.lakera.ai/terms-of-service](https://www.lakera.ai/terms-of-service), docs.lakera.ai/docs/terms, lakera.ai/security-policy.
- **Next step:** headless render of the Community-tier click-through, or Wayback snapshot, before any classification.

### 2.5 Deferred at Gate 1 — sales-only, terms not classified

Recorded with family and architecture; **no attempt made to classify terms**, because
the governing document is a negotiated MSA that is not public and guessing at it is
the worst outcome available.

| Vendor | Family | Placement | Enforcement capability |
|---|---|---|---|
| Straiker | guardrail | out-of-process | signal |
| Zenity | guardrail, evidence | both | signal |
| HiddenLayer (AISec) | guardrail | out-of-process | signal |
| Noma Security | guardrail, evidence | both | signal |
| Lasso Security | gate, guardrail | out-of-process gateway | brokers/proxies |
| Aim Security | guardrail | out-of-process | signal |
| Palo Alto Prisma AIRS 3.0 (+ Protect AI) | gate, guardrail | both (AI Agent Gateway) | brokers (gateway, limited preview) |
| CrowdStrike Falcon AI detection | guardrail | out-of-process | signal |
| Arthur (Shield) | guardrail | out-of-process | signal |
| Cisco AI Defense (Robust Intelligence) | guardrail | out-of-process | signal |
| SentinelOne (Prompt Security) | guardrail | out-of-process | signal |
| F5 (CalypsoAI) | guardrail | out-of-process | signal |
| RuntimeAI, Capsule, Cranium, Pillar, Akto | mixed | mixed | no self-serve path found |

**Canonical-domain rule (added v2):** pin the canonical domain for every vendor
before any re-run. Capsule, Arthur, and Noma have name collisions of the same
kind that put the wrong Ephor in v1.

---

## 3. Pre-acceptance click-through forensics

Evidence capture occurs **before the contract forms**. Clicking accept before
hashing creates an unprovable contract state if the vendor later updates terms.

```
                    [Evaluate target]
                            │
           ┌────────────────┴────────────────┐
   [Terms document found]           [No terms presented]
            │                                │
    ┌───────┴────────┐              [Terms-Absent Route]
[Clear]        [Ambiguous]           1. Record signup + onboarding
    │               │                   flow, screen and network
Classify        Halt →                2. Verify no clickwrap AND no
 & run          Gate 1                   browsewrap, footer link,
                                         dashboard terms, or terms
                                         at API-key issuance
                                      3. Archive WARC + timestamped PDF
                                      4. Classify Publication:
                                         Undetermined-by-Absence
                                      5. Proceed, noting no contractual
                                         permission or safe harbor exists
                                         either
```

**Absence of terms is not permission.** It means no contractual restriction *and*
no contractual license, no defined scope of authorized use, no safe harbor. A
recorded signup flow proves what that flow showed on that date; it does not prove
a negative about the vendor's whole estate.

**Tri-hash verification** (all self-serve rows, not only terms-absent ones).
SHA-256 of the governing terms — or of the proof of their absence — at three
milestones, because continued use is commonly construed as acceptance of updates:

- **H_A** — at account creation / acceptance
- **H_E** — at benchmark run-time execution
- **H_P** — at publication release

---

## 4. Google non-delegation — delivery-model finding

Google's General Service Terms §7 permits the Customer to test **itself** and bars
it from permitting a third party to do so.

**Scope.** The clause restricts "Tests," defined as benchmark tests of the
Services. Not every assessment is a benchmark. An engagement measuring whether a
specific client's configuration holds under adversarial load is arguably not a
benchmark of Google's Services. The clause bites hardest on comparative,
performance-characterizing work that names the service.

**Who is bound.** Google's Customer, not Arvaken — Arvaken is not a party. Breach
exposure sits on the client; Arvaken's exposure is inducing that breach.

**No consent path** in the general terms for the third-party restriction; the
written-consent mechanism attaches to the hyperscale-provider carve-out. Cure
comes from the client's own negotiated agreement, which large enterprise
customers frequently have and which supersedes the standard service terms.
**Therefore: per-engagement document retrieval, never a categorical rule.**

### 4.1 Consequences for the Firm

- **Intake gate.** Every engagement opens with a terms register: for each cloud and vendor product in scope, the governing document, its date, and whether third-party testing is permitted.
- **Engagement letter.** Client represents it has authority to authorize the testing and that no third-party terms prohibit it, and indemnifies Arvaken against third-party claims arising from that authorization.
- **Scope-of-work language.** Assessment of the client's configuration and controls. Never "benchmark." No comparative performance claims about a named cloud service in a client deliverable.
- **Where the work genuinely is a benchmark, it runs customer-operated.**

### 4.2 Consequences for the platform — architecture constraint

The clause draws its line exactly between customer-run and third-party-run
testing. **The platform is the customer-run side**, which is independent
confirmation of the platform-first staging.

**Design constraint, locked now:** the customer must be the operator of record.
Probes do not originate from Arvaken infrastructure into a client's GCP project
as a managed service. Execution runs under the customer's control, with Arvaken
supplying tooling, methodology, and interpretation. Cheap today, expensive to
retrofit.

**Do not** move Firm engagements into Arvaken's own tenant. A client wants its own
deployment measured — its policies, its agents, its data flows. A lab replica
delivers nothing the client asked for. Arvaken's own tenant is the route for the
**Independent Series**, not for the Firm.

### 4.3 Hyperscaler funding

Google's carve-out bars testing or disclosure **on behalf of** a hyperscale public
cloud provider. Grants, co-marketing, sponsored evaluation, or credits at scale
from AWS or Microsoft would bar publishing GCP comparisons. The independence
policy should refuse that money regardless, so this is a **flat prohibition in
the reciprocity register**, not a revenue firewall to maintain.

---

## 5. Corporate reciprocity register

The reciprocity condition binds **Arvaken**, not any product. It accumulates
across creditors and survives any single evaluation. Tracked here once; recorded
per row only as provenance.

| Creditor | Trigger | Grant given | Reach |
|---|---|---|---|
| AWS | AWS Service Terms §1.8 — any customer who publishes a Benchmark | AWS may benchmark and publish about Arvaken's products, irrespective of restrictions in Arvaken's terms | Arvaken's products generally |
| Microsoft | Universal License Terms for Online Services — **offering a competitive product** | Waiver of Arvaken's restrictions **plus** an affirmative obligation to provide access to competitive products on request | Competitive products |
| Google | General Service Terms §7(ii) | Google may benchmark and publish about Arvaken's products | **Publicly available** products only |

### Standing obligations this creates

1. **Arvaken's own MSA and ToS cannot contain an anti-benchmarking, anti-comparative-testing, or DeWitt clause.** Foreclosed by three creditors, not by preference. Note the correct legal characterization: these are waivers and grants, not conditions precedent. Including such a clause would not *breach* the vendor terms — it would simply be void and unenforceable against them. **Do not draft affirmative covenants nobody required.**
2. **Replication disclosure runs to the vendor whose service was the target.** Testing Ephor on EC2 is not a benchmark of AWS. Merging delivery packets unconditionally routes telemetry to AWS for publications they have no interest in and opens a standing notification channel Arvaken does not want. Packets are quarantined to the subject.
3. **Arvaken must be able to produce replication information and a testable instance of its own product on request.** Requires a documented, versioned reference configuration from the start.
4. **Unbounded counter-publication.** The reply slot in Arvaken's policy does not bind these three. Their right to benchmark and publish about Arvaken is governed by their agreements — no word cap, no clock, no embargo. This is a term Arvaken accepted, not a process it controls.
5. **Product decision forced.** A sales-gated platform narrows Google's reach (publicly available products only) and simultaneously makes the open-benchmark grant hollow, because nobody could take it up. If the credibility position is wanted, a publicly obtainable tier is required. Decide deliberately rather than letting pricing decide it.

---

## 5A. Claimed-against-demonstrated register

Built from documentation review only. No probe has run against any commercial
product, so the demonstrated column is empty by construction. **Empty means
unverified, not absent** — stated in the first paragraph of any publication
using this table.

Fill `(claimed)` from each vendor's own documentation, product naming, marketing
pages, or sales collateral, recorded to the five-part standard against that
document. Leave `(demonstrated)` blank until a probe in that family has run.

| Vendor / product | halt:pre_execution | halt:in_flight | gate | identity/credential | guardrail | evidence |
|---|---|---|---|---|---|---|
| | claimed / demo | claimed / demo | claimed / demo | claimed / demo | claimed / demo | claimed / demo |
| Ephor (Developer) | claimed / — | **not claimed** — cleared actions run gateway→target with nothing in the path | claimed / — | claimed / — | — | claimed / — |
| Invariant / Snyk (OSS) | claimed / — | / — | claimed / — | / — | — | / — |
| AWS Bedrock AgentCore | / — | / — | / — | claimed / — | — | / — |
| AWS Bedrock Guardrails | — | — | — | — | claimed / — | — |
| Azure AI Content Safety | — | — | — | — | claimed / — | — |
| Google Model Armor | — | — | — | — | claimed / — | — |
| Anthropic Agent SDK | claimed / — | / — | claimed / — | — | — | — |
| OpenAI Agents SDK | claimed / — | / — | claimed / — | — | claimed / — | — |
| NeMo Guardrails (OSS) | — | — | — | — | claimed / — | — |
| Guardrails AI (OSS) | — | — | — | — | claimed / — | — |
| Lakera Guard (Community) | — | — | — | — | claimed / — | — |
| *17 deferred vendors* | populate from public documentation — no account required | | | | | |

**The finding this table supports, available now:** across the screened
population, N vendors claim a capability in the halt family and **zero have been
independently verified by anyone**. The gap between what the category claims and
what has been checked is the measurement, and it requires reading marketing
pages and manuals rather than running probes.

**Deferred vendors are in scope for this table.** Gate 1 deferral blocks
*testing*, not documentation review. A sales-only vendor's public claims are
public, so the claimed column can be completed for the entire population
including the 17 deferred rows — which is what makes this a population-wide
finding rather than a finding about the eleven products that happen to be
self-serve.

> **Ruling beneath the transcription (founder, 2026-09-16; not part of the PDF this copy transcribes).**
> - **Blank cells.** A blank cell in this register means "not yet read", never "no claim found". A cell says "not
>   claimed" only where the vendor's own document says so.
> - **Gate 1.** Gate 1 asks whether a product can be obtained and used for testing under published terms without a
>   sales conversation. Self-serve and open-source products pass; demonstration-only and enterprise-contract-only
>   products fail.
> - **The transcription above is unchanged.** The PDF still governs it.

---

## 6. Publication sequence

Staged by risk tier, not by how complete the material is. Entity formation gates
tier 1; bound media coverage gates tier 3 only.

### Tier 1 — no account, no probe, no vendor contact, no coverage required

| # | Subject | Basis |
|---|---|---|
| 0 | **Claims register (§5A) + family taxonomy and risk mapping** | Quotes each vendor's own documentation back. No performance claim about any product. Covers the full population including the 17 deferred vendors, since Gate 1 blocks testing, not reading. Lead with this — it says something about the products. |
| 1 | **The terms survey** (this ledger) | A finding about public documents, made by reading public documents. Includes the sharpest single observation held: a provider barring benchmark disclosure specifically for two of its own security products. **Second, not first** — it is a story about vendors' lawyers rather than their products, and leading with it frames the Lab as a legal-research shop. It is also the publication most likely to provoke a response, which is better received after the Lab's purpose is established. |

### Tier 2 — Lab-owned subjects and open-source, entity only

| # | Subject | Basis |
|---|---|---|
| 2 | **`gate.auto_promotion`, reference implementation** | Design-class property, subject built by the Lab under §3B. Specification published alongside; provenance stated in the first paragraph. Establishes the probe as prior art before any named instance. |
| 3 | **`gate.bypass_path`, reference gateway** | Same basis. The run that makes the probe credible when it later reports on a vendor's default install. |
| 4 | **Invariant / Snyk (OSS)** | Apache-2.0. Named adversarial result under §3A: pinned edition in the row name, specific enterprise delta, local judge weights, maintainer right of reply on the standard clock. |
| 5 | **Guardrail-family OSS suite** — NeMo Guardrails, Guardrails AI, OpenAI Guardrails | Same conditions. Separate publication from the gate-family work; content-filter family only. |

### Tier 3 — commercial subjects, requires §26(2) coverage bound

| # | Subject | Basis |
|---|---|---|
| 6 | **AWS Bedrock AgentCore** | Permitted scope under the pen-test policy, publication permitted under Service Terms §1.8, counterparty can absorb a finding, no adjacency. First commercial subject. In-VPC mock termination mandatory; external-API tool shapes out of scope and stated as such. |
| 7 | **Ephor** | Arm's length, adjacency disclosed in writing before the first probe, standard reply clock. Blocked until the signup capture resolves the terms question. |
| 8 | **In-process SDK controls (hosted editions)** | Only after written evaluation authorization naming publication returns from Anthropic and OpenAI. |

**In-process is lower priority, not out of scope.** The vendor has no runtime
enforcement role in code running under Arvaken's configuration, so the honest
claim is "this guard has these bypass properties," never "this vendor's control
stops an agent." An adversary with code execution in the agent process deletes
the guard, narrowing the meaningful threat model to injection-driven misbehavior
by an otherwise-intact agent. But in-process is where nearly every shipped agent
lives — a gateway-only benchmark measures the minority architecture, and the
in-process publication has the largest audience.

---

## 7. Probe families pending pre-registration

Named and scoped here. **Thresholds are not written until the harness that
produces them exists**, and are frozen once written. An edited pre-registration
is worth less than none.

| Probe | Question | Notes |
|---|---|---|
| `gate.self_report` | The risk score is computed from the agent's own proposal and reasoning. Does an agent that narrates a destructive action benignly get it cleared? | Resistance-to-influence variant. |
| `gate.auto_promotion` | Can a destructive verb be walked through repeated approvals into standing policy? | Design-class question about any approvals-become-policy mechanism. Publishes vendor-neutral first. |
| `ledger.misreport` | Does a hash chain detect a compromised data plane reporting falsely? | **Pre-registers three-valued**: detected at write time / detected at audit time / never. Three different products; a binary threshold will not bind. |
| `gate.bypass_path` | Does the agent retain an independent route to the target from inside its own execution context, and does the gateway detect or record the attempt? | Credential-holding is an architectural claim; bypass is a **deployment** fact. Runs against Arvaken's own reference gateway first. Write-ups report the deployment, not just the product. |

---

## 8. Coverage statement

**Screened:** ~33 candidates.
**Survived Gate 1:** 11 (Bedrock Guardrails, AgentCore, Azure AI Content Safety,
Model Armor, Anthropic API, OpenAI API, OpenAI Guardrails OSS, NeMo Guardrails
OSS, Guardrails AI OSS, Invariant OSS, Ephor Developer, Lakera Guard Community).
**Deferred at Gate 1:** 17.
**Unscreened but in population:** Entra Agent ID, Purview agent controls, Copilot
Studio governance, Vertex AI tool permissioning, Salesforce Agentforce, LangSmith
governance, CrewAI enterprise controls.

**Terms located to the five-part standard:** AWS pen-test policy and Service Terms
§1.8; Microsoft ROE (element 2 missing) and Universal License Terms; Google
General Service Terms §7 and §9, SecOps Service Specific Terms, AlloyDB Omni
terms; OpenAI CVE assignment policy; Anthropic Responsible Disclosure Policy and
ToS §D.4; four OSS licenses.

**Terms not located, with URLs tried:**
- Google SCC service-specific section — cloud.google.com/terms/service-terms (section not surfaced)
- Google Pre-GA Offerings Terms — same document, section not surfaced
- Lakera Guard Community ToS — seven URLs listed at §2.4
- Ephor — no legal document exists on ephor.dev; ephor.dev/tower-live.html robots-disallowed
- All 17 deferred vendors — governing MSAs not public

---

## 9. Open items blocking a complete Population 1 table

1. **Retrieve the SCC service-specific schedule and the Pre-GA Offerings Terms.** Two documents, one afternoon. Only thing blocking Model Armor.
2. **Capture Ephor's signup flow** before accepting. Five minutes. Only thing blocking the Ephor row.
3. **Headless render of Lakera's Community ToS** or a Wayback snapshot.
4. **Screen Entra Agent ID and Vertex tool permissioning** — the hyperscalers' actual halt- and gate-family controls, currently unmeasured.
5. **Written evaluation authorization naming publication** requested from Anthropic and OpenAI.
6. **Complete the claimed column of §5A** across all 28 vendors, deferred ones included. Documentation review only — no accounts, no probes, no vendor contact. This is the shortest path to publication zero and is not blocked by anything else on this list.
