# Restricted-field UI performance

## Contract

`isPartialRecord()` must retain the existing fail-closed rules for records and
their x2many children. It must not make every field component subscribe to all
ordinary values on all sibling lines.

The per-record WeakMap stores an Owl computed getter, not a permanent permission
boolean. Its dependencies are restricted metadata, data keys, field definitions,
relation replacement, list membership and child computed results. Quantity,
description and price changes do not rescan unchanged child permission metadata.
A detached native Scope, when needed, prevents a shared getter from inheriting
the first field component's lifetime. Native computation disposal and weak keys
avoid a process-global record registry. Backend access checks are unchanged.

Do not replace this with `toRaw()` alone or a permanently memoized boolean.
A manually maintained model-wide version is another possible design, but all
load, replace, add, unlink, delete, undo and discard paths must invalidate it.
The reactive derivation avoids maintaining that separate invalidation protocol.

## DEV2 verification, 2026-10-23

Only synthetic, neutralized databases and remote headless Chrome were used.
The native Owl SHA-256 matched the BGWY production runtime. Production customer
data was not used as a write-test fixture.

- 28 restricted-field HOOT tests, 290 assertions: passed.
- 16 backend restricted-read tests: passed, including strict API/write errors,
  field-group and record ACLs, failed-compute rollback and accessible batch reads.
- HOOT covers nested metadata, relation changes, first caller scope destruction,
  ordinary scalar edits without rescans, reload, rollback and discard. It also
  covers deleting the only restricted line, new-line onchange restrictions and
  ordinary line edits not rerunning parent permission consumers.
- Administrator and team-restricted salesperson, both 50 and 150 lines: product
  selection, quantity/price changes, save and read-back passed. Existing lines
  retained their original values. Another team's order still raised AccessError.
- Minified, without tracing/video/profiling: adding a line took 0.39-0.43 seconds
  at 50 lines and 0.92 seconds at 150 lines on the shared two-core DEV2 server.
- Native-only comparison with equivalent fixtures on the same runtime: 0.28-0.37
  seconds at 50 lines and 0.55-0.58 seconds at 150 lines. This is a separate fresh
  database, not an uninstall of dependent modules from the customer QA database.
- Debug CPU samples under this function at 150 lines fell from 39.95/48.41 seconds
  to 0.09/7.41 seconds. These are sampled observation windows, not per-click SLAs.
