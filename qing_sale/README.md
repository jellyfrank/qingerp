# qing_sale

## Chinese sales labels (#2470)

The native pro-forma send buttons and order-line Add Line/Section/Note controls
have Chinese translations through the existing Qing Sale form inheritance,
including the independent mobile kanban line-creation controls.
English source labels, button groups, visibility and line-type contexts are
unchanged. No native Odoo translation file or server source is patched.

Improves Community sales menus and adds display-only row numbers to sales lists and order lines.

## Order payment status (#2442)

Payment Status is independent of native invoice_status and amount_paid.
Only posted customer invoices/credit notes and their actual reconciled payment
or bank-statement amounts are counted. Authorizations, unallocated partner
payments, journal write-offs and invoice/credit-note offsets are not receipts.

The target is the full tax-inclusive order total: a paid deposit remains partial.
Merged invoices allocate each receipt proportionally to the linked tax-inclusive
invoice line amounts. Unlinked invoice lines retain their share. This is a
document-level proportional allocation, not a claim that a customer paid a
specific line. Lines linked to multiple orders, invalid negative allocations
and native payments without an allocatable journal entry require review.

Amounts use the invoice currency and invoice accounting date for conversion to
the order currency, rounded at the order currency precision. Partial/full cash
refunds are separate from credit-note offsets. Cancelled orders are not applicable.
Stored statuses support filtering and grouping and follow native reconciliation
updates. No payment, journal entry or reconciliation is created by this feature.

The generic list column is optional and hidden initially. Customer modules may
enable it by default without replacing the financial computation.

## Optional sales team isolation (#2445)

Assign `qing_sale.group_sale_team_restricted` to opt into team-based visibility.
Team leaders and members can read and update orders of their teams, including
peer-owned orders. Other teams and unassigned orders are excluded, including
order lines, customer invoices/credit notes, their lines and analysis models.
Native company constraints still apply. Vendor bills and journal entries keep
their native accounting access; this feature grants no accounting write rights.
System administrators and full accounting users retain their native cross-team
scope. Changes of team membership apply to subsequent requests.

Before enabling, review existing documents' team assignment and user membership.
Customer addons may enable the optional group for all sales roles; generic
QingERP does not force the policy on unrelated customers.
