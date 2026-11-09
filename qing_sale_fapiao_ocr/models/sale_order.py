from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _check_upload_fapiao_access(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting administrators can upload issued tax invoices.'))
        if self.company_id != self.env.company:
            raise UserError(self.env._('Switch to the sales order company before uploading its tax invoice.'))
        if self.state != 'sale':
            raise UserError(self.env._('Confirm the sales order before uploading its tax invoice.'))

    def _create_invoices(self, final=False, grouped=False):
        self._qing_lock_invoice_upload()
        return super()._create_invoices(final=final, grouped=grouped)

    def _qing_lock_invoice_upload(self):
        """Serialize native invoicing and uploads, including older transaction snapshots."""
        if not self:
            return
        self.check_access('read')
        self.flush_recordset()
        order_ids = tuple(self.ids)
        self.env.cr.execute(
            'SELECT id FROM sale_order WHERE id IN %s ORDER BY id FOR UPDATE',
            [order_ids],
        )
        # Updating the row makes a stale repeatable-read snapshot retry instead of
        # creating a second invoice from previously cached invoiceable quantities.
        self.env.cr.execute(
            'UPDATE sale_order SET qing_invoice_upload_revision = '
            'COALESCE(qing_invoice_upload_revision, 0) + 1 WHERE id IN %s',
            [order_ids],
        )
        self.invalidate_recordset()
        self.order_line.invalidate_recordset([
            'invoice_lines', 'qty_invoiced', 'qty_to_invoice', 'invoice_status',
        ])

    qing_invoice_upload_revision = fields.Integer(
        string='Invoice Upload Revision', copy=False, readonly=True,
        help='Internal revision used to serialize invoice creation for this order.',
    )

    def action_upload_fapiao(self):
        self._check_upload_fapiao_access()
        context = {
            key: self.env.context[key]
            for key in ('allowed_company_ids', 'lang', 'tz')
            if key in self.env.context
        }
        context['default_sale_order_id'] = self.id
        view = self.env.ref('qing_sale_fapiao_ocr.view_sale_fapiao_upload_form')
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Upload Issued Tax Invoice'),
            'res_model': 'qing.fapiao.ocr',
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'new',
            'context': context,
        }

    @api.model_create_multi
    def create(self, vals_list):
        if any('qing_invoice_upload_revision' in values for values in vals_list):
            raise AccessError(self.env._('The invoice upload revision is managed internally.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'qing_invoice_upload_revision' in vals:
            raise AccessError(self.env._('The invoice upload revision is managed internally.'))
        return super().write(vals)
