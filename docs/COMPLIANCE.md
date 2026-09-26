# Compliance Readiness

`justfastllm` ships privacy and security controls that help an operator run the gateway in a GDPR-ready, SOC 2-ready, and India DPDP-ready posture. Some teams say DDPA when referring to Indian digital data protection work; this project maps that shorthand to India's DPDP framework. The software can enforce technical controls automatically; legal compliance still depends on the operator's processing purposes, notices, customer contracts, subprocessors, transfer mechanisms, incident process, and audit evidence.

This guide is engineering documentation, not legal advice.

## Source Frameworks

The controls in this guide are mapped to:

- EU GDPR principles, subject rights, security, processor duties, and privacy by design/default.
- European Data Protection Board guidance on privacy by design/default and controller/processor accountability.
- AICPA SOC 2 Trust Services Criteria for security, availability, processing integrity, confidentiality, and privacy.
- India's Digital Personal Data Protection Act, 2023 and Digital Personal Data Protection Rules, 2025, including notice, consent, rights, erasure, security safeguards, and grievance workflows.

Primary references checked for this control map on 2026-09-26:

- GDPR text: https://eur-lex.europa.eu/eli/reg/2016/679/oj
- EDPB privacy by design/default: https://www.edpb.europa.eu/topics/ai-and-technology/privacy-by-design-and-by-default_en
- EDPB data protection by design/default summary: https://www.edpb.europa.eu/system/files/2026-02/edpb-summary-gdpr-data-protection-design-default_en.pdf
- EDPB data subject rights: https://www.edpb.europa.eu/topics/key-gdpr-concepts/data-subject-rights_en
- EDPB small-business compliance guide: https://www.edpb.europa.eu/sme/be-compliant/be-compliant_en
- AICPA SOC suite and Trust Services Criteria: https://www.aicpa-cima.com/resources/landing/system-and-organization-controls-soc-suite-of-services
- AICPA Trust Services Criteria PDF: https://assets.ctfassets.net/rb9cdnjh59cm/72xv4p67HVXKp6CjWmjkPk/1cdbfa19f6307e2720396b66a6194dc9/trust-services-criteria-updated-copyright.pdf
- MeitY DPDP Act, 2023 page: https://www.meity.gov.in/digital-personal-data-protection-act-2023-4
- MeitY DPDP Act, 2023 PDF: https://www.meity.gov.in/static/uploads/2024/02/Digital-Personal-Data-Protection-Act-2023.pdf
- MeitY DPDP Rules, 2025: https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa?pageTitle=Digital-Personal-Data-Protection-Rules-2025
- MeitY DPDP Rules, 2025 Gazette PDF: https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf

The runtime endpoints also expose these source categories through `framework_sources` in `/v1/compliance/status`, `/v1/compliance/evidence`, and `/v1/compliance/report`.

## Automatic Controls

These controls are enforced by the deployed gateway.

