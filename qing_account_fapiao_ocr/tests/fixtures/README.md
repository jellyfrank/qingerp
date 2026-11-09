# Synthetic tax-invoice PDF

`synthetic_tax_invoice.pdf` contains only the synthetic `TEXT` values in
`test_fapiao_ocr.py`, printed as one paragraph per line on one A4 page. It contains
no customer data and is not a legally issued invoice.

The fixture was printed with headless Chromium using the local Hiragino Sans GB
font at 16 px and a 1.8 line height. Its glyphs and Unicode mappings are embedded,
so Poppler can extract the Chinese text without an external Adobe-GB1 CMap or a
server-installed Chinese font. The test uses the real PDF extraction service;
it does not substitute extracted text. The two-page rejection test joins two
copies using Odoo's PDF helper.

To replace this fixture, print the same synthetic text to a single-page PDF with
embedded Unicode mappings and verify that both Poppler and the tax-field parser
recover the number, date, parties and totals asserted by the end-to-end test.
