from odoo import fields, models


class FapiaoProviderSelect(models.TransientModel):
    _name = 'qing.fapiao.provider.select'
    _description = 'Select Invoicing Provider'
    _check_company_auto = True

    company_id = fields.Many2one(related='request_id.company_id', store=True)
    provider_id = fields.Many2one('qing.fapiao.provider', string='Invoicing Provider', required=True, check_company=True)
    request_id = fields.Many2one('qing.fapiao.request', string='Invoice Request', required=True, readonly=True, ondelete='cascade', check_company=True)
    seller_tax_id = fields.Char(related='request_id.seller_tax_id')

    def action_queue(self):
        self.ensure_one()
        self.check_access('write')
        self.request_id._create_provider_order(self.provider_id)
        return self.request_id.action_open_provider_order()