| Control | Implementation |
| --- | --- |
| Admin authentication | `JUSTFASTLLM_MASTER_KEY` protects control-plane, metrics, audit, auth-event, privacy, and compliance endpoints. |
| Scoped application credentials | Virtual keys and service-account keys support model access, route access, budgets, RPM limits, TPM limits, expiration, disable, and deletion. |
| Data minimization | Request events store usage, model, provider, cost, latency, user ID, team ID, tags, and key preview; they do not store prompts or completions. |
| Cache minimization | Compliance mode disables response-cache storage by default. Operators must set `JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED=true` before prompts/responses can be cached. When caching is enabled, user, team, and virtual-key cache scope values are hashed into cache keys so identical prompts from different users or applications do not share cached responses. |
| Secret minimization | Provider keys and master key can use `env:` and `file:` secret references. |
| Durable evidence storage | Control-plane keys, users, teams, spend, usage, access, auth, audit, privacy requests, erasure evidence, user memory, and optional response-cache entries persist to Postgres, MySQL, MSSQL, MongoDB, Redis, or atomic JSON file snapshots depending on backend configuration. Use encrypted disks or volumes when file snapshots hold regulated data. |
| Endpoint access evidence | Every HTTP request records a sanitized access event with request ID, method, path, status, latency, auth context, key preview, user ID, and team ID. Access events are saved immediately to the configured durable control-plane backend. |
| Strict database evidence writes | In compliance mode, database-backed control-plane, user-memory, and enabled response-cache stores raise storage failures instead of silently dropping evidence. |
| Supported production database families | `/health` and `/v1/compliance/status` report configured database families and driver dependencies, and fail readiness for unsupported enabled database URLs outside Postgres, MySQL, MSSQL, and MongoDB. |
| Database transport encryption gate | `/health` and `/v1/compliance/status` report database transport-security indicators and fail readiness for enabled remote database URLs that do not explicitly request encrypted transport. Local database URLs pass for development and CI. |
| Retention | `JUSTFASTLLM_USAGE_RETENTION_DAYS` prunes usage records; `JUSTFASTLLM_AUDIT_RETENTION_DAYS` prunes audit, endpoint access, and authentication records automatically. Pruned evidence is written back to the configured durable control-plane backend. |
| Consent ledger | `POST /v1/privacy/consents`, `GET /v1/privacy/consents`, `PATCH /v1/privacy/consents/{consent_id}`, and `POST /v1/privacy/consents/{consent_id}/withdraw` track consent grant, notice version, purpose, lawful basis, source, status, withdrawal timestamp, and metadata. |
| Privacy request register | `POST /v1/privacy/requests`, `GET /v1/privacy/requests`, and `PATCH /v1/privacy/requests/{request_id}` track request type, status, due date, completion, notes, and metadata for access, erasure, correction, restriction, objection, portability, withdrawal, grievance, nomination, and appeal workflows. Invalid request types and statuses are rejected instead of silently reclassified. |
| Subject access | `GET /v1/privacy/users/{user_id}/export` exports user-linked user records, keys, usage events, endpoint access references, auth references tied through payload, virtual keys, or `X-User-ID`, audit target and actor references, consent records, preferences, and feedback. |
| Subject erasure | `DELETE /v1/privacy/users/{user_id}/erase` deletes user records, keys, usage events tied through payload, virtual keys, or `X-User-ID`, preferences, and feedback, clears response-cache entries under the gateway cache prefix, and pseudonymizes audit target and actor references, endpoint access, auth, privacy-request references, and consent records. |
| User memory control | Preferences and feedback can be read, updated, exported, and erased per user. When gateway auth is configured, memory endpoints require a master key or a virtual key scoped to the same user. |
| Auditability | Administrative creates, updates, deletes, erasures, runtime config reloads, guardrail changes, preference writes, and feedback writes are audit logged with sanitized actor context such as `master`, `virtual_key:{preview}`, `user:{id}`, or `anonymous`. |
| Endpoint access API | `/v1/access` exposes compact method, path, status, latency, request ID, auth context, and timestamp evidence without storing bodies, prompts, completions, or headers. |
| Authentication evidence | `/v1/auth/events` records sanitized auth type, outcome, reason, route, model, key preview, user ID, team ID, and timestamp without storing raw tokens, headers, bodies, prompts, or completions. |
| Logging hygiene | Request logs sanitize authorization, cookies, API keys, bearer tokens, and common email patterns. Unhandled exception responses and logs use generic messages and exception classes rather than raw exception text. |
| Policy enforcement | Provider allow-lists, denied models, required tags, prompt-token ceilings, and response block patterns run at runtime. |
| Guardrails | Required message structure, message length, and block-pattern checks run before upstream provider calls. |
| CORS control | Browser dashboard access can be restricted with `JUSTFASTLLM_CORS_ALLOW_ORIGIN`. |
| Compliance status | `GET /v1/compliance/status` exposes runtime controls, retention windows, contact metadata, and remaining operator tasks. |
| Evidence map | `GET /v1/compliance/evidence` returns machine-readable control mappings for automated and operator-owned evidence. |
| Evidence integrity | `GET /v1/compliance/integrity` returns counts, SHA-256 digests, and tamper-evident hash-chain validation for sanitized audit, endpoint access, and authentication evidence so release and audit exports can be matched later. |
| Deployment report | `GET /v1/compliance/report` returns pass/fail readiness checks, implemented controls, evidence endpoints, and non-automatable legal/audit gates. |
| Deploy-time gate | `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true` makes `/health` return `503` until required privacy/security settings are present, including durable control-plane storage, a real control-plane evidence write with matching readback, write/read verified durable enabled user memory, write/read verified enabled response-cache storage, retention, contacts, DPA/subprocessor URLs, CORS, and cache minimization. |

