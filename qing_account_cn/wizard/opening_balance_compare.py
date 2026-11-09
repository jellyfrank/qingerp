import csv
import io
import re
from collections import defaultdict
from decimal import Decimal

from odoo import fields, models
from odoo.exceptions import AccessError, UserError


MAX_SOURCE_BYTES = 1024 * 1024
MAX_SOURCE_ROWS = 1000
BATCH_HEADERS = ['move_external_id', 'line_external_id', 'account_external_id',
                 'partner_external_id', 'currency_external_id', 'date_maturity',
                 'balance', 'amount_currency']
MAX_BATCH_ROWS = 5000
COMPARISON_MODES = [('account', 'Net Balance by Account'),
                    ('batch', 'Detailed Partner/Currency and Complete Batch')]

BALANCE_PATTERN = re.compile(r'^-?[0-9]{1,15}(?:\.[0-9]{1,6})?$')


class OpeningBalanceCompare(models.TransientModel):
    _name = 'qing.cn.opening.balance.compare'
    _description = '期初来源余额对照'

    def _check_scope(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('仅财务管理员可以核对期初来源余额。'))
        self.check_access('write')
        summary = self.move_ids._qing_cn_opening_summary()
        if self.company_id != self.env.company:
            raise UserError(self.env._('请切换回创建核对向导时的公司。'))
        if not self.source_date or any(move.date != self.source_date for move in self.move_ids):
            raise UserError(self.env._('选中凭证日期必须全部等于来源余额日期。'))
        if any(move.move_type != 'entry' or move.state == 'cancel' for move in self.move_ids):
            raise UserError(self.env._('仅支持普通日记账分录，不能包含发票、账单或已取消凭证。'))
        return summary

    def _read_source(self):
        if not self.source_file:
            raise UserError(self.env._('请上传来源余额 CSV。'))
        if self.source_file.size > MAX_SOURCE_BYTES:
            raise UserError(self.env._('来源 CSV 不能超过 1 MiB。'))
        try:
            content = self.source_file.content.decode('utf-8-sig')
            reader = csv.reader(io.StringIO(content), strict=True)
            if next(reader, None) != ['account_external_id', 'balance']:
                raise UserError(self.env._('CSV 表头必须为 account_external_id,balance。'))
            values = {}
            for number, row in enumerate(reader, 2):
                if number > MAX_SOURCE_ROWS + 1:
                    raise UserError(self.env._('来源 CSV 最多支持 1000 个科目。'))
                if len(row) != 2 or not BALANCE_PATTERN.fullmatch(row[1].strip()):
                    raise UserError(self.env._('第 %s 行格式错误：余额需为普通十进制数，借正贷负，不使用千位分隔符。', number))
                xmlid = row[0].strip()
                account = self.env.ref(xmlid, raise_if_not_found=False) if '.' in xmlid else None
                if not account or account._name != 'account.account':
                    raise UserError(self.env._('第 %s 行不是有效的科目外部 ID。', number))
                account.check_access('read')
                if self.company_id not in account.company_ids:
                    raise UserError(self.env._('第 %s 行科目不属于当前公司。', number))
                if account.id in values:
                    raise UserError(self.env._('第 %s 行科目重复；请先按科目汇总来源余额。', number))
                values[account.id] = Decimal(row[1].strip())
        except (UnicodeDecodeError, csv.Error, ValueError) as error:
            raise UserError(self.env._('无法读取来源文件，请使用 UTF-8 编码的 CSV。')) from error
        if not values:
            raise UserError(self.env._('来源 CSV 至少需要一个科目。'))
        return values

    def _compare_rows(self, source):
        actual = defaultdict(Decimal)
        for line in self.move_ids.line_ids:
            actual[line.account_id.id] += Decimal(str(line.balance))
        accounts = self.env['account.account'].browse(sorted(source.keys() | actual.keys()))
        accounts.check_access('read')
        currency = self.company_id.currency_id
        rows = []
        for account in accounts.sorted(lambda item: (item.code or '', item.id)):
            expected = source.get(account.id, Decimal(0))
            selected = actual.get(account.id, Decimal(0))
            difference = currency.round(float(selected - expected))
            status = self.env._('一致')
            if account.id not in source:
                status = self.env._('来源缺失')
            elif account.id not in actual:
                status = self.env._('选中分录缺失')
            elif not currency.is_zero(difference):
                status = self.env._('有差额')
            rows.append((account, expected, selected, difference, status))
        return rows

    def _batch_ref(self, xmlid, model, number, missing=False):
        if not xmlid or '.' not in xmlid:
            raise UserError(self.env._('第 %s 行需要完整的外部 ID。', number))
        record = self.env.ref(xmlid, raise_if_not_found=False)
        if not record:
            if missing:
                return self.env[model]
            raise UserError(self.env._('第 %s 行基础资料外部 ID 不存在。', number))
        if record._name != model:
            raise UserError(self.env._('第 %s 行外部 ID 对应的模型不正确。', number))
        record.check_access('read')
        if model == 'account.account':
            valid = self.company_id in record.company_ids
        elif model == 'res.currency':
            valid = True
        else:
            valid = not record.company_id or record.company_id == self.company_id
        if not valid:
            raise UserError(self.env._('第 %s 行外部 ID 不属于当前公司。', number))
        return record

    def _read_batch_source(self):
        if not self.source_file or self.source_file.size > MAX_SOURCE_BYTES:
            raise UserError(self.env._('请上传不超过 1 MiB 的来源明细 CSV。'))
        rows = []
        seen = set()
        try:
            reader = csv.reader(io.StringIO(self.source_file.content.decode('utf-8-sig')), strict=True)
            if next(reader, None) != BATCH_HEADERS:
                raise UserError(self.env._('表头不匹配，请下载逐笔／完整批次模板。'))
            for number, cells in enumerate(reader, 2):
                if number > MAX_BATCH_ROWS + 1:
                    raise UserError(self.env._('逐笔来源最多支持 5000 行。'))
                if len(cells) != len(BATCH_HEADERS):
                    raise UserError(self.env._('第 %s 行列数不正确。', number))
                row = dict(zip(BATCH_HEADERS, (cell.strip() for cell in cells)))
                for name in ('balance', 'amount_currency'):
                    if not BALANCE_PATTERN.fullmatch(row[name]):
                        raise UserError(self.env._('第 %s 行金额格式不正确，需使用借正贷负的十进制数。', number))
                    row[name] = Decimal(row[name])
                for name, model in (
                    ('move', 'account.move'), ('line', 'account.move.line'),
                    ('account', 'account.account'), ('partner', 'res.partner'), ('currency', 'res.currency'),
                ):
                    xmlid = row[name + '_external_id']
                    row[name] = (self.env[model] if name == 'partner' and not xmlid else
                                 self._batch_ref(xmlid, model, number, missing=name in ('move', 'line')))
                key = ('id', row['line'].id) if row['line'] else ('xmlid', row['line_external_id'])
                if key in seen:
                    raise UserError(self.env._('第 %s 行分录重复（含指向同一分录的不同外部 ID）。', number))
                seen.add(key)
                due = row['date_maturity']
                if due and not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', due):
                    raise UserError(self.env._('第 %s 行到期日应为 YYYY-MM-DD。', number))
                row['date_maturity'] = fields.Date.to_date(due) if due else False
                if row['account'].account_type in ('asset_receivable', 'liability_payable'):
                    if not row['partner'] or not row['date_maturity']:
                        raise UserError(self.env._('第 %s 行应收应付必须提供往来单位和到期日。', number))
                rows.append(row)
        except (UnicodeDecodeError, csv.Error, ValueError) as error:
            raise UserError(self.env._('无法读取明细来源，请检查 UTF-8 CSV 编码、日期及外部 ID。')) from error
        if not rows:
            raise UserError(self.env._('来源明细不能为空。'))
        return rows

    def _batch_comparison(self, rows):
        """Compare every source line and both set differences without exporting IDs."""
        currency = self.company_id.currency_id
        selected_lines = self.move_ids.line_ids
        selected_ids = set(selected_lines.ids)
        selected_move_ids = set(self.move_ids.ids)
        source_ids = {row['line'].id for row in rows if row['line']}
        source_moves = {row['move'].id for row in rows if row['move']}
        issues = []
        differences = []
        source_totals = defaultdict(Decimal)
        for row in rows:
            move_key = row['move'].id or row['move_external_id']
            source_totals[move_key] += row['balance']
            line = row['line']
            reasons = []
            if not row['move']:
                reasons.append(self.env._('来源凭证在目标库缺失'))
            elif row['move'].id not in selected_move_ids:
                reasons.append(self.env._('来源凭证未选中'))
            if not line:
                reasons.append(self.env._('来源分录在目标库缺失'))
            else:
                if line.id not in selected_ids:
                    reasons.append(self.env._('来源分录未选中'))
                if line.move_id != row['move']:
                    reasons.append(self.env._('所属凭证不符'))
                if line.account_id != row['account']:
                    reasons.append(self.env._('科目不符'))
                if line.partner_id != row['partner']:
                    reasons.append(self.env._('往来单位不符'))
                if line.currency_id != row['currency']:
                    reasons.append(self.env._('币种不符'))
                if line.date_maturity != row['date_maturity']:
                    reasons.append(self.env._('到期日不符'))
                if not currency.is_zero(float(Decimal(str(line.balance)) - row['balance'])):
                    reasons.append(self.env._('本位币余额不符'))
                if not row['currency'].is_zero(float(Decimal(str(line.amount_currency)) - row['amount_currency'])):
                    reasons.append(self.env._('原币金额不符'))
            if reasons:
                differences.append((row['line_external_id'], reasons))
        extra_lines = selected_lines.filtered(lambda line: line.id not in source_ids)
        extra_moves = self.move_ids.filtered(lambda move: move.id not in source_moves)
        if extra_moves:
            issues.append(self.env._('选中凭证中有 %s 张未出现在来源清单。', len(extra_moves)))
        if extra_lines:
            issues.append(self.env._('选中分录中有 %s 行未出现在来源清单。', len(extra_lines)))
        unbalanced = sum(not currency.is_zero(float(total)) for total in source_totals.values())
        if unbalanced:
            issues.append(self.env._('来源清单中有 %s 张凭证的本位币借贷不平衡。', unbalanced))
        return {'differences': differences, 'extra_lines': extra_lines, 'issues': issues,
                'source_move_count': len(source_totals)}

    def _batch_group_totals(self, rows):
        source = defaultdict(lambda: [Decimal(0), Decimal(0)])
        actual = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for row in rows:
            key = (row['account'].id, row['partner'].id, row['currency'].id)
            source[key][0] += row['balance']
            source[key][1] += row['amount_currency']
        for line in self.move_ids.line_ids:
            key = (line.account_id.id, line.partner_id.id, line.currency_id.id)
            actual[key][0] += Decimal(str(line.balance))
            actual[key][1] += Decimal(str(line.amount_currency))
        result = []
        for key in sorted(source.keys() | actual.keys()):
            account = self.env['account.account'].browse(key[0])
            partner = self.env['res.partner'].browse(key[1])
            currency = self.env['res.currency'].browse(key[2])
            expected = source.get(key, [Decimal(0), Decimal(0)])
            selected = actual.get(key, [Decimal(0), Decimal(0)])
            status = self.env._('一致')
            if key not in source:
                status = self.env._('来源缺失')
            elif key not in actual:
                status = self.env._('选中分录缺失')
            elif (not self.company_id.currency_id.is_zero(float(selected[0] - expected[0]))
                  or not currency.is_zero(float(selected[1] - expected[1]))):
                status = self.env._('有差额')
            result.append(self.env._(
                '%(account)s / %(partner)s / %(currency)s | 本位币 %(expected)s → %(actual)s | 原币 %(source_fx)s → %(actual_fx)s | %(status)s',
                account=account.display_name, partner=partner.display_name or '—', currency=currency.name,
                expected=expected[0], actual=selected[0], source_fx=expected[1], actual_fx=selected[1], status=status,
            ))
        return result

    def _batch_control_totals(self, rows, source_move_count):
        """Cross-check an independently supplied batch summary against both sides."""
        if not self.control_enabled:
            return [self.env._('未启用独立控制数：仅核对来源清单与选中凭证，不能识别两边同时遗漏。')]
        if self.control_move_count <= 0 or self.control_line_count <= 0:
            raise UserError(self.env._('独立控制凭证数和分录数必须为正整数。'))
        amounts = []
        for value in (self.control_debit, self.control_credit):
            value = (value or '').strip()
            if not BALANCE_PATTERN.fullmatch(value) or Decimal(value) < 0:
                raise UserError(self.env._('独立控制借贷总额必须填写非负十进制数，不使用千位符；零金额请填 0。'))
            amounts.append(Decimal(value))
        currency = self.company_id.currency_id
        expected = [self.control_move_count, self.control_line_count, *amounts]
        source = [source_move_count, len(rows),
                  sum((max(row['balance'], Decimal(0)) for row in rows), Decimal(0)),
                  sum((max(-row['balance'], Decimal(0)) for row in rows), Decimal(0))]
        lines = self.move_ids.line_ids
        selected = [len(self.move_ids), len(lines),
                    sum((Decimal(str(line.debit)) for line in lines), Decimal(0)),
                    sum((Decimal(str(line.credit)) for line in lines), Decimal(0))]
        labels = [self.env._('凭证数'), self.env._('分录数'), self.env._('借方总额'), self.env._('贷方总额')]
        text = [self.env._('独立批次控制数（金额为本位币）：项目 | 独立控制 | 来源清单 | 选中凭证 | 状态')]
        mismatch = 0
        for index, label in enumerate(labels):
            reasons = []
            for actual, name in ((source[index], self.env._('来源清单')), (selected[index], self.env._('选中凭证'))):
                difference = actual - expected[index]
                equal = difference == 0 if index < 2 else currency.is_zero(float(difference))
                if not equal:
                    reasons.append(self.env._('%s不符', name))
            mismatch += bool(reasons)
            text.append(f'{label} | {expected[index]} | {source[index]} | {selected[index]} | '
                        + ('；'.join(reasons) if reasons else self.env._('一致')))
        text.append(self.env._('独立控制数差异：%s 项。', mismatch))
        if not currency.is_zero(float(amounts[0] - amounts[1])):
            text.append(self.env._('独立控制数本身借贷不平衡，请复核来源批次汇总。'))
        text.append(self.env._('控制数应取自旧系统独立批次汇总；即使全部一致，也不能排除等额替换或错误来源。'))
        return text

    def _compare_batch(self, summary):
        rows = self._read_batch_source()
        check = self._batch_comparison(rows)
        text = [self.env._(
            '%(company)s / %(date)s / 本位币 %(currency)s；来源 %(source_moves)s 张凭证、%(source_lines)s 行；选中 %(moves)s 张、%(lines)s 行。',
            company=self.company_id.display_name, date=self.source_date, currency=self.company_id.currency_id.name,
            source_moves=check['source_move_count'], source_lines=len(rows), moves=len(self.move_ids), lines=summary['line_count'],
        )]
        text.append(self.env._('逐行差异或缺失：%s；选中多余分录：%s。', len(check['differences']), len(check['extra_lines'])))
        text.append(self.env._('核对时间（UTC）：%s。比较原分录余额，不是核销后的当前未结；修改账务后请重新核对。', fields.Datetime.now()))
        text.extend(self._batch_control_totals(rows, check['source_move_count']))
        text.extend(check['issues'])
        text.extend(summary['issues'])
        for xmlid, reasons in check['differences']:
            text.append(xmlid + '：' + '；'.join(reasons))
        for line in check['extra_lines']:
            text.append(self.env._('来源未列：凭证 %s / 分录数据库ID %s / %s', line.move_id.display_name, line.id, line.name or ''))
        text.append(self.env._('按科目／往来／币种汇总（来源 → 选中，借正贷负）：'))
        text.extend(self._batch_group_totals(rows))
        text.append(self.env._('完整性仅相对于本次来源清单；清单自身的遗漏需独立确认。本结果不代表财务验收，不自动过账或补平。'))
        self.result = '\n'.join(text)
        return {'type': 'ir.actions.act_window', 'name': self.env._('期初来源余额对照'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    comparison_mode = fields.Selection(
        COMPARISON_MODES,
        string='Comparison Mode', default='account', required=True,
    )
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, readonly=True)
    control_credit = fields.Char(string='来源贷方总额', help='独立来源汇总的本位币贷方正数合计；零金额填 0，不使用千位符。')
    control_debit = fields.Char(string='来源借方总额', help='独立来源汇总的本位币借方正数合计；零金额填 0，不使用千位符。')
    control_enabled = fields.Boolean(string='校验独立批次控制数')
    control_line_count = fields.Integer(string='来源分录总行数')
    control_move_count = fields.Integer(string='来源凭证总张数')
    move_ids = fields.Many2many('account.move', string='选中凭证', required=True, readonly=True)
    result = fields.Text(string='核对结果', readonly=True)
    source_date = fields.Date(string='来源余额日期', required=True)
    source_file = fields.Binary(string='来源余额 CSV', attachment=False, required=True)
    source_filename = fields.Char(string='文件名')

    def action_compare(self):
        summary = self._check_scope()
        if self.comparison_mode == 'batch':
            return self._compare_batch(summary)
        source = self._read_source()
        rows = self._compare_rows(source)
        currency = self.company_id.currency_id
        mismatch = sum(row[4] != self.env._('一致') for row in rows)
        text = [self.env._(
            '%(company)s / %(date)s / 本位币 %(currency)s\n选中 %(moves)s 张凭证；核对 %(accounts)s 个科目，差异或缺失 %(mismatch)s 个。',
            company=self.company_id.display_name, date=self.source_date,
            currency=currency.name, moves=len(self.move_ids), accounts=len(rows), mismatch=mismatch,
        )]
        text.append(self.env._('核对时间（UTC）：%s；凭证修改后请重新核对。', fields.Datetime.now()))
        text.append(self.env._('仅比较科目净余额（借正贷负），差额＝选中分录－来源。未检查往来明细、原币或全批完整性，不代表财务验收。'))
        source_total = sum(source.values())
        if not currency.is_zero(float(source_total)):
            text.append(self.env._('来源余额合计不为零，请确认来源范围是否完整。'))
        text.extend(summary['issues'])
        text.append(self.env._('科目 | 来源余额 | 选中余额 | 差额 | 状态'))
        for account, expected, selected, difference, status in rows:
            text.append(f'{account.display_name} | {expected:f} | {selected:f} | {difference:.{currency.decimal_places}f} | {status}')
        self.result = '\n'.join(text)
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('期初来源余额对照'),
            'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new',
        }

    def write(self, values):
        if {'comparison_mode', 'source_file', 'source_date', 'move_ids', 'company_id',
            'control_enabled', 'control_move_count', 'control_line_count',
            'control_debit', 'control_credit'} & values.keys():
            values = dict(values, result=False)
        return super().write(values)
