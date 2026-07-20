# Phase 2C desktop persistence boundary

Phase 2C does **not** cut production over to Flask and does not make Flask the
exclusive production writer. The existing local desktop behavior remains the
default while the migration path is rehearsed against temporary data.

## Explicit modes

`traveler_persistence.TravelerPersistence` is the only persistence contract used
by the terminal and Tkinter entry points.

- `local` is the default when `JOB_TRAVELER_PERSISTENCE_MODE` is absent. Its
  `LocalTravelerPersistence` adapter owns the only traveler JSON write
  implementation in this repository. It reloads beneath the backend-compatible
  job lock, performs a recursive three-way merge, validates a staged document,
  atomically replaces it, syncs the directory where supported, and confirms the
  persisted revision before reporting success.
- `service` is explicit opt-in and has no filesystem fallback. It requires an
  already-issued approved employee bearer session to be injected into an
  HTTPS-only `TravelerClient`, followed by injection of
  `ServiceTravelerPersistence` into `job_traveler.main(...)` or
  `JobTravelerApp(..., persistence=...)`.

No bearer token environment variable, token file, embedded credential, proxy
secret, login flow, or credential store is provided. Setting the mode to
`service` without an injected authenticated client fails closed. This is
intentional: the repositories do not yet contain a safe desktop employee-session
acquisition source.

The service implementation supports existing `set_field` and
`replace_field_after_conflict` commands and the strictly typed `set_fields`
batch command. It refuses creation, operation-plan resizing, status and
timestamp workflows, quantity-completion changes, dimensions, protected fields,
unknown-field mutations, and general document replacement. Local compatibility
mode continues to support the existing coordinated structural workflow.

## Audited desktop save granularity

The pre-Phase-2C entry points had two raw traveler writers:
`job_traveler.save_job` and `job_traveler_gui.save_job_to_path` (with
`save_job_data` as a wrapper). They now delegate to the boundary. The remaining
HTML `write_text` call creates only a temporary print preview, not traveler JSON.

- New-job actions are structural creation.
- Programming is one multi-field logical save and may also resize the operation
  plan. Service mode refuses it when protected/type/status/timestamp or resize
  changes are present.
- Saw Cutting, Deburr, Packing, and Shipping forms are multi-field logical saves;
  their current status/timestamp behavior keeps the complete form local-only.
- A selected CNC operation is one multi-field logical save containing protected
  completion/status/timestamp behavior and is therefore local-only.
- An inspection header and its dimension collection are separate multi-field
  logical saves. Protected status/timestamp and dimension changes remain
  local-only.
- The explicit Save buttons submit the current document; an unchanged document
  is a confirmed no-op with no rewrite or revision increment.
- True allowlisted one-field service saves use `set_field`; two or more
  allowlisted fields use one atomic `set_fields` request.

## Conflict and retry contract

Both desktops show the intended and authoritative field values only when a
conflict occurs. Keeping the authoritative value is the default and retains
other unsaved input. A deliberate replacement compares against the latest field
hash; a second intervening edit conflicts again. Cancellation performs no write.

One canonical UUID request ID belongs to one logical service mutation. The
client never automatically retries validation, authentication, authorization,
feature-gate, or conflict responses. If a mutation transport outcome is
ambiguous, the returned retry object contains the original command and request
ID (but no authorization data), and only that exact request may be retried.

## Production activation blockers

All of the following remain required before a later cutover:

1. Provide a reviewed desktop source for short-lived, already-approved employee
   bearer sessions without plaintext or permanent credential storage.
2. Authorize or redesign the currently refused creation, plan-resize, status,
   timestamp, quantity-completion, and inspection-dimension actions. Phase 2C
   deliberately does not weaken the Phase 2B field allowlist.
3. Validate the production HTTPS origin, certificate trust, reverse-proxy
   assertions, and trusted-shop-network policy from the intended desktop hosts.
4. Complete an operational recovery rehearsal for the real topology and define
   the coordinated writer cutover and rollback window.
5. Only during that later coordinated activation, explicitly enable the read and
   mutation gates. Production and deployment examples must remain disabled now.
6. Verify every deployed desktop is in service mode before claiming Flask is the
   exclusive production writer.

The temporary Flask-app and multiprocessing tests are a non-production service
client rehearsal only. They are not a production deployment procedure.