## Required Deployment Settings

Set these before handling production personal data.

```bash
JUSTFASTLLM_MASTER_KEY=env:GATEWAY_MASTER_KEY
JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database
JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=postgresql+psycopg://user:pass@db.example.com:5432/justfastllm?sslmode=require
JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE=justfastllm_control_plane_state
JUSTFASTLLM_COMPLIANCE_MODE=true
JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED=false
JUSTFASTLLM_USAGE_RETENTION_DAYS=90
JUSTFASTLLM_AUDIT_RETENTION_DAYS=365
JUSTFASTLLM_PRIVACY_CONTACT=privacy@example.com
JUSTFASTLLM_SECURITY_CONTACT=security@example.com
JUSTFASTLLM_SUBPROCESSORS_URL=https://example.com/subprocessors
JUSTFASTLLM_DPA_URL=https://example.com/dpa
JUSTFASTLLM_CORS_ALLOW_ORIGIN=https://dashboard.example.com
```

Use shorter usage retention where possible. Keep audit retention aligned with customer contracts, incident-response needs, and audit evidence requirements. Endpoint access evidence and authentication evidence use the audit retention window.

## GDPR Readiness Map

| GDPR area | Gateway support | Operator evidence still required |
| --- | --- | --- |
| Lawfulness, fairness, transparency | Compliance status exposes contacts and DPA/subprocessor URLs. | Privacy notice, lawful basis, customer instructions, consent where required. |
| Purpose limitation | Required tags, model/provider policies, and route-scoped keys help bind traffic to approved purposes. | Record of processing activities and purpose definitions. |
| Data minimization | Usage events avoid prompt/response persistence; route and model controls reduce unnecessary processing. | Application-layer minimization and prompt design. |
| Accuracy | User records and metadata can be updated. | Process for correcting source-system personal data. |
| Storage limitation | Automatic retention pruning for usage, audit, endpoint access, and authentication records. | Retention schedule covering provider logs, app logs, backups, and warehouses. |
| Integrity and confidentiality | Master key, virtual keys, secret references, sanitization, budgets, and rate limits. | Infrastructure encryption, SSO, network controls, employee access management. |
| Accountability | Durable control-plane evidence, access events, auth events, audit logs, OpenAPI contract, compliance status, tests, and documented runbooks. | DPIAs, policies, training, vendor reviews, signed processor terms. |
| Data subject rights | Privacy request register, export and erasure APIs, and memory export/delete. | Identity verification, appeal/escalation handling, and response communication. |
| Processor obligations | DPA URL, subprocessor URL, audit evidence, technical controls. | Signed data-processing terms and customer-specific instructions. |
| Breach readiness | Security contact and audit trail. | Breach detection, legal assessment, notification workflow, incident records. |

Runtime evidence now maps controls to specific requirement families such as GDPR Articles 5, 12, 15-18, 20, 21, 24, 25, 28, 30, 32, 33, and 34. Use those mappings as a release-evidence index, not as a legal opinion.

## SOC 2 Readiness Map

SOC 2 compliance is not automatic. A SOC 2 report requires an independent CPA examination over the operator's system, controls, evidence, and period of operation. `justfastllm` provides control support and evidence surfaces for that audit.

| Trust Services area | Gateway support |
| --- | --- |
| Security | Master key, virtual keys, route/model access, sanitized logs, secret references, guardrails, policies. |
| Availability | Health endpoint, provider health, alerts, fallbacks, traffic mirroring, deployment descriptors. |
| Processing integrity | OpenAPI contract, deterministic tests, request validation, durable endpoint access events, durable auth events, durable usage records, pricing table, audit logs. |
| Confidentiality | No prompt persistence in usage logs, header redaction, secret references, admin isolation. |
| Privacy | Data minimization, retention, subject export, erasure, memory delete, compliance status. |

Recommended evidence exports:

```bash
curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/compliance/status

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/compliance/evidence

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/compliance/integrity

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/compliance/report

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/audit

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/access

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/proxy/features
```

## India DPDP Readiness Map

