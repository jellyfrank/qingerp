import csv
import hashlib
import io
import json
import re
from datetime import datetime
from decimal import Decimal

from markupsafe import Markup
from odoo import fields, models
from odoo.exceptions import AccessError, UserError

MAX_ROWS = 2000


class BankImport(models.TransientModel):
    _name = 'qing.bank.import'
    _description = '银行流水导入'

    def _check_scope(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError('仅财务管理员可以导入银行流水。')
        journal = self.journal_id
        journal.check_access('write')
        self.profile_id.check_access('read')
        if journal.company_id != self.env.company or journal.type != 'bank':
            raise UserError('请选择当前公司的银行日记账。')
        if not journal.bank_account_id or not journal.default_account_id or not journal.suspense_account_id:
            raise UserError('请先配置银行日记账的银行账号、银行科目及暂记科目。')
        if self.bank_account.strip().replace(' ', '') != journal.bank_account_id.account_number.replace(' ', ''):
            raise UserError('确认的本方银行账号与日记账不一致。')
        return journal.currency_id or journal.company_id.currency_id

    def _parse_rows(self, currency):
        profile = self.profile_id
        if not self.file or self.file.size > 1024 * 1024:
            raise UserError('请上传不超过 1 MiB 的 CSV 文件。')
        if not 1 <= profile.header_row <= 100:
            raise UserError('表头行号必须在 1 至 100 之间。')
        try:
            content = self.file.content.decode(profile.encoding)
            reader = csv.reader(
                io.StringIO(content),
                delimiter=profile._delimiter_character(),
                strict=True,
            )
            for _ in range(profile.header_row - 1):
                next(reader)
            headers = [value.strip() for value in next(reader)]
            if len(headers) != len(set(headers)) or not all(headers):
                raise UserError('表头存在重复列或空列。')
            mapping = {
                'date': profile.date_column, 'payment_ref': profile.memo_column,
                'qing_bank_reference': profile.reference_column,
                'partner_name': profile.partner_column, 'account_number': profile.account_column,
                'currency': profile.currency_column,
            }
            amount_columns = [profile.amount_column] if profile.amount_mode == 'signed' else [profile.income_column, profile.expense_column]
            if not all(amount_columns) or any(column not in headers for column in amount_columns + [v for v in mapping.values() if v]):
                raise UserError('CSV 表头与模板不匹配；不使用的可选列请在模板中留空。')
            rows = []
            for number, values in enumerate(reader, profile.header_row + 1):
                if not any(v.strip() for v in values):
                    continue
                if len(values) != len(headers) or len(rows) >= MAX_ROWS:
                    raise UserError('CSV 列数不一致或超过 2000 条；请拆分文件。')
                raw = dict(zip(headers, (v.strip() for v in values)))
                row = {key: raw[column] if column else '' for key, column in mapping.items()}
                if row.pop('currency') not in ('', currency.name):
                    raise UserError('CSV 币种与银行日记账币种不一致。')
                row['date'] = datetime.strptime(row['date'], profile.date_format).date().isoformat()
                def amount(column):
                    value = raw[column] or '0'
                    if not re.fullmatch(r'-?\d{1,12}(\.\d{1,6})?', value):
                        raise UserError('金额必须是普通数字，不含千分符、科学计数或货币符号。')
                    return Decimal(value)
                if profile.amount_mode == 'signed':
                    value = amount(profile.amount_column)
                else:
                    income, expense = amount(profile.income_column), amount(profile.expense_column)
                    if income < 0 or expense < 0 or (income and expense):
                        raise UserError('收入和支出必须非负，且不能同时有金额。')
                    value = income - expense
                if not value or Decimal(str(currency.round(float(value)))) != value:
                    raise UserError('金额不能为零，也不能超过币种精度。')
                if not row['payment_ref']:
                    raise UserError('每笔流水必须有摘要。')
                row['amount'] = float(value)
                row['qing_bank_reference'] = row['qing_bank_reference'] or False
                rows.append(row)
            if not rows:
                raise UserError('CSV 没有交易数据。')
            return rows
        except (UnicodeError, csv.Error, StopIteration, ValueError) as error:
            raise UserError('CSV 编码、日期或结构不正确，请检查格式模板。') from error

    def _prepare_preview(self):
        currency = self._check_scope()
        rows = self._parse_rows(currency)
        Line = self.env['account.bank.statement.line']
        references = [r['qing_bank_reference'] for r in rows if r['qing_bank_reference']]
        domain = [('journal_id', '=', self.journal_id.id), ('qing_bank_reference', 'in', references)]
        existing = {line.qing_bank_reference: line for line in Line.search(domain)}
        domain = [('journal_id', '=', self.journal_id.id), ('date', 'in', [r['date'] for r in rows])]
        candidates = Line.search(domain, limit=20001)
        if len(candidates) > 20000:
            raise UserError('同日已有流水过多，请拆分导入日期。')
        pairs = {(str(line.date), line.amount) for line in candidates}
        seen = set()
        statuses = []
        for row in rows:
            ref = row['qing_bank_reference']
            if ref and ref in seen:
                raise UserError('文件内银行流水号重复，请先核实。')
            seen.add(ref)
            old = existing.get(ref)
            if old:
                if any((str(old[key]) if key == 'date' else old[key] or '') != (value or '')
                       for key, value in row.items()):
                    raise UserError('银行流水号已存在，但日期、金额或其他内容不同，请人工核实。')
                statuses.append('已导入，跳过')
            elif not ref and (row['date'], row['amount']) in pairs:
                statuses.append('疑似重复，需确认')
            else:
                statuses.append('可导入')
            pairs.add((row['date'], row['amount']))
        token = hashlib.sha256(json.dumps([self.journal_id.id, rows, statuses], sort_keys=True).encode()).hexdigest()
        return rows, statuses, token

    allow_suspected = fields.Boolean(string='已核实疑似重复项均为独立真实交易，仍然导入')
    bank_account = fields.Char(string='确认本方银行账号', required=True)
    file = fields.Binary(string='银行 CSV', required=True, attachment=False)
    filename = fields.Char()
    journal_id = fields.Many2one('account.journal', required=True, string='银行日记账', domain="[('type', '=', 'bank')]")
    preview = fields.Html(string='预览结果', readonly=True)
    preview_token = fields.Char(readonly=True)
    profile_id = fields.Many2one('qing.bank.import.profile', required=True, string='格式模板')

    def action_preview(self):
        rows, statuses, token = self._prepare_preview()
        html_rows = [Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>') %
                     (r['date'], r['payment_ref'], r['amount'], r['qing_bank_reference'] or '', status)
                     for r, status in zip(rows, statuses)]
        self.write({'preview_token': token, 'allow_suspected': False,
                    'preview': Markup('<table class="table"><tr><th>日期</th><th>摘要</th><th>金额</th><th>流水号</th><th>检查</th></tr>%s</table>') % Markup('').join(html_rows)})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_import(self):
        self._check_scope()
        # Serialize imports into a journal; native ORM transaction retries handle concurrent updates.
        self.env.cr.execute('UPDATE account_journal SET write_date = write_date WHERE id = %s', [self.journal_id.id])
        rows, statuses, token = self._prepare_preview()
        if not self.preview_token or token != self.preview_token:
            raise UserError('文件、配置或已有流水发生变化，请重新预览。')
        if '疑似重复，需确认' in statuses and not self.allow_suspected:
            raise UserError('存在疑似重复流水，请核实后勾选确认，或修正文件。')
        vals = [dict(row, journal_id=self.journal_id.id, qing_import_filename=self.filename)
                for row, status in zip(rows, statuses) if status != '已导入，跳过']
        lines = self.env['account.bank.statement.line'].create(vals)
        self.preview_token = False
        action = self.env['ir.actions.act_window']._for_xml_id('qing_account.action_bank_transactions')
        action['domain'] = [('id', 'in', lines.ids)]
        return action
