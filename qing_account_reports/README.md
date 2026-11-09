# qing_account_reports

Adds a full-page trial balance, balance sheet, profit statement, and cash-flow statement to Odoo Community. The trial-balance page supports a date range, company-currency totals, debit-credit checks, source-ledger navigation, and CSV export; calculations continue to use posted journal items visible to the current accounting manager.

Task #2390 adds Reporting / Account Ledger. Select a current-company account and
date range to see opening, signed per-entry amounts, chronological running balances,
period turnover, and closing. Ordering is date then journal-item ID; page balances
include earlier pages. Balances are in company currency, with foreign-currency
amounts displayed separately. Reconcilable accounts show native residuals at the
end date, not the account balance. No artificial year-end earnings transfer is
applied. Archived accounts and native record visibility are respected. CSV export
is limited to 10,000 entries, protects spreadsheet formulas, and never posts or
reconciles entries.
