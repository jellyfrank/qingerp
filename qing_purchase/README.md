# qing_purchase

Restores a guarded purchase-order action for creating vendor-bill drafts using Odoo's native invoicing flow.

Purchase users can access only orders assigned to them as Buyer, including
order lines and purchase analysis. Purchase administrators and system
administrators can access all purchases in their allowed companies. Full
accounting roles retain the native cross-buyer accounting access.

Vendor bills, refunds and receipts follow the buyers of their linked purchase
lines. A bill spanning different buyers is visible only to the administrators
and full accountants, not to any one ordinary buyer. An unassigned purchase
does not grant buyer access. Bills without purchase links remain visible to
their creator under the native accounting permissions.

The same policy covers journal items, invoice analysis, bill autocomplete and
purchase/bill matching, including direct RPC reads and updates. Ordinary buyers
cannot assign their orders or source lines to another buyer's purchase. Native
company boundaries, portal access and existing create/post permissions remain
in force; this module does not grant accounting administrator rights.
