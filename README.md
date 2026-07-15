# Job Traveler

A beginner-friendly terminal-based CNC Job Traveler app built with Python.

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
