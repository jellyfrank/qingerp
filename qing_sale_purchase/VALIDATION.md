# Validation

Target: Odoo 20.0 Community (`8738f2d4d99e02630999f56008eda1de2922ce73`).
Runtime: `ohm/saas-deploy:tenant-qingerp-200-618`; isolated PostgreSQL 16 container,
new `qing_purchase_test` database. No production database clone or tenant writes.
Dependency source: Solutions release `a377cb78514eaa9f34a54e450a21c1b80caf40b9`.

## Verified

- Base dependency installation completed; final sales view XPath targets the Odoo 20 `sol_qty` column.
- With `sale_stock`, `purchase_stock`, `sale_purchase_stock`: module upgrade and 14 business tests passed (0 failures, 0 errors).
- Partial 60/40 procurement, different vendors, distinct source lines and native navigation.
- Same-wizard idempotency, stale wizard prevention, direct PO quantity increase prevention.
- PO cancellation/deletion release, cancellation recovery rejection when quantity is already reallocated, clean SO/PO copies.
- Sales quantity increases/decreases and cancellation notifications without modifying existing purchases.
- Manager-only excess procurement, actual purchase-user creation, sales-only quantity visibility without purchase-cost access.
- Zero-price procurement without a confirmation checkbox, forged source rejection, company/product consistency.
- Chinese translations loaded successfully; field labels, create button and quantity guidance verified in the compiled form.
- Native Form vendor pricing and discount, vendor currency and purchase taxes, purchase/sales unit conversion.
- Warehouse receipt validation: purchased quantity becomes received, sales delivered quantity remains zero.
- Native outsourced services still generate their PO automatically, and a purchase-only user can confirm it.
- MTO rules are rejected by the manual wizard.
- Independent database transactions: simultaneous lock rejection, stale-snapshot serialization failure, fresh retry rejecting over-allocation; exactly one 70-unit allocation remains. `QING_CONCURRENCY_OK`.

## Performance

| Configuration | SO lines | One-line quantity edit SQL | RFQ creation SQL |
| --- | ---: | ---: | ---: |
| Without stock | 50 | 11 | 263 |
| Without stock | 150 | 10 | 636 |
| With stock | 50 | 151 | 287 |
| With stock | 150 | 245 | 674 |

With stock, native move/procurement updates add work on sales edits. The module does not
write all SO lines from a stored compute. Procurement creation grows with selected lines;
quantity aggregation uses a batched query. Measured operations run in savepoints under the
Odoo test rollback harness. Product line ordering is preserved and one PO line remains per source line.

## Boundaries

- Native Form/ORM/view compilation tests; no browser visual sign-off.
- No deployment or installation on the shared qingerp tenant.
- No historical origin-text matching or automatic supplier price-master updates.
- Warehouse receipts supported; dropshipping, dedicated stock allocation and cross-SO merging are excluded.
- Actual supplier returns and vendor bill posting are native workflows and were not end-to-end tested here.

The independent transaction check is intentionally executable only against the named disposable
database; it commits synthetic fixtures. Remove the isolated database/container after running it.
The expected SerializationFailure in its log is a successful stale-snapshot rejection, not an unhandled application failure.

## 20.0.1.1 wizard simplification (2026-09-19)

Removed the price confirmation field, onchange writes and server-side confirmation gate.
Finite, non-negative price validation remains; the existing negative/zero-price regression
now exercises this rule without any confirmation value. Sales quantity and sales unit use
`optional="hide"`; the Chinese help text no longer asks for a price confirmation.

Installed 20.0.1.0 into a fresh isolated stock-enabled database, then upgraded to 20.0.1.1:
14 business regressions passed with 0 failures / 0 errors. No shared tenant installation
or module upgrade was performed in this task.
