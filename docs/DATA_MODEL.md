# Target Data Model

This document fixes ownership and lifecycle. Exact columns may evolve, but the separation between product truth and execution state is deliberate.

## `product` schema

Suggested core entities:

| Entity | Purpose | Lifecycle |
| --- | --- | --- |
| `tenant` | Product tenant/account boundary | Durable |
| `user` | Product user | Durable |
| `identity` | External identity mapping such as Feishu/OIDC | Durable, provider-specific |
| `document` | Logical document | Durable |
| `document_version` | Exact immutable bytes used by audits | Append-only |
| `rule_profile` | Versioned rule selection/configuration | Versioned |
| `audit_round` | One accepted product audit request | Durable, idempotent |
| `audit_result` | Result revision produced for an audit round | Append-only |
| `audit_finding` | Rule-level PASS/NG/ERR and evidence | Append-only with result revision |
| `review` | Human review/override commentary | Append-only |
| `usage_event` | Local usage/cost/point history | Append-only |

An audit round is a product concept. Its identity must not change because execution retries.

## `engine` schema

Suggested execution entities:

| Entity | Purpose | Lifecycle |
| --- | --- | --- |
| `job` | Durable unit of engine work linked to one audit round | Retryable |
| `job_attempt` | One execution attempt | Append-only |
| `worker_lease` or job lease fields | Claim owner and expiry | Ephemeral/recoverable |
| `execution_event` | Optional operational trace/diagnostic events | Retention-controlled |

Do not put user review or the authoritative product result only in `engine`.

## Atomic acceptance

Accepting an audit must create product truth and executable work in one database transaction:

~~~text
BEGIN
  verify idempotency key
  create/read document_version
  insert product.audit_round
  insert engine.job linked to audit_round
COMMIT
~~~

This avoids an accepted audit with no work, or a job with no product owner.

## Idempotency

Product mutation endpoints use an idempotency key scoped at least by tenant + operation. A duplicate request returns the existing product object rather than creating a second audit round.

## Result revision

Worker retries must not overwrite previous evidence.

~~~text
audit_round A
  engine job J
    attempt 1 -> result revision 1 (ERR)
    attempt 2 -> result revision 2 (PASS/NG findings)
~~~

The product may choose which revision is current, but history remains queryable.

## Immutable file identity

Each `document_version` records at least:

- object key
- SHA-256
- byte size
- MIME type / source format
- source provider (`upload`, `feishu`, migration, etc.)
- source file token/revision when available
- created time and actor

Recommended object-key shape:

~~~text
tenant/{tenant_id}/documents/{document_id}/{version_id}/original.ext
~~~

Object keys are never overwritten.

## Evidence chain

Every historical result must be traceable through:

~~~text
audit_round
 -> document_version
 -> immutable object key + SHA-256
 -> rule_profile version
 -> engine job + attempt
 -> engine build/git SHA
 -> audit_result revision
 -> audit_finding/evidence
 -> optional human review
~~~

If this chain is broken, the product cannot reliably explain what bytes and rules produced a historical result.
