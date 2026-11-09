import math

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

AUDIT_FIELDS = {'state', 'confirmed_at', 'confirmed_by', 'cancelled_at', 'cancelled_by'}


class FapiaoAllocation(models.Model):
    _name = 'qing.fapiao.allocation'
    _description = '税票金额分配'
    _check_company_auto = True
    _rec_name = 'move_id'

    def _lock(self):
        self.check_access('write')
        self.fapiao_id._lock()
        self.env.cr.execute('SELECT id FROM qing_fapiao_allocation WHERE id IN %s ORDER BY id FOR UPDATE',
                            [tuple(self.ids) or (0,)])
        self.invalidate_recordset()

    @api.constrains('amount_untaxed', 'amount_tax', 'fapiao_id', 'move_id')
    def _check_values(self):
        for line in self:
            amounts = (line.amount_untaxed, line.amount_tax)
            if not all(math.isfinite(v) and v >= 0 and abs(v - line.currency_id.round(v)) < 1e-8 for v in amounts):
                raise ValidationError(self.env._('分配金额和税额必须是非负有限数，且符合币种小数精度。'))
            if line.currency_id.is_zero(sum(amounts)):
                raise ValidationError(self.env._('分配金额与税额不能同时为零。'))
            line.fapiao_id._validate_moves(line.move_id)

    def _validate_capacity(self):
        Allocation = self.env['qing.fapiao.allocation']
        active = [('state', '=', 'confirmed'), ('fapiao_id.state', '=', 'registered')]
        for field, records in (('fapiao_id', self.fapiao_id), ('move_id', self.move_id)):
            groups = Allocation._read_group(active + [(field, 'in', records.ids)], [field],
                                            ['amount_untaxed:sum', 'amount_tax:sum'])
            for record, net, tax in groups:
                if (record.currency_id.compare_amounts(net, record.amount_untaxed) > 0
                        or record.currency_id.compare_amounts(tax, record.amount_tax) > 0):
                    raise UserError(self.env._('分配超出税票或业务账单的剩余不含税金额／税额，请分别核对。'))

        self.move_id._qing_validate_request_capacity()

    amount_tax = fields.Monetary(string='分配税额', required=True, default=0)
    amount_untaxed = fields.Monetary(string='分配不含税金额', required=True, default=0)
    cancel_reason = fields.Char(string='撤销原因')
    cancelled_at = fields.Datetime(string='撤销时间', readonly=True)
    cancelled_by = fields.Many2one('res.users', string='撤销人', readonly=True)
    company_id = fields.Many2one(related='fapiao_id.company_id', store=True)
    confirmed_at = fields.Datetime(string='确认时间', readonly=True)
    confirmed_by = fields.Many2one('res.users', string='确认人', readonly=True)
    currency_id = fields.Many2one(related='fapiao_id.currency_id')
    fapiao_id = fields.Many2one('qing.fapiao', string='税票', required=True, ondelete='restrict', check_company=True, index=True)
    move_id = fields.Many2one('account.move', string='业务账单', required=True, ondelete='restrict', check_company=True, index=True)
    state = fields.Selection([('draft', 'Pending Confirmation'), ('confirmed', 'Confirmed'), ('cancelled', 'Canceled')],
                             string='Allocation Status', default='draft', readonly=True, required=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for values in vals_list:
            if AUDIT_FIELDS & values.keys():
                raise AccessError(self.env._('分配状态和操作留痕只能通过按钮设置。'))
            values = dict(values)
            for key in ('amount_untaxed', 'amount_tax'):
                values.setdefault(key, self.env.context.get('default_' + key, 0))
            self.env['qing.fapiao']._validate_input_amounts(values)
            vals = dict(values, state='draft', confirmed_at=False, confirmed_by=False, cancelled_at=False, cancelled_by=False)
            prepared.append(vals)
        parents = self.env['qing.fapiao'].browse({v.get('fapiao_id') or self.env.context.get('default_fapiao_id') for v in prepared} - {None, False})
        parents._lock()
        if any(p.state == 'cancelled' for p in parents):
            raise UserError(self.env._('已取消登记的税票不能分配。'))
        return super().create(prepared)

    def write(self, vals):
        if AUDIT_FIELDS & vals.keys() or 'fapiao_id' in vals:
            raise AccessError(self.env._('不可直接修改分配状态或所属税票。'))
        self.env['qing.fapiao']._validate_input_amounts(vals)
        self._lock()
        if any(l.fapiao_id.state == 'cancelled' or l.state == 'cancelled'
               or (l.state == 'confirmed' and set(vals) - {'cancel_reason'}) for l in self):
            raise UserError(self.env._('已确认分配不可修改，请填写原因并撤销后重新分配。'))
        return super().write(vals)

    def unlink(self):
        self._lock()
        if any(l.state != 'draft' or l.fapiao_id.state == 'cancelled' for l in self):
            raise UserError(self.env._('仅待确认分配可删除，已确认及已撤销明细需保留。'))
        return super().unlink()

    def action_confirm(self):
        with self.env.cr.savepoint():
            self._lock()
            if any(l.state != 'draft' or l.fapiao_id.state != 'registered' for l in self):
                raise UserError(self.env._('先确认税票登记，再确认待分配明细。'))
            self.move_id._qing_lock_fapiao_moves()
            self._check_values()
            self.fapiao_id._touch_allocation_revision()
            self.move_id._qing_touch_allocation_revision()
            super().write({'state': 'confirmed', 'confirmed_at': fields.Datetime.now(), 'confirmed_by': self.env.uid})
            self._validate_capacity()
        return True

    def action_cancel(self):
        with self.env.cr.savepoint():
            self._lock()
            if any(l.state != 'confirmed' or l.fapiao_id.state != 'registered' or not (l.cancel_reason or '').strip() for l in self):
                raise UserError(self.env._('仅有效登记的已确认分配可撤销，请填写原因。'))
            domain = [('fapiao_id', 'in', self.fapiao_id.ids), ('state', '=', 'done')]
            if self.env['qing.fapiao.request'].search_count(domain):
                raise UserError(self.env._('This allocation completes an invoice request. Correct the tax invoice registration as a whole first.'))
            self.move_id._qing_lock_fapiao_moves()
            self.fapiao_id._touch_allocation_revision()
            self.move_id._qing_touch_allocation_revision()
            super().write({'state': 'cancelled', 'cancelled_at': fields.Datetime.now(), 'cancelled_by': self.env.uid})
        return True
