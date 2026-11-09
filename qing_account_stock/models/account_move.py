from odoo import api, fields, models
from odoo.exceptions import UserError


_ACCRUAL_TOKEN = object()


class AccountMove(models.Model):
    _inherit = 'account.move'

    _qing_reversal_unique = models.Constraint('UNIQUE(qing_accrual_reversal_batch_id)', '该批次已生成冲回凭证。')
    _qing_accrual_unique = models.Constraint('UNIQUE(qing_accrual_batch_id)', '该批次已生成暂估草稿。')

    def _qing_check_accrual_edit(self):
        if self.env.context.get('_qing_accrual_token') is not _ACCRUAL_TOKEN and (self.qing_accrual_batch_id or self.qing_accrual_reversal_batch_id):
            raise UserError(self.env._('暂估及冲回凭证由批次管理，不能直接修改、删除或重置；请进入关联批次操作。'))

    @api.model
    def _qing_create_accrual(self, batch, values):
        batch._check_manager()
        values = dict(values, qing_accrual_batch_id=batch.id)
        internal = self.with_context(_qing_accrual_token=_ACCRUAL_TOKEN)
        return super(AccountMove, internal).create(values).with_env(self.env)

    def _qing_discard_accrual(self):
        self.ensure_one()
        self.qing_accrual_batch_id._check_manager()
        if self.state != 'draft':
            raise UserError(self.env._('非草稿暂估凭证不能随批次取消。'))
        return self.with_context(_qing_accrual_token=_ACCRUAL_TOKEN).unlink()

    def _qing_post_accrual(self):
        self.ensure_one()
        self.qing_accrual_batch_id._check_manager()
        return self.with_context(_qing_accrual_token=_ACCRUAL_TOKEN).action_post()

    def _qing_reverse_accrual(self, batch, date):
        self.ensure_one()
        batch._check_manager()
        reversal = self.with_context(_qing_accrual_token=_ACCRUAL_TOKEN)._reverse_moves(
            default_values_list=[{'date': date, 'qing_accrual_reversal_batch_id': batch.id}], cancel=True)
        return reversal.with_env(self.env)

    qing_accrual_reversal_batch_id = fields.Many2one('qing.accrual.batch', string='暂估冲回批次',
        readonly=True, copy=False, index=True, ondelete='restrict')
    qing_accrual_batch_id = fields.Many2one('qing.accrual.batch', string='暂估准备批次', readonly=True,
        copy=False, index=True, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_qing_accrual_token') is not _ACCRUAL_TOKEN and (any(vals.get('qing_accrual_batch_id') or vals.get('qing_accrual_reversal_batch_id') for vals in vals_list) or self.env.context.get('default_qing_accrual_batch_id') or self.env.context.get('default_qing_accrual_reversal_batch_id')):
            raise UserError(self.env._('请通过暂估批次生成凭证草稿。'))
        return super().create(vals_list)

    def write(self, values):
        self._qing_check_accrual_edit()
        if {'qing_accrual_batch_id', 'qing_accrual_reversal_batch_id'} & values.keys() and self.env.context.get('_qing_accrual_token') is not _ACCRUAL_TOKEN:
            raise UserError(self.env._('不能直接修改暂估批次关联。'))
        return super().write(values)

    def unlink(self):
        self._qing_check_accrual_edit()
        return super().unlink()

    def copy_data(self, default=None):
        self._qing_check_accrual_edit()
        return super().copy_data(default)

    def _post(self, soft=True):
        if (self.qing_accrual_batch_id or self.qing_accrual_reversal_batch_id) and self.env.context.get('_qing_accrual_token') is not _ACCRUAL_TOKEN:
            raise UserError(self.env._('请通过暂估批次的受控入口过账或冲回。'))
        return super()._post(soft=soft)
