from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _lock_rental_inventory(self):
        """Serialize rental decisions and force stale repeatable-read snapshots to retry.

        An advisory lock alone does not refresh a PostgreSQL repeatable-read
        snapshot. Updating the product row makes competing stale transactions
        raise SerializationFailure; Odoo retries the complete request.
        """
        products = self.exists().sorted('id')
        if not products:
            return
        products.flush_recordset(['qing_rental_lock_version'])
        for product in products:
            self.env.cr.execute(
                'UPDATE product_product SET qing_rental_lock_version = '
                'COALESCE(qing_rental_lock_version, 0) + 1 WHERE id = %s',
                [product.id],
            )
        products.invalidate_recordset(['qing_rental_lock_version'])

    qing_rental_lock_version = fields.Integer(default=0, readonly=True, copy=False)