| DPDP area | Gateway support | Operator evidence still required |
| --- | --- | --- |
| Notice and consent | DPA/subprocessor/contact metadata can be exposed through compliance status. | Clear notice, consent capture, consent withdrawal workflow where consent is the basis. |
| Data Principal rights | Privacy request register plus export and erase endpoints support access, erasure, withdrawal, grievance, nomination, appeal, and related rights workflows. | Identity verification, grievance redressal owner, response workflow, nomination handling when applicable. |
| Purpose limitation | Policies, required tags, scoped keys, provider controls. | Purpose statements and internal approval for each processing activity. |
| Security safeguards | Auth, redaction, secret references, audit logs, rate limits. | Infrastructure safeguards, employee access controls, monitoring, incident process. |
| Breach intimation readiness | Security contact, audit logs, usage records. | Board and affected-person notification workflow and legal assessment. |
| Processor governance | Subprocessor URL and DPA URL fields. | Processor contracts, cross-border transfer review, vendor inventory. |
| Erasure when purpose is complete | Retention pruning and erasure API. | Retention schedule across all connected systems and backups. |

Where existing customer materials use `DDPA`, treat it as an internal naming alias and publish the external legal reference as India's Digital Personal Data Protection Act, 2023 and DPDP Rules, 2025.

## Evidence Payload Fields

The compliance endpoints intentionally separate automated technical controls from operator-owned controls:

- `framework_sources`: official source families and URLs used by the gateway evidence map.
- `mapped_requirements`: the statutory, framework, or contract requirement family each control supports.
- `operator_responsibilities`: actions that must be completed outside the gateway, such as notices, identity verification, signed DPAs, breach workflow, cloud encryption, access reviews, SOC 2 examination, backup retention, provider retention, and vendor governance.
- `assurance.can_claim_automatic_legal_or_attested_compliance`: always `false`; the correct production claim is readiness support, not automatic legal or SOC 2 attestation.

## Data-Subject And Data-Principal Workflows

`GET /v1/compliance/status` includes a `privacy_requests` summary with total requests, open or in-progress requests, overdue open requests, requests due within seven days, counts by request type, counts by status, and supported request/status values. Use this summary for release evidence and operational review; use `GET /v1/privacy/requests` for item-level case handling.

Export:

```bash
curl -X POST \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-123","request_type":"access","source":"support","notes":"identity verified"}' \
  https://gateway.example.com/v1/privacy/requests

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/privacy/users/user-123/export
```

Erase:

```bash
curl -X DELETE \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/privacy/users/user-123/erase
```

Erasure deletes the user spend bucket, attached virtual keys, usage events, preferences, feedback, and response-cache entries under the gateway cache prefix. Audit, auth, and privacy-request references are pseudonymized instead of fully removed so the operator retains security and rights-request evidence without retaining the user's identifier in those entries.

## Operator Checklist

Before claiming compliance for a deployed instance:

- Confirm `JUSTFASTLLM_MASTER_KEY` is set and admin endpoints are private.
- Confirm retention windows match the published retention schedule.
- Confirm provider accounts and upstream model providers are listed in subprocessors where required.
- Confirm customer contracts include appropriate data-processing terms.
- Start from [Data Processing Addendum Template](DPA_TEMPLATE.md) when drafting gateway-specific customer terms with counsel.
- Confirm privacy notice explains LLM processing, providers, purposes, retention, and rights.
- Confirm DSR and grievance workflows use the export and erasure endpoints.
- Export `/v1/compliance/report` and `/v1/compliance/integrity` after deployment and store them with release or audit evidence. Confirm the `hash_chain.valid` fields for `audit_events`, `access_events`, and `auth_events` are `true`.
- Confirm logs, backups, dashboards, provider consoles, and downstream analytics follow the same retention and erasure policy.
- Confirm incident-response runbooks include breach notification assessment and evidence retention.
- Confirm SOC 2 evidence collection is owned by an auditor-ready control owner.

## Non-Automatable Requirements

The gateway cannot automatically complete these obligations:

- Legal determination of controller, processor, Data Fiduciary, or Data Processor role.
- Lawful basis selection, consent design, or notice language.
- Signed customer DPA or India processing terms.
- Subprocessor approval and transfer-impact assessment.
- Independent SOC 2 Type 1 or Type 2 audit.
- Employee background checks, access reviews, security training, or HR controls.
- Cloud account encryption, SSO, SIEM, vulnerability management, or physical security controls outside the gateway.

The correct claim is: `justfastllm` provides deployable technical controls and evidence surfaces for privacy and SOC 2 readiness. The deploying organization must complete the organizational, contractual, and audit requirements before claiming legal or attested compliance.
