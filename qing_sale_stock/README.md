# qing_sale_stock

Displays delivery and return progress for sales orders using completed stock moves.

## Delivery photos (#2442)

Orders can contain multiple delivery photos with captions and a stable sequence.
The native image field validates images, preserves proportions and bounds the
stored image to 1600 x 1600 pixels. Photos are not copied when duplicating orders.
Read access follows the sales order; creation/edit/deletion require order write
access. Portal access and public sharing are not enabled.

Delivery photos are independent of Chatter attachments and of delivery status.
This module does not automatically print every photo. Customer report extensions
can explicitly render this relation using the existing report access boundary.
