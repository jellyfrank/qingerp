from markupsafe import Markup

from odoo import fields, models
from odoo.exceptions import AccessError, UserError


class AccrualClosing(models.TransientModel):
    _name = 'qing.accrual.closing'
    _description = '暂估结账检查'

    def _check_scope(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('仅财务管理员可以执行暂估结账检查。'))
        if self.company_id != self.env.company:
            raise UserError(self.env._('请切换到检查所属公司。'))

    def _issues(self):
        self._check_scope()
        today = fields.Date.context_today(self)
        domain = [('company_id', '=', self.company_id.id), ('state', 'in', ['draft', 'locked', 'prepared', 'posted'])]
        batches = self.env['qing.accrual.batch'].search(domain, limit=1001, order='id')
        if len(batches) > 1000:
            raise UserError(self.env._('待处理批次超过1000条，请先清理草稿或分批处理；本次不生成不完整检查结果。'))
        issues = []
        for batch in batches:
            if batch.state in ('draft', 'locked', 'prepared'):
                issues.append((batch, self.env._('尚未过账，请复核后处理或取消。')))
                continue
            move = batch.draft_move_id
            if not move or move.state != 'posted' or batch.reversal_move_id:
                issues.append((batch, self.env._('批次与会计凭证状态不一致，请核查。')))
            if not batch.reversal_date:
                issues.append((batch, self.env._('缺少冲回日期，请补充。')))
            elif move and batch.reversal_date < move.date:
                issues.append((batch, self.env._('冲回日期早于原凭证，请修正。')))
            elif batch.reversal_date <= today:
                issues.append((batch, self.env._('已到期未冲回，请进入批次执行冲回。')))
        return batches, issues

    company_id = fields.Many2one('res.company', string='公司', required=True, readonly=True,
        default=lambda self: self.env.company)
    result = fields.Html(string='检查结果', readonly=True)

    def action_check(self):
        batches, issues = self._issues()
        body = Markup('').join(Markup('<tr><td><a href="/odoo/qing.accrual.batch/%s">%s</a></td><td>%s</td></tr>') %
            (batch.id, batch.name, message) for batch, message in issues)
        self.result = Markup('<p>检查时间（UTC）：%s；当前公司待处理批次：%s；提示：%s项。</p>'
            '<p>%s</p><table class="table table-bordered"><thead><tr><th>批次</th><th>待核查事项</th></tr></thead><tbody>%s</tbody></table>') % (
                fields.Datetime.now(), len(batches), len(issues),
                self.env._('请处理下列事项后重新检查。') if issues else self.env._('当前未发现暂估批次待核查事项；这不代表整个账套已通过结账检查。'), body)
        return {'type': 'ir.actions.act_window', 'name': self.env._('暂估结账检查'),
            'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def write(self, values):
        if 'company_id' in values:
            values = dict(values, result=False)
        return super().write(values)
