import math

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

COLORS = [('blue', 'Blue'), ('red', 'Red')]
DIRECTIONS = [('in', 'Input'), ('out', 'Output')]
KINDS = [('special', 'Special VAT Invoice'), ('normal', 'General VAT Invoice'),
         ('other', 'Other Tax Invoice')]
STATES = [('draft', 'Draft Registration'), ('registered', 'Registered'),
          ('cancelled', 'Registration Cancelled')]
AUDIT_FIELDS = {'state', 'registered_at', 'registered_by', 'cancelled_at', 'cancelled_by'}


class Fapiao(models.Model):
    _name = 'qing.fapiao'
    _description = '税票台账'
    _order = 'invoice_date desc, id desc'
    _check_company_auto = True
    _rec_name = 'number'

    @api.model
    def _validate_input_amounts(self, vals):
        for key in ('amount_untaxed', 'amount_tax'):
            if key not in vals:
                continue
            try:
                value = float(vals[key] or 0)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValidationError(self.env._('金额和税额必须为有效数字。')) from error
            if not math.isfinite(value) or value < 0:
                raise ValidationError(self.env._('金额和税额必须为非负有限数。'))

    def _lock(self):
        self.check_access('write')
        records = self | self.original_id
        records.check_access('read')
        self.env.cr.execute('SELECT id FROM qing_fapiao WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(records.ids) or (0,)])
        records.invalidate_recordset()
        self.invalidate_recordset()

    @api.depends('amount_untaxed', 'amount_tax')
    def _compute_total(self):
        for record in self:
            record.amount_total = record.amount_untaxed + record.amount_tax

    @api.depends('company_id.partner_id', 'partner_id', 'direction')
    def _compute_party_partners(self):
        for record in self:
            company_partner = record.company_id.partner_id.commercial_partner_id
            counterparty = record.partner_id.commercial_partner_id
            if record.direction == 'in':
                record.buyer_partner_id = company_partner
                record.seller_partner_id = counterparty
            else:
                record.buyer_partner_id = counterparty
                record.seller_partner_id = company_partner

    @api.model
    def _party_snapshot_defaults(self, vals):
        company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
        partner = self.env['res.partner'].browse(vals.get('partner_id'))
        direction = vals.get('direction', 'in')
        buyer = company.partner_id if direction == 'in' else partner
        seller = partner if direction == 'in' else company.partner_id
        defaults = {}
        for key, value in (
            ('buyer_name', buyer.name), ('buyer_tax_id', buyer.vat),
            ('seller_name', seller.name), ('seller_tax_id', seller.vat),
        ):
            if key not in vals:
                defaults[key] = value
        return defaults

    @api.onchange('company_id', 'partner_id', 'direction')
    def _onchange_parties(self):
        for record in self:
            buyer = record.company_id.partner_id if record.direction == 'in' else record.partner_id
            seller = record.partner_id if record.direction == 'in' else record.company_id.partner_id
            record.buyer_name, record.buyer_tax_id = buyer.name, buyer.vat
            record.seller_name, record.seller_tax_id = seller.name, seller.vat

    @api.constrains('amount_untaxed', 'amount_tax', 'color')
    def _check_amounts(self):
        for record in self:
            if not all(math.isfinite(value) and value >= 0 for value in (record.amount_untaxed, record.amount_tax)):
                raise ValidationError(self.env._('金额和税额请填写非负有限数；红票用红字类型表示，金额仍填正数。'))

    def _validate_registration(self):
        for record in self:
            if record.company_id != self.env.company:
                raise UserError(self.env._('请切换到税票所属公司。'))
            if not record.number or not record.number.strip() or not record.buyer_name or not record.seller_name:
                raise UserError(self.env._('请填写票号、购买方名称和销售方名称。'))
            if any(abs(v - record.currency_id.round(v)) >= 1e-8 for v in (record.amount_untaxed, record.amount_tax)):
                raise UserError(self.env._('金额和税额必须符合票面币种的小数精度。'))
            if record.currency_id.is_zero(record.amount_total):
                raise UserError(self.env._('价税合计必须大于零。'))
            if not record.source_file or not record.source_filename:
                raise UserError(self.env._('请上传原始税票文件并填写文件名。'))
            if record.color == 'red' and not record.original_number:
                raise UserError(self.env._('红字税票请记录对应蓝字票号；此处不发起税务红冲。'))
            moves = record.move_ids
            moves.check_access('read')
            record._validate_moves(moves)

    def _validate_moves(self, moves):
        self.ensure_one()
        moves.check_access('read')
        expected = ('in_invoice' if self.direction == 'in' else 'out_invoice') if self.color == 'blue' else (
            'in_refund' if self.direction == 'in' else 'out_refund')
        if any(move.company_id != self.company_id or move.state != 'posted' or move.move_type != expected
               or move.currency_id != self.currency_id or move.commercial_partner_id != self.partner_id.commercial_partner_id
               for move in moves):
            raise UserError(self.env._('关联账单必须同公司、同币种、同往来单位、已过账，且进销项及蓝红方向一致。'))

    def _touch_allocation_revision(self):
        if self:
            self.env.cr.execute('UPDATE qing_fapiao SET allocation_revision = COALESCE(allocation_revision, 0) + 1 WHERE id IN %s',
                                [tuple(self.ids)])
            self.invalidate_recordset(['allocation_revision'])
            self.modified(['allocation_revision'])

    @api.depends('allocation_ids.state', 'allocation_ids.amount_untaxed', 'allocation_ids.amount_tax',
                 'amount_untaxed', 'amount_tax', 'state', 'allocation_revision',
                 'red_ids.state', 'red_ids.amount_untaxed', 'red_ids.amount_tax')
    def _compute_balances(self):
        for record in self:
            active = record.allocation_ids.filtered(lambda l: l.state == 'confirmed' and record.state == 'registered')
            red = record.red_ids.filtered(lambda r: r.state == 'registered')
            for part in ('untaxed', 'tax'):
                allocated = sum(active.mapped('amount_' + part))
                reversed_amount = sum(red.mapped('amount_' + part))
                record['allocated_' + part] = allocated
                record['remaining_' + part] = record['amount_' + part] - allocated
                record['red_remaining_' + part] = record['amount_' + part] - reversed_amount

    @api.onchange('original_id')
    def _onchange_original(self):
        if self.original_id:
            self.original_number = self.original_id.number

    def _validate_red_capacity(self):
        for record in self:
            if record.color != 'red':
                if record.original_id:
                    raise UserError(self.env._('仅红字税票可关联原蓝票。'))
                continue
            original = record.original_id
            if not original or original == record or original.state != 'registered' or original.color != 'blue':
                raise UserError(self.env._('红字税票必须选择已登记的原蓝字税票。'))
            if (original.company_id != record.company_id or original.currency_id != record.currency_id
                    or original.direction != record.direction or original.kind != record.kind
                    or original.partner_id.commercial_partner_id != record.partner_id.commercial_partner_id
                    or original.number != record.original_number):
                raise UserError(self.env._('红票与原蓝票的公司、币种、往来、进销项、票种及原票号必须一致。'))
            # Legacy red registrations with only a textual reference need review first.
            domain = [('state', '=', 'registered'), ('color', '=', 'red'), ('original_id', '=', False),
                      ('company_id', '=', original.company_id.id), ('direction', '=', original.direction),
                      ('original_number', '=', original.number)]
            if self.search_count(domain):
                raise UserError(self.env._('该蓝票存在未关联原票的历史红票，请先核对并更正历史登记。'))
            active = original.red_ids.filtered(lambda r: r.state == 'registered')
            for part in ('untaxed', 'tax'):
                total = sum(active.mapped('amount_' + part))
                if original.currency_id.compare_amounts(total, original['amount_' + part]) > 0:
                    raise UserError(self.env._('累计红冲超出原蓝票的不含税金额或税额，不能确认登记。'))

    allocated_tax = fields.Monetary(string='已分配税额', compute='_compute_balances')
    allocated_untaxed = fields.Monetary(string='已分配金额', compute='_compute_balances')
    allocation_ids = fields.One2many('qing.fapiao.allocation', 'fapiao_id', string='金额税额分配')
    allocation_revision = fields.Integer(default=0, readonly=True)
    original_id = fields.Many2one('qing.fapiao', string='原蓝字税票', check_company=True, ondelete='restrict', index=True)
    red_ids = fields.One2many('qing.fapiao', 'original_id', string='关联红票')
    red_remaining_tax = fields.Monetary(string='剩余可红冲税额', compute='_compute_balances')
    red_remaining_untaxed = fields.Monetary(string='剩余可红冲金额', compute='_compute_balances')
    remaining_tax = fields.Monetary(string='待分配税额', compute='_compute_balances')
    remaining_untaxed = fields.Monetary(string='待分配金额', compute='_compute_balances')

    amount_tax = fields.Monetary(string='税额', default=0.0, required=True)
    amount_total = fields.Monetary(string='价税合计', compute='_compute_total', store=True)
    amount_untaxed = fields.Monetary(string='不含税金额', default=0.0, required=True)
    buyer_name = fields.Char(string='购买方名称', required=True)
    buyer_partner_id = fields.Many2one('res.partner', string='购买方', compute='_compute_party_partners', store=True)
    buyer_tax_id = fields.Char(string='购买方税号')
    cancel_reason = fields.Text(string='取消登记原因')
    cancelled_at = fields.Datetime(string='取消登记时间', readonly=True, copy=False)
    cancelled_by = fields.Many2one('res.users', string='取消登记人', readonly=True, copy=False)
    code = fields.Char(string='发票代码', default='', help='按原票填写；无代码的税票留空。')
    color = fields.Selection(COLORS, string='Invoice Color', default='blue', required=True)
    company_id = fields.Many2one('res.company', string='公司', default=lambda self: self.env.company, required=True, index=True)
    currency_id = fields.Many2one('res.currency', string='币种', default=lambda self: self.env.company.currency_id, required=True)
    direction = fields.Selection(DIRECTIONS, string='Invoice Direction', default='in', required=True)
    invoice_date = fields.Date(string='开票日期', default=fields.Date.today, required=True)
    kind = fields.Selection(KINDS, string='Tax Invoice Type', default='special', required=True)
    move_ids = fields.Many2many('account.move', 'qing_fapiao_move_rel', 'fapiao_id', 'move_id',
        string='关联业务账单', check_company=True, help='仅来源关联，不表示金额已分配或核销。')
    note = fields.Text(string='备注')
    number = fields.Char(string='票号', required=True, index=True)
    original_number = fields.Char(string='原蓝字票号', help='与所选原蓝字税票号码一致；本系统不发起税务红冲申请。')
    partner_id = fields.Many2one('res.partner', string='往来单位', required=True, check_company=True)
    registered_at = fields.Datetime(string='登记时间', readonly=True, copy=False)
    registered_by = fields.Many2one('res.users', string='登记人', readonly=True, copy=False)
    seller_name = fields.Char(string='销售方名称', required=True)
    seller_partner_id = fields.Many2one('res.partner', string='销售方', compute='_compute_party_partners', store=True)
    seller_tax_id = fields.Char(string='销售方税号')
    source_file = fields.Binary(string='原始文件', attachment=False)
    source_filename = fields.Char(string='原始文件名')
    state = fields.Selection(STATES, string='Registration Status', default='draft', readonly=True, required=True, copy=False)

    _active_identity = models.UniqueIndex(
        "(company_id, direction, COALESCE(code, ''), number) WHERE state != 'cancelled'",
        '本公司该进销项、发票代码和票号已有登记，请检查重复。')

    @api.model_create_multi
    def create(self, vals_list):
        if 'default_amount_total' in self.env.context:
            raise AccessError(self.env._('价税合计由金额和税额自动计算。'))
        prepared = []
        for vals in vals_list:
            if (AUDIT_FIELDS | {'amount_total', 'allocation_revision', 'red_ids'}) & vals.keys():
                raise AccessError(self.env._('登记状态和审计信息只能通过按钮设置。'))
            self._validate_input_amounts(vals)
            vals = dict(vals)
            vals.update(self._party_snapshot_defaults(vals))
            for key in ('number', 'code', 'original_number', 'buyer_tax_id', 'seller_tax_id'):
                if key in vals:
                    vals[key] = (vals[key] or '').strip()
            # Explicit values also defeat RPC-supplied default_* context values.
            vals.update(state='draft', registered_at=False, registered_by=False, cancelled_at=False, cancelled_by=False)
            prepared.append(vals)
        self.flush_model(['state'])
        return super().create(prepared)

    def write(self, vals):
        if (AUDIT_FIELDS | {'amount_total', 'allocation_revision', 'red_ids'}) & vals.keys():
            raise AccessError(self.env._('登记状态和审计信息只能通过按钮设置。'))
        self._lock()
        if any(record.state == 'cancelled' or (record.state == 'registered' and set(vals) - {'cancel_reason', 'allocation_ids'}) for record in self):
            raise UserError(self.env._('已登记税票内容不可修改；错误登记请填写原因并取消登记后重新录入。'))
        self._validate_input_amounts(vals)
        vals = dict(vals)
        for key in ('number', 'code', 'original_number', 'buyer_tax_id', 'seller_tax_id'):
            if key in vals:
                vals[key] = (vals[key] or '').strip()
        return super().write(vals)

    def unlink(self):
        self._lock()
        if any(record.state != 'draft' for record in self):
            raise UserError(self.env._('已登记及已取消登记记录需保留，不可删除。'))
        self.allocation_ids.unlink()
        return super().unlink()

    def copy(self, default=None):
        raise UserError(self.env._('税票不可复制，请新建并核对真实票号。'))

    def action_register(self):
        with self.env.cr.savepoint():
            self._lock()
            if any(record.state != 'draft' for record in self):
                raise UserError(self.env._('仅草稿登记可确认。'))
            self.move_ids._qing_lock_fapiao_moves()
            self._validate_registration()
            (self | self.original_id)._touch_allocation_revision()
            result = super().write({'state': 'registered', 'registered_at': fields.Datetime.now(), 'registered_by': self.env.uid})
            self.flush_recordset(['state'])
            self._validate_red_capacity()
        return result

    def action_cancel_registration(self):
        with self.env.cr.savepoint():
            self._lock()
            if any(record.state != 'registered' or not (record.cancel_reason or '').strip() for record in self):
                raise UserError(self.env._('仅已登记记录可取消，请填写取消登记原因。'))
            for record in self:
                if record.color == 'blue':
                    domain = [('state', '=', 'registered'), ('color', '=', 'red'),
                              ('company_id', '=', record.company_id.id), ('direction', '=', record.direction),
                              '|', ('original_id', '=', record.id), ('original_number', '=', record.number)]
                    if self.search_count(domain):
                        raise UserError(self.env._('原蓝票仍有有效红票，请先处理红票登记。'))
            moves = self.allocation_ids.move_id
            moves._qing_lock_fapiao_moves()
            moves._qing_touch_allocation_revision()
            (self | self.original_id)._touch_allocation_revision()
            result = super().write({'state': 'cancelled', 'cancelled_at': fields.Datetime.now(), 'cancelled_by': self.env.uid})
            self.flush_recordset(['state'])
        return result
