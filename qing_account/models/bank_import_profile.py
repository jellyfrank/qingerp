from odoo import fields, models

ENCODINGS = [('utf-8-sig', 'UTF-8'), ('gb18030', 'GB18030')]
SEPARATORS = [('comma', 'Comma'), ('semicolon', 'Semicolon'), ('tab', 'Tab')]
SEPARATOR_CHARACTERS = {'comma': ',', 'semicolon': ';', 'tab': '\t'}
MODES = [('signed', 'Single amount column: income positive, expense negative'),
         ('split', 'Separate income and expense columns')]


class BankImportProfile(models.Model):
    _name = 'qing.bank.import.profile'
    _description = '银行 CSV 格式模板'

    account_column = fields.Char(string='对方账号列', default='account_number')
    amount_column = fields.Char(string='金额列', default='amount')
    amount_mode = fields.Selection(MODES, default='signed', required=True, string='Amount Format')
    currency_column = fields.Char(string='币种列', default='currency')
    date_column = fields.Char(string='日期列', default='date', required=True)
    date_format = fields.Char(string='日期格式', default='%Y-%m-%d', required=True)
    delimiter = fields.Selection(SEPARATORS, default='comma', required=True, string='Delimiter')
    encoding = fields.Selection(ENCODINGS, default='utf-8-sig', required=True, string='Text Encoding')
    expense_column = fields.Char(string='支出列', default='expense')
    header_row = fields.Integer(string='表头行号', default=1, required=True)
    income_column = fields.Char(string='收入列', default='income')
    memo_column = fields.Char(string='摘要列', default='memo', required=True)
    name = fields.Char(required=True, string='模板名称')
    partner_column = fields.Char(string='对方户名列', default='partner_name')
    reference_column = fields.Char(string='银行流水号列', default='transaction_id')

    def _delimiter_character(self):
        self.ensure_one()
        return SEPARATOR_CHARACTERS[self.delimiter]
