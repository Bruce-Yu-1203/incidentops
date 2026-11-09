# ADR-011: Local UI authentication and identity seeding

- **Status:** Accepted (Phase 0 default)
- **Resolves:** architecture.md §17 question 4

## Context

M1 needs real server-side authorization for approvers but no internet exposure.

## Decision

- Humans authenticate against the FastAPI control plane with a username and
  password. Passwords are hashed with `hashlib.scrypt` (standard library).
- Successful login creates an opaque random session token stored **hashed** in
  Control PostgreSQL with an expiry; the browser receives it as an `HttpOnly`,
  `SameSite=Strict` cookie. The Next.js UI never makes authorization decisions.
- Roles are `viewer`, `operator`, `approver`, `administrator`, enforced in API
  dependencies. Separation of duties (proposer ≠ approver) is enforced in the
  approval service.
- Local and CI identities are created by an explicit seed command that reads
  passwords from environment variables. No default passwords exist in code.
- Service identities (Alertmanager, executor, verifier, agent, benchmark
  controller) use separate bearer tokens, one per process.

## Consequences

Before any internet exposure, replace local passwords with a maintained identity
provider (OIDC) behind the same role model.
