import csv
import io
from collections import defaultdict

from markupsafe import Markup

from odoo import fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes


ACCOUNT_TYPES = [('both', 'Receivable and Payable'), ('asset_receivable', 'Receivable'),
                 ('liability_payable', 'Payable')]
MAX_STATEMENT_LINES = 10000


class PartnerStatement(models.TransientModel):
    _name = 'qing.partner.statement'
    _description = '客商对账与账龄'

    def _age_labels(self):
        return [
            self.env._('Not Due'),
            self.env._('1–30 Days Overdue'),
            self.env._('31–60 Days Overdue'),
            self.env._('61–90 Days Overdue'),
            self.env._('Over 90 Days Overdue'),
            self.env._('Reverse Open Item'),
            self.env._('Settled'),
        ]

    def _statement_domain(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('仅财务管理员可以生成客商对账。'))
        self.check_access('write')
        if self.company_id != self.env.company:
            raise UserError(self.env._('请切换回创建对账向导时的公司。'))
        if not self.date_from or not self.date_to or self.date_from > self.date_to:
            raise UserError(self.env._('请填写有效的起止日期，开始日期不能晚于截止日期。'))
        self.partner_ids.check_access('read')
        if any(partner.company_id and partner.company_id != self.company_id for partner in self.partner_ids):
            raise UserError(self.env._('选择的往来单位不能属于其他公司。'))
        types = ['asset_receivable', 'liability_payable'] if self.account_type == 'both' else [self.account_type]
        domain = [('company_id', '=', self.company_id.id), ('parent_state', '=', 'posted'),
                  ('account_id.account_type', 'in', types), ('date', '<=', self.date_to)]
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))
        return domain

    def _statement_data(self):
        """Reconstruct residuals by native matching max_date, never today's residual."""
        domain = self._statement_domain()
        lines = self.env['account.move.line'].search(domain, order='date, move_id, id', limit=MAX_STATEMENT_LINES + 1)
        if len(lines) > MAX_STATEMENT_LINES:
            raise UserError(self.env._('所选范围含超过10000条历史往来分录，请按往来单位拆分核对。'))
        partials = lines.matched_debit_ids | lines.matched_credit_ids
        partials.check_access('read')
        adjustments = defaultdict(lambda: [0.0, 0.0])
        for partial in partials:
            if partial.max_date > self.date_to:
                continue
            adjustments[partial.debit_move_id.id][0] -= partial.amount
            adjustments[partial.debit_move_id.id][1] -= partial.debit_amount_currency
            adjustments[partial.credit_move_id.id][0] += partial.amount
            adjustments[partial.credit_move_id.id][1] += partial.credit_amount_currency
        groups = {}
        details = []
        currency = self.company_id.currency_id
        for line in lines:
            key = (line.account_id.account_type, line.partner_id.id, line.currency_id.id)
            group = groups.setdefault(key, {
                'type': line.account_id.account_type, 'partner': line.partner_id, 'currency': line.currency_id,
                'opening': 0.0, 'debit': 0.0, 'credit': 0.0, 'closing': 0.0, 'residual': 0.0,
                'residual_currency': 0.0, 'aging': [0.0] * 6,
            })
            group['closing'] += line.balance
            if line.date < self.date_from:
                group['opening'] += line.balance
            else:
                group['debit'] += line.debit
                group['credit'] += line.credit
            residual = currency.round(line.balance + adjustments[line.id][0])
            residual_fx = line.currency_id.round(line.amount_currency + adjustments[line.id][1])
            group['residual'] += residual
            group['residual_currency'] += residual_fx
            direction = 1 if group['type'] == 'asset_receivable' else -1
            normalized = residual * direction
            normalized_fx = residual_fx * direction
            due = line.date_maturity or line.date
            days = (self.date_to - due).days
            if currency.is_zero(residual) and line.currency_id.is_zero(residual_fx):
                bucket = 6
            elif normalized < 0 or (currency.is_zero(residual) and normalized_fx < 0):
                bucket = 5
            else:
                bucket = 0 if days <= 0 else min((days - 1) // 30 + 1, 4)
            if bucket < 6:
                group['aging'][bucket] += normalized
            details.append({'line': line, 'residual': residual, 'residual_currency': residual_fx, 'bucket': bucket})
        return {'groups': list(groups.values()), 'details': details}

    def _statement_html(self, data, frozen=False):
        def table(headers, rows):
            head = Markup('').join(Markup('<th>%s</th>') % value for value in headers)
            body = Markup('').join(Markup('<tr>%s</tr>') % Markup('').join(
                Markup('<td>%s</td>') % value for value in row) for row in rows)
            return Markup('<div class="table-responsive"><table class="table table-bordered"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>') % (head, body)
        currency = self.company_id.currency_id
        age_labels = self._age_labels()
        amount = lambda value: f'{value:.{currency.decimal_places}f}'
        rows, ages = [], []
        for group in data['groups']:
            name = group['partner'].display_name or '未填写往来单位'
            kind = '应收' if group['type'] == 'asset_receivable' else '应付'
            prefix = [kind, name, group['currency'].name]
            rows.append(prefix + [amount(group[key]) for key in ('opening', 'debit', 'credit', 'closing', 'residual')]
                        + [f"{group['residual_currency']:.{group['currency'].decimal_places}f}"])
            ages.append(prefix + [amount(value) for value in group['aging']])
        title = Markup('<p>%s / %s 至 %s / 本位币 %s；共 %s 条已过账往来分录。</p>') % (
            self.company_id.display_name, self.date_from, self.date_to, currency.name, len(data['details']))
        warning = Markup('<p>账面余额借正贷负；账龄应收借方、应付贷方为正，反向未结单列，不自动认定为预收预付。'
                         '无到期日按记账日计算；截止日当天到期列未到期。原币按币种分组。'
                         '按当前有效凭证及核销关系回算，撤销核销或回溯改账后须重新生成；本结果不是历史冻结快照。</p>')
        if frozen:
            warning = Markup('<p>此为保存时按当时有效凭证和核销关系生成的固定快照，后续变更不改写本结果。账面余额借正贷负；账龄应付贷方为正，反向未结单列。无到期日按记账日；当天到期列未到期；原币按币种分组。</p>')
        return title + warning + table(['类别', '往来单位', '原币', '期初', '本期借方', '本期贷方', '期末', '截止日未结本币', '截止日未结原币'], rows) + table(['类别', '往来单位', '原币'] + age_labels[:6], ages)

    def _statement_csv(self, data):
        age_labels = self._age_labels()
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['日期', '凭证', '分录ID', '科目', '往来单位', '摘要', '类别', '到期日', '币种',
                         '借方', '贷方', '原币发生额', '截止日未结本币', '截止日未结原币', '账龄', '期间归属', '公司', '本位币', '开始日期', '截止日期'])
        for item in data['details']:
            line = item['line']
            labels = [line.move_id.name, line.account_id.display_name, line.partner_id.display_name or '', line.name or '']
            values = [line.date, labels[0], line.id, labels[1], labels[2], labels[3],
                             '应收' if line.account_id.account_type == 'asset_receivable' else '应付',
                             line.date_maturity or '', line.currency_id.name, line.debit, line.credit,
                             line.amount_currency, item['residual'], item['residual_currency'], age_labels[item['bucket']],
                             '期初历史' if line.date < self.date_from else '本期', self.company_id.display_name,
                             self.company_id.currency_id.name, self.date_from, self.date_to]
            # Protect all text cells, including company and currency labels; keep numbers numeric.
            writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@'))
                             else value for value in values])
        return stream.getvalue().encode('utf-8-sig')

    account_type = fields.Selection(ACCOUNT_TYPES, string='Partner Type', default='both', required=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True, readonly=True)
    date_from = fields.Date(string='开始日期', required=True, default=lambda self: fields.Date.today().replace(day=1))
    date_to = fields.Date(string='截止日期', required=True, default=fields.Date.today)
    partner_ids = fields.Many2many('res.partner', string='往来单位', help='留空查询全部；指定时按所选联系人精确筛选，不自动合并子联系人。')
    result = fields.Html(string='对账与账龄', readonly=True)
    result_file = fields.Binary(string='下载明细 CSV', readonly=True, attachment=False)
    result_filename = fields.Char(default='partner-statement.csv', readonly=True)

    def action_generate(self):
        data = self._statement_data()
        self.write({'result': self._statement_html(data), 'result_file': BinaryBytes(self._statement_csv(data))})
        return {'type': 'ir.actions.act_window', 'name': self.env._('客商对账与账龄'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_save_snapshot(self):
        snapshot = self.env['qing.statement.snapshot']._from_wizard(self)
        return {'type': 'ir.actions.act_window', 'name': self.env._('客商对账快照'),
                'res_model': snapshot._name, 'res_id': snapshot.id, 'view_mode': 'form', 'target': 'current'}

    def action_open_lines(self):
        domain = self._statement_domain()
        return {'type': 'ir.actions.act_window', 'name': self.env._('对账范围分录'),
                'res_model': 'account.move.line', 'view_mode': 'list,form', 'domain': domain}

    def write(self, values):
        if {'account_type', 'company_id', 'date_from', 'date_to', 'partner_ids'} & values.keys():
            values = dict(values, result=False, result_file=False)
        return super().write(values)
