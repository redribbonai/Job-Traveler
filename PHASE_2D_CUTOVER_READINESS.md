# Phase 2D cutover readiness

## Implemented

- The desktop client has an injected employee-session manager using the existing
  ShopOS username/PIN login, current-session validation, logout, and one
  OS-keyring credential slot. PINs and bearer tokens are process/keyring values
  only; no plaintext token file or local fallback is used.
- Service mode displays the signed-in employee and provides Switch Employee and
  Sign Out. Local compatibility mode remains the default and retains its prior
  persistence and workflow behavior.
- `job_planner` is a normalized capability granted only by the existing
  management-password-protected local account utility. Planner-only create and
  operation-plan resize controls are capability-gated in both clients and are
  rechecked by the server on every request.
- Service persistence uses typed, versioned, idempotent create and resize
  commands. Ordinary allowlisted field edits remain available to approved
  employees; protected status, quantity, dimensions, and plan fields are not
  smuggled through ordinary saves.
- Structural mutation and general traveler mutation gates remain disabled by
  default in every deployment configuration.

## Rehearsed / tested only

The Phase 2D backend tests exercise temporary SQLite/authentication state,
sanitized travelers, fake transports, capability grant/revoke, current-session
projection, create and resize replay/conflict/confirmation behavior, and
feature-gate denial. Desktop session tests use an injected fake credential store.
The repository suites are the authoritative automated rehearsal; no production
database, jobs directory, keyring, certificate, proxy, DNS, or network setting
is used by those tests.

## Still prohibited

No production migration, feature-gate enablement, desktop cutover, mobile code,
employee-administration HTTP API, refresh-token architecture, broad role
hierarchy, status/closure workflow, Parts Count mutation, inspection-dimension
mutation, audit timeline, or live-update channel is included.

## Future maintenance-window sequence

1. Take and verify one coordinated JSON/SQLite backup gate and retain the
   rollback copy outside the live roots.
2. Complete real certificate-chain and trusted-network validation from each
   production desktop class.
3. Enable the general and structural mutation gates together only after the
   service and backup gates are green.
4. Move every production desktop to explicit service mode in one coordinated
   window, then verify ordinary editing and planner authorization.
5. Keep the local writer available only as the documented rollback path until
   post-window verification completes.

## Rollback prerequisites and blockers

Rollback requires the coordinated JSON/SQLite backup gate, a tested restore
procedure, verified real certificates and trusted-network configuration, and a
complete inventory of desktops that must switch back together. Those gates are
not present in this phase. Production remains disabled until they are completed;
the actual cutover must not be inferred from this temporary rehearsal.

