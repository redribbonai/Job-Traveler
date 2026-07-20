# Job Traveler

A beginner-friendly terminal-based CNC Job Traveler app built with Python.

Phase 2C adds a revision-aware desktop persistence boundary while keeping local
compatibility mode as the production-safe default. The authenticated ShopOS
service client remains non-production, explicit opt-in, and has no local
fallback. See [PHASE_2C_SERVICE_CLIENT.md](PHASE_2C_SERVICE_CLIENT.md) for the
mode, conflict, retry, and later-cutover constraints.

This app creates digital job travelers for machine shop jobs. Each traveler is saved as a JSON file and can be opened later by job number. Employees can update their section of the traveler, and the app prints a paper-style traveler with blanks for missing fields.

## Features

- Create new job travelers
- Save job travelers as JSON files
- Open existing travelers by job number
- List existing saved travelers
- Update traveler sections:
  - Programming
  - Saw Cutting
  - CNC Machining
  - Deburr
  - Inspection
  - Packing
  - Shipping
- Preserve existing field values when updating
- Add timestamps when sections are updated
- Print a paper-style traveler with blanks for missing fields

## Multi-operation workflow

Programming defines one or more sequential machining operations. Each operation
records its type (`Mill` or `Turning`), program, revision, status, update time,
and notes. Programming does not assign a machine.

CNC Machining selects one configured operation and records its operator,
official machine, completed quantity, automatically calculated status, update
time, and notes. Machine choices are filtered by the operation type.

Inspection selects a configured operation. Its operation type comes from
Programming and its machine comes from that operation's CNC Machining record;
neither value is manually entered again. A CNC machine must be assigned before
the inspection can be saved. Inspection records preserve:

- Inspector and report type
- Selected operation number
- Automatic operation type and machine snapshots
- Target Dimension
- Tolerance
- Finding / Actual Dimension
- Measurement Equipment Used
- Pass / Rejected result

The JSON structure uses `programming.operations`, `cnc_machining.operations`,
and operation-linked `inspection.records` lists. Existing single-operation jobs
are read as Operation 1 in memory. Their legacy CNC machine is preferred, with
the old Programming machine used only when the CNC machine is blank. Files are
not rewritten just by opening or printing them; the normalized structure is
saved after an actual workflow update.

### Shared domain contract

`traveler_domain.py` is the single pure compatibility contract shared by the
terminal application, Tkinter application, and ShopOS services. It owns
the seven section names, allowed statuses and operation types, official machine
lists, legacy/current normalization, canonical save representation, operation
resizing, structural validation, status derivation, and read-model helpers. It
does not import Tkinter, read or write files, prompt, print, or launch windows.
All normalization functions return copies; importing or reading the contract
cannot rewrite a traveler.

`job_traveler.py` continues to re-export the existing public constants and
functions, so terminal and GUI callers keep their current imports and behavior.
Sanitized legacy and canonical fixtures under `test_fixtures/` cover the shared
contract without depending on ignored shop jobs.

Legacy compatibility intentionally tolerates missing sections, numeric-string
operation counts, missing positional operation numbers, and missing or unknown
status values (reported as `Pending` in the derived status view). The explicit
storage validator rejects non-object sections, non-object operation rows,
invalid present operation numbers, and duplicate operation numbers rather than
normalizing those protected structures away. The desktop normalizer retains its
older permissive direct-call behavior for compatibility; storage/API consumers
must validate before normalizing untrusted files.

Contract version 2 also owns the pure validation and projection rules used by
the disabled ShopOS ordinary-field mutation service. Legacy travelers still
emit temporary compatibility coordinates and behave as document revision zero;
reading, viewing, printing, or normalizing them never creates metadata.

On the first successful confirmed server mutation, ShopOS may persist one
namespaced compatibility object:

```text
_shopos.document_revision
_shopos.last_applied_mutation_id
_shopos.operation_identities.machining_operations
_shopos.operation_identities.sections
```

The operation values are server-generated canonical UUIDs. One machining UUID
is shared by the corresponding Programming, CNC Machining, and inspection
descriptors; fixed sections also have stable section identities. Duplicate,
malformed, or conflicting UUIDs are rejected. Unknown compatible top-level,
section, operation, and `_shopos` values remain intact. Desktop canonical saves
preserve this metadata, but desktop writers do not yet advance revisions or
participate in server conflict detection.

The ordinary-field contract permits names, program name/revision, official CNC
machine selection, notes, nonnegative non-CNC section quantities, and existing
shipping details. It does not permit job identity, `_shopos`, UUIDs, operation
numbers/list structure, statuses, closure, dimensions, Tasks, or assignments.
Conflicts are scoped to the selected field through a deterministic value hash;
unrelated-field changes do not conflict. A deliberate replacement still must
submit the latest value hash and conflicts again if that field changed twice.

The reserved future `_shopos.closure_state` value may be read as `open` or
`closed`, but this phase never creates it. A valid `closed` marker makes employee
mutation read-only. There are no Tasks, assignments, employee edit-history
timeline, rollback snapshots, or closure commands. Production mutation remains
prohibited until the desktop writers participate in revisions and the controlled
deployment is explicitly approved.

Scrap, reject, and fail numbers are not printed on the public traveler. They may be used later for private boss reports.

## How to Run

```bash
python3 job_traveler.py
```

#Main Menu

1. Create New Job Traveler
2. Open Existing Job Traveler
3. List Existing Job Travelers
0. Exit

#Job Data

Saved job travelers are stored in:

jobs/

Each job is saved as:

jobs/<job_number>.json

Example:

jobs/12637-01.json

#Project Goal

The goal of this project is to become a digital production tracking system for CNC machine shops. The Parts Count Inspection System will eventually become one module inside this larger Job Traveler app.
