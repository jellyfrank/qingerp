import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from odoo import api, fields, models
from odoo.tools.binary import BinaryBytes

from ..models.balance_sheet_template import ROW_NAMES as BALANCE_ROWS
from ..models.profit_statement_template import ROW_NAMES as PROFIT_ROWS


class FinancialReportExport(models.TransientModel):
    _name = 'qing.financial.report.export'
    _description = 'Financial Statement Export'
    _check_company_auto = True

    @api.model
    def _make_xlsx(self, report_type, wizard, data, metadata):
        book = Workbook()
        sheet = book.active
        sheet.title = '资产负债表' if report_type == 'balance' else '利润表'
        sheet.append(['公司', metadata['company'], '报表日期', metadata['date_to']])
        sheet.append(['本位币', metadata['currency'], '报表方案', metadata['scheme']])
        sheet.append(['资产负债表规则', metadata['balance_mapping'], '利润表规则', metadata['profit_mapping']])
        sheet.append([])
        if report_type == 'balance':
            headers = ['行次', '项目', '年初余额', '期末余额']
            row_names = BALANCE_ROWS
        else:
            headers = ['行次', '项目', '本年金额' if wizard.report_mode == 'annual' else '本年累计金额',
                       '上年金额' if wizard.report_mode == 'annual' else '本月金额']
            row_names = PROFIT_ROWS
        sheet.append(headers)
        for code, name in row_names.items():
            sheet.append([code, name, *[wizard.currency_id.round(value) for value in data['amounts'][code]]])
        for cell in sheet[5]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = 'C6'
        sheet.column_dimensions['A'].width = 10
        sheet.column_dimensions['B'].width = 43
        sheet.column_dimensions['C'].width = 20
        sheet.column_dimensions['D'].width = 20
        for row in sheet.iter_rows(min_row=6, min_col=3, max_col=4):
            for cell in row:
                cell.number_format = '#,##0.00;[Red](#,##0.00)'
                cell.alignment = Alignment(horizontal='right')
        detail = book.create_sheet('科目贡献')
        detail.append(['科目编码', '科目名称', '往来单位', '行次', headers[2], headers[3]])
        account_model = self.env['account.account']
        partner_model = self.env['res.partner']
        for key, amounts in sorted(data['details'].items()):
            if report_type == 'balance':
                account_id, partner_id, code = key
                partner = partner_model.browse(partner_id)
                partner_name = partner.display_name if partner else ''
            else:
                account_id, code = key
                partner_name = ''
            account = account_model.browse(account_id)
            detail.append([
                account.code, account.name, partner_name, code,
                *[wizard.currency_id.round(value) for value in amounts],
            ])
            for cell in detail[detail.max_row][:3]:
                cell.data_type = 's'
        for cell in detail[1]:
            cell.font = Font(bold=True)
        detail.freeze_panes = 'E2'
        for key, width in [('A', 19), ('B', 43), ('C', 36), ('D', 10), ('E', 20), ('F', 20)]:
            detail.column_dimensions[key].width = width
        for worksheet in book.worksheets:
            for row in worksheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = 's'
        stream = io.BytesIO()
        book.save(stream)
        return BinaryBytes(stream.getvalue())

    @api.model
    def _make_cash_xlsx(self, data):
        book = Workbook()
        sheet = book.active
        sheet.title = '现金流量表'
        sheet.append(['公司', data['company'], '报表日期', data['date_to']])
        sheet.append(['本位币', data['currency'], '报表方案', data['scheme']])
        sheet.append([])
        sheet.append(['行次', '项目', *data['headers']])
        for row in data['rows']:
            sheet.append([row['code'], row['name'], *[float(value) for value in row['amounts']]])
        for cell in sheet[4]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = 'C5'
        for key, width in [('A', 10), ('B', 66), ('C', 20), ('D', 20)]:
            sheet.column_dimensions[key].width = width
        for row in sheet.iter_rows(min_row=5, min_col=3, max_col=4):
            for cell in row:
                cell.number_format = '#,##0.00;[Red](#,##0.00)'
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        stream = io.BytesIO()
        book.save(stream)
        return BinaryBytes(stream.getvalue())

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    file = fields.Binary(readonly=True, attachment=False)
    filename = fields.Char(readonly=True)
    html = fields.Html(readonly=True)
