# qing_base

Provides shared QingERP branding, settings, audit logging, interface extensions, and basic installation statistics. Browser push registration, permission prompts, native notifications, and server push delivery are disabled; in-app messaging remains available.

## Computed Field Permissions

Internal web list and form reads tolerate permission failures inside non-stored
computations. Readable records and fields remain available; failing fields,
compute siblings, declared non-stored dependents and monetary fields whose
currency is unavailable are omitted and identified by
`__qing_restricted_fields`. The client displays `Permission restricted` instead
of manufacturing a zero or empty value. A partially readable record is read-only
and its business buttons are disabled. Navigating, reloading and discarding
remain available. Restricted values are excluded from client save payloads.

The implementation observes the common ORM compute entry through `mommy_base`.
No per-model permission map or compute-method rewrite is required. Install the
matching Solutions commit containing `mommy_base.tools.restricted_compute`
alongside this QingERP commit; upgrade `mommy_base` and `qing_base` together.

Normal record, model and field access denials still raise. Plain ORM/API reads,
web saves, unsaved records, stored recomputations and non-permission exceptions
retain their original behavior. Existing `compute_sudo` definitions are unchanged;
the observer never raises privileges. Failed attempts escape an Odoo savepoint
before retries, rolling back partial cache and database changes.

The successful path keeps batch reads. Failing batches are bisected with bounded
prefetch; only singleton failures need field probes. Recovery has a 1024-attempt
budget per web read, after which the original permission error is raised. If a
nested relation's computation fails without a root compute, that relation is
shown as unavailable as a whole. Searches, sorting and server-side grouped
aggregations are strict. Visible list totals are marked restricted when any
included record's value or currency is restricted.
