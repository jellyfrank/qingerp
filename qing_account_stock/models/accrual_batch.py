from dateutil.relativedelta import relativedelta

from odoo import api, Command, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes

ACCRUAL_STATES = [('draft', 'Draft'), ('locked', 'Locked'),
                  ('prepared', 'Draft Entry Prepared'), ('posted', 'Posted'),
                  ('reversed', 'Reversed'), ('cancel', 'Cancelled')]


class AccrualBatch(models.Model):
    _name = 'qing.accrual.batch'
    _description = '暂估准备批次'
    _order = 'id desc'

    def _check_manager(self):
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('仅财务管理员可以操作暂估准备批次。'))
        self.check_access('write')
        if any(batch.company_id != self.env.company for batch in self):
            raise UserError(self.env._('请切换到批次所属公司后操作。'))

    @api.model
    def _from_wizard(self, wizard):
        rows = wizard._rows()
        if any(row['gap'] < 0 for row in rows):
            raise UserError(self.env._('范围内有到票超过收货的明细，请先处理或缩小范围。'))
        rows = [row for row in rows if row['line'].uom_id.compare(row['gap'], 0) > 0]
        if not rows or any(row['reference'] is None or row['reference'] < 0 for row in rows):
            raise UserError(self.env._('没有可用的正数未到票明细，或参考额无法计算。'))
        return super().create({
            'name': self.env['ir.sequence'].next_by_code('qing.accrual.batch') or '/',
            'company_id': wizard.company_id.id, 'captured_at': fields.Datetime.now(),
            'source_line_ids': [Command.set([row['line'].id for row in rows])],
            'source_fingerprint': wizard._fingerprint_rows(rows),
            'snapshot_html': wizard._render(rows), 'snapshot_file': BinaryBytes(wizard._csv(rows)),
        })

    def _current_fingerprint(self):
        self.ensure_one()
        self.source_line_ids.check_access('read')
        wizard = self.env['qing.unbilled.purchase'].create({
            'company_id': self.company_id.id, 'include_balanced': True,
            'order_ids': [Command.set(self.source_line_ids.order_id.ids)],
        })
        if not self.source_line_ids:
            return False
        rows = [row for row in wizard._rows() if row['line'] in self.source_line_ids]
        return wizard._fingerprint_rows(rows)

    def _record_draft(self, move, snapshot):
        self.ensure_one()
        self._check_manager()
        super().write({'draft_move_id': move.id, 'entry_snapshot': snapshot, 'state': 'prepared'})

    def _lock_transition(self):
        self.ensure_one()
        self._check_manager()
        self.env.cr.execute('SELECT id FROM qing_accrual_batch WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    cancelled_at = fields.Datetime(string='取消时间', readonly=True)
    cancelled_by = fields.Many2one('res.users', string='取消人', readonly=True)
    captured_at = fields.Datetime(string='快照时间', readonly=True, required=True)
    company_id = fields.Many2one('res.company', string='公司', required=True, readonly=True, index=True)
    draft_move_id = fields.Many2one('account.move', string='暂估凭证', readonly=True, ondelete='set null')
    entry_snapshot = fields.Json(string='凭证生成快照', readonly=True)
    locked_at = fields.Datetime(string='锁定时间', readonly=True)
    locked_by = fields.Many2one('res.users', string='锁定人', readonly=True)
    name = fields.Char(string='批次编号', readonly=True, required=True)
    notes = fields.Text(string='备注')
    posted_at = fields.Datetime(string='过账时间', readonly=True)
    posted_by = fields.Many2one('res.users', string='过账人', readonly=True)
    reversal_date = fields.Date(string='冲回日期')
    reversal_move_id = fields.Many2one('account.move', string='冲回凭证', readonly=True, ondelete='restrict')
    reversed_at = fields.Datetime(string='冲回时间', readonly=True)
    reversed_by = fields.Many2one('res.users', string='冲回人', readonly=True)
    snapshot_file = fields.Binary(string='下载批次 CSV', readonly=True, attachment=False)
    snapshot_filename = fields.Char(default='accrual-batch.csv', readonly=True)
    snapshot_html = fields.Html(string='核对快照', readonly=True)
    source_fingerprint = fields.Char(readonly=True)
    source_line_ids = fields.Many2many('purchase.order.line', string='来源采购明细', readonly=True)
    state = fields.Selection(ACCRUAL_STATES,
                             default='draft', readonly=True, required=True, string='Status', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        raise UserError(self.env._('请从“到货未到票核对”生成批次，不能手工创建快照。'))

    def write(self, values):
        self._check_manager()
        if set(values) == {'reversal_date'} and all(batch.state == 'posted' for batch in self):
            return super().write(values)
        if set(values) - {'notes'} or any(batch.state != 'draft' for batch in self):
            raise UserError(self.env._('批次快照不可修改；仅草稿备注可编辑。来源变化请取消后重新生成。'))
        return super().write(values)

    def unlink(self):
        self._check_manager()
        if any(batch.state != 'draft' for batch in self):
            raise UserError(self.env._('仅未锁定的草稿可删除；其他批次保留记录。'))
        return super().unlink()

    def action_lock(self):
        self.ensure_one()
        self._check_manager()
        if self.state != 'draft':
            raise UserError(self.env._('仅草稿可以锁定。'))
        if self._current_fingerprint() != self.source_fingerprint:
            raise UserError(self.env._('来源已变化，请取消此批次并从核对入口重新生成。'))
        with self.env.cr.savepoint():
            self.env['qing.accrual.source']._acquire(self)
            super().write({'state': 'locked', 'locked_at': fields.Datetime.now(), 'locked_by': self.env.uid})
        return True

    def action_check_sources(self):
        self.ensure_one()
        self._check_manager()
        fresh = self._current_fingerprint() == self.source_fingerprint
        message = self.env._('来源与快照一致。') if fresh else \
            self.env._('来源已经变化；此快照不再代表当前未到票余额，请取消后重新生成。')
        return self._qing_notify(message, title=self.env._('来源检查'),
                                 notification_type='success' if fresh else 'warning', sticky=True)

    def action_cancel(self):
        self._lock_transition()
        if self.state == 'cancel':
            return True
        if self.state in ('posted', 'reversed'):
            raise UserError(self.env._('已过账批次不能取消，请使用冲回入口。'))
        if self.draft_move_id:
            self.draft_move_id._qing_discard_accrual()
        self.env['qing.accrual.source']._release(self)
        super().write({'state': 'cancel', 'cancelled_at': fields.Datetime.now(), 'cancelled_by': self.env.uid})
        return True

    def action_preview(self):
        self.ensure_one()
        self._check_manager()
        if self.state != 'locked':
            raise UserError(self.env._('请先锁定暂估准备批次。'))
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.accrual.preview',
                'view_mode': 'form', 'target': 'new', 'name': self.env._('暂估分录预览'),
                'context': {'default_batch_id': self.id}}

    def action_post(self):
        self._lock_transition()
        move = self.draft_move_id
        if self.state != 'prepared' or not move or move.state != 'draft':
            raise UserError(self.env._('仅已生成草稿的批次可以过账。'))
        today = fields.Date.context_today(self)
        if move.date != today or self._current_fingerprint() != self.source_fingerprint:
            raise UserError(self.env._('草稿日期不是当天或来源已变化，请取消后重新生成。'))
        if self.company_id._get_violated_lock_dates(move.date, False, move.journal_id):
            raise UserError(self.env._('过账日期处于锁账范围。'))
        snapshot = self.entry_snapshot
        expected = sorted((row[key], row['partner_id'], row['amount'] if key == 'debit_account_id' else -row['amount'])
            for row in snapshot['rows'] for key in ('debit_account_id', 'credit_account_id'))
        actual = sorted((line.account_id.id, line.partner_id.id, line.balance) for line in move.line_ids)
        if expected != actual or str(move.date) != snapshot['date'] or move.journal_id.id != snapshot['journal_id']:
            raise UserError(self.env._('凭证与生成快照不一致，不能过账。'))
        with self.env.cr.savepoint():
            move._qing_post_accrual()
            if move.state != 'posted' or str(move.date) != snapshot['date']:
                raise UserError(self.env._('原生过账结果或日期与批次不一致。'))
            super().write({'state': 'posted', 'posted_at': fields.Datetime.now(), 'posted_by': self.env.uid,
                'reversal_date': move.date + relativedelta(months=1, day=1)})
        return True

    def action_reverse(self):
        self._lock_transition()
        move = self.draft_move_id
        if self.state != 'posted' or not move or move.state != 'posted' or self.reversal_move_id:
            raise UserError(self.env._('仅尚未冲回的已过账批次可以冲回。'))
        date = self.reversal_date
        if not date or date < move.date or date > fields.Date.context_today(self):
            raise UserError(self.env._('冲回日期不得早于原凭证，也不能晚于今天。'))
        if self.company_id._get_violated_lock_dates(date, False, move.journal_id):
            raise UserError(self.env._('冲回日期处于锁账范围。'))
        with self.env.cr.savepoint():
            reversal = move._qing_reverse_accrual(self, date)
            if reversal.state != 'posted' or reversal.date != date:
                raise UserError(self.env._('原生冲回结果或日期与批次不一致。'))
            self.env['qing.accrual.source']._release(self)
            super().write({'state': 'reversed', 'reversal_move_id': reversal.id,
                'reversed_at': fields.Datetime.now(), 'reversed_by': self.env.uid})
        return True
