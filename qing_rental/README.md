# qing_rental

Adds Community equipment-rental reservations, stock transfers, renewals, and deposits.

Rental orders reuse `qing_sale` payment status based on actual receipts allocated
to linked rental invoices. The rental list shows the status by default and supports
payment filters and grouping. The shared sales form exposes received and refunded
amounts under Other Information.

Refundable deposits and deposit refunds remain separate liability entries: neither
settles rent nor changes its payment status. Renewal charges increase the amount
due, so a previously paid order becomes partially paid until the extra rent is paid.
Invoice creation, credit-note offsets and unallocated advances are not cash receipts.
