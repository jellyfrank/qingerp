from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class MommyFapiaoOrder(models.Model):
    _inherit = 'qing.fapiao.order'

    @api.model
    def _create_order(self, provider, payload):
        if 'default_qing_request_id' in self.env.context:
            raise AccessError(self.env._('Provider task links are managed by invoicing actions.'))
        return super()._create_order(provider, payload)

    def _check_submit_allowed(self):
        result = super()._check_submit_allowed()
        if self.qing_request_id:
            request = self.qing_request_id
            request._check_scope()
            request._lock()
            if request.state != 'approved':
                raise UserError(self.env._('The invoice request must remain approved before provider submission.'))
            request._validate_provider_order(self)
            request._validate_submission()
            request._lock_sources()._qing_validate_request_capacity()
        return result

    def _record_business_result(self):
        self.ensure_one()
        if not self.qing_request_id:
            return super()._record_business_result()
        return self.qing_request_id._record_provider_invoice(self)

    qing_request_id = fields.Many2one('qing.fapiao.request', string='Invoice Request',
                                      readonly=True, copy=False, ondelete='restrict', check_company=True, index=True)

    _unique_qing_request = models.Constraint(
        'UNIQUE(qing_request_id, request_key)',
        'This provider task is already bound to the invoice request.',
    )

    def action_retry_qing_request(self):
        self.ensure_one()
        self._check_manager()
        request = self.qing_request_id
        if not request:
            raise UserError(self.env._('Only a QingERP invoice request task can be retried here.'))
        request.check_access('read')
        order = request._retry_provider_order(self)
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.fapiao.order',
                'res_id': order.id, 'view_mode': 'form', 'target': 'current'}

    def action_open_qing_request(self):
        self.ensure_one()
        self._check_manager()
        request = self.qing_request_id
        if not request:
            return False
        request.check_access('read')
        return request._qing_view_action(
            'qing_account_fapiao.action_fapiao_request', 'qing_account_fapiao.view_fapiao_request_form',
        )

    @api.model_create_multi
    def create(self, vals_list):
        if any('qing_request_id' in vals for vals in vals_list) or 'default_qing_request_id' in self.env.context:
            raise AccessError(self.env._('Provider task links are managed by invoicing actions.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'qing_request_id' in vals:
            raise AccessError(self.env._('Provider task links are managed by invoicing actions.'))
        return super().write(vals)
