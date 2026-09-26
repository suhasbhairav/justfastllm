# Data Processing Addendum Template

This template is a starting point for an operator deploying `justfastllm`. It is not legal advice and should be reviewed by counsel before use with customers, vendors, or employees.

## Parties

- Customer / Controller / Data Fiduciary: `[customer legal name]`
- Operator / Processor / Data Processor: `[operator legal name]`
- Service: `justfastllm` LLM gateway deployment at `[deployment URL or environment]`
- Effective date: `[date]`

## Processing Instructions

The operator processes personal data only to provide, secure, monitor, support, and improve the gateway according to the customer's documented instructions.

The customer is responsible for establishing the lawful basis, consent where required, notice, and permitted purposes for prompts, files, metadata, user IDs, and feedback submitted to the gateway.

## Categories Of Personal Data

Depending on customer usage, personal data may include:

- User IDs, team IDs, email addresses, workspace identifiers, and metadata supplied by the customer.
- Prompt, response, embedding, audio, image, moderation, rerank, feedback, and preference content sent through the gateway.
- Usage metadata such as model, provider, route, token counts, cost, latency, key preview, request tags, and timestamps.
- Administrative audit records for keys, users, teams, erasure, configuration, and control-plane actions.

## Categories Of Data Subjects

Depending on customer usage:

- Customer employees, contractors, and service accounts.
- Customer end users.
- Customer prospects, support contacts, or other people whose data is included in prompts or files.

## Processing Purposes

Permitted purposes:

- Route LLM requests to configured providers.
- Enforce authentication, authorization, budgets, rate limits, model access, route access, guardrails, and policies.
- Record usage, spend, latency, provider health, alerts, and audit evidence.
- Store user preferences and feedback when enabled.
- Support data export, erasure, compliance evidence, and incident investigation.

The operator must not process personal data for unrelated advertising, sale, profiling, or model training unless explicitly authorized in a separate written instruction.

## Subprocessors

The operator must maintain a current subprocessor list at:

`[JUSTFASTLLM_SUBPROCESSORS_URL]`

Potential subprocessors may include:

- Cloud hosting provider.
- Managed Redis or database provider.
- Log, monitoring, and security tooling.
- Upstream LLM model providers configured by the customer or operator.
- Email, ticketing, or incident-response tooling used for support.

The operator must notify the customer of material subprocessor changes according to the customer contract.

## International Transfers

The operator must document cross-border transfers involving:

- Hosting region.
- Upstream LLM provider region.
- Log and monitoring region.
- Support and incident-response access locations.

Where required, the operator and customer should use appropriate transfer mechanisms, assessments, and supplementary safeguards.

## Security Measures

The operator should enable and maintain:

- `JUSTFASTLLM_MASTER_KEY`.
- Scoped virtual keys or service-account keys for every application.
- Model allow-lists, route allow-lists, budgets, RPM limits, and TPM limits.
- `JUSTFASTLLM_COMPLIANCE_MODE=true`.
- `JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED=false` unless approved by policy.
- `JUSTFASTLLM_USAGE_RETENTION_DAYS` and `JUSTFASTLLM_AUDIT_RETENTION_DAYS`.
- Private dashboard access through SSO, VPN, private network, or equivalent.
- HTTPS at the edge.
- Encrypted storage for control-plane snapshots, Redis, logs, and backups.
- Secret references through `env:` or `file:` instead of raw config secrets.
- Centralized logging with sanitized headers.
- Regular key rotation and access review.

## Assistance With Rights Requests

The operator will reasonably assist with access, correction, deletion, portability, withdrawal, objection, grievance, and similar requests by using gateway controls including:

```bash
GET /v1/privacy/users/{user_id}/export
DELETE /v1/privacy/users/{user_id}/erase
```

The customer remains responsible for validating requester identity and deciding whether a request is legally valid.

## Deletion And Return

Upon termination or valid erasure instruction, the operator should delete or return personal data in gateway-controlled stores unless retention is required by law, security, dispute, audit, or backup policy.

Gateway erasure deletes user records, attached virtual keys, usage events, preferences, and feedback. Audit, auth, and privacy-request references are pseudonymized to preserve security and rights-request evidence without retaining the user's identifier in those entries.

## Breach Notification

The operator should notify the customer without undue delay after confirming a personal-data breach affecting the gateway and provide available information about:

- Nature of the incident.
- Categories and approximate volume of affected data.
- Affected systems, providers, or subprocessors.
- Mitigation steps.
- Recommended customer actions.
- Point of contact for incident coordination.

Operational security contact:

`[JUSTFASTLLM_SECURITY_CONTACT]`

## Audit And Evidence

The operator can provide gateway evidence from:

```bash
GET /v1/compliance/status
GET /v1/compliance/evidence
GET /v1/compliance/integrity
GET /v1/audit
GET /v1/proxy/features
GET /openapi.json
```

SOC 2 reports, penetration-test reports, employee access reviews, cloud configuration evidence, and vendor due diligence are operator-level artifacts and are not generated automatically by the gateway.

## Retention

Recommended defaults:

- Usage events: 90 days or less.
- Audit events: 365 days or contract/audit-required duration.
- Response cache: disabled for personal data unless explicitly approved.
- Backups: aligned to documented retention and erasure policy.

Configured runtime values are available from:

```bash
GET /v1/compliance/status
```

## Customer Responsibilities

The customer should:

- Provide lawful processing instructions.
- Avoid sending unnecessary personal data.
- Maintain notices and consent flows.
- Configure approved providers and regions.
- Review subprocessors.
- Verify rights-request identity.
- Decide whether erasure exceptions apply.
- Ensure application logs and downstream systems honor the same retention and erasure outcomes.

## Operator Responsibilities

The operator should:

- Maintain security controls described in this addendum.
- Follow documented customer instructions.
- Keep subprocessor and DPA URLs current.
- Preserve audit evidence.
- Assist with rights requests and incidents.
- Review access and rotate credentials.
- Maintain deployment, backup, monitoring, and vulnerability-management controls.

## Signatures

Customer:

Name:

Title:

Date:

Operator:

Name:

Title:

Date:
