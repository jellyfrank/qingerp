# Repair Purchases

Optional QingERP integration of Repairs and Purchase. Authorized repair operators
with purchase access select unarranged added parts or purchasable service lines,
choose a vendor and create a draft RFQ. Native vendor pricing, units, taxes and
purchase receipts remain in use. Both quotation and direct repair pricing work.

The source repair and source lines are retained on the RFQ. Cancelled purchases
release their arranged quantity. Repeated submissions return the same RFQ;
concurrent wizards recheck the remaining requirement under a repair row lock
and advance a document-level revision to reject stale repeatable-read snapshots.
Removal/return moves are not purchased. No customer invoice or sales quotation
is created by this module. Customer-specific manual-purchase restrictions belong
in customer addons, not here.
