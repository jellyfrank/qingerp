# QingERP Repairs

Task #2392 extends native Odoo 20 Repairs without changing core source.

## Reuse Audit

The matching Solutions 20 `mommy_base` provides company settings and attachment
sharing, but no repair pricing engine. Native `repair`, `account.tax`, Sales,
invoice creation and payment reconciliation remain authoritative. Customer
document sharing uses native `portal.mixin`, not a second attachment-share token.

## Configuration

Advanced Settings > Repairs > Default Repair Pricing is company-specific.
Linked Quotation is the default. Direct Pricing enables editable service and
added-part charges. Changing the setting affects only new repairs. Repairs
created from a sales order keep their quotation path. Administrators may switch
an empty draft only; existing quotations, invoices and signed prices are never
converted or deleted automatically.

Direct prices use separate `qing_price_unit` fields, never stock valuation's
`price_unit`. Native tax helpers perform document-level rounding. Quotations
remain the sole price source in quotation mode; repair totals include only
repair-linked lines, not unrelated goods in the same sales order.

Direct invoices are created only after repair completion. A repeated request
returns the existing draft; posted charges cannot be edited or invoiced twice.
Additional uninvoiced charges use the native supplemental-invoice path.

## Completion and List States (#2433)

The company option **Create Draft Invoice on Repair Completion** defaults to
off. When enabled, confirmed direct-priced repairs create a native draft after
successful completion, including zero-priced warranty charges. Quotations,
quantity warnings, signed-price checks and accounting create permissions remain
authoritative. Empty repairs do not create empty invoices. Repeated completion
does not duplicate stock moves or invoices. Nothing posts or pays automatically.

The list offers six optional columns: device model, fault description, repair
content, total, invoice status and payment status. Ordinary exports include all
six. The read-only status summaries, like native invoice counts, are available to
repair users without granting access to accounting records.

Only direct repair invoices or invoices linked to repair-specific sales lines
are included, together with their credit notes. Unrelated sales-order charges
are excluded. Cancelled documents are ignored when active documents exist;
otherwise the invoice status is cancelled. A mix of drafts and posted documents
is explicitly shown. Payment states consider posted documents only: identical
states are preserved, a mix of unpaid/partial/in-payment/paid is partially paid,
and combinations involving reversal or blocking are shown as mixed. No posted
document means not applicable. These are document-state summaries, not a new
receivable balance calculation.

## Text Search

The native repair search offers Device Model and Repair Content as separate
case-insensitive partial-text search choices. They use the existing stored
fields, retain multiline content and respect native repair/company access.
Existing reference, product, customer, quotation, status and date searches remain
unchanged. Labels follow field translations and customer overrides, so BGWY's
Repair Record label is retained without customer-specific search code.

## Optional Portal

Install `qing_repair_portal` for Share, Preview, customer photos, PDF, optional
direct-price confirmation and separate completion acceptance. Signatures are
disabled by default and copied from company settings on new repairs. Tokenized
links grant access to one document to anyone holding the link. No repair or
stock-model ACL is granted to public/portal users. Internal notes and costs are
excluded from customer HTML/PDF. Signature requests include a price hash; stale
prices and premature completion acceptance are rejected. An administrator may
reopen an unfinished, uninvoiced price confirmation; prior evidence remains in
the chatter. Rotating the native access token invalidates old links.

Task #2432 adds onsite price and completion signing through a native signature
wizard. Internal repair write access is checked before reusing the portal's
validated signing method. Raw signature fields remain protected from direct
writes. Each signature records the submitting user and attaches a complete
signed repair PDF alongside the original signature and price audit evidence.
Download Repair PDF is also available on the backend form. The current PDF
includes both signatures once completion acceptance is signed.
Customer document sections use block layouts rather than flex grids so device
information, tags and both signatures remain inside the PDF's printable area.

The customer `bgwy_repair` supplies branding, terms, customer-only layouts and staged intake requirements.
It does not override the pricing default. Existing Odoo 16 code, databases and
share URLs are outside this task's migration scope.

## Regression Tests

Run tagged `qing_repair` and `qing_repair_portal` tests in a new isolated Odoo 20
database using the native accounting test chart. Cover defaults/copy/multi-company,
administrator restrictions, native quotation invoicing, direct pricing, zero
charges, stock-cost separation, warranty and global tax rounding, draft invoice
sync, duplicate requests, signatures, stale hashes, token isolation and reports.
