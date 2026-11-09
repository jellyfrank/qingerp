import math

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.qing_account_fapiao.models.fapiao import COLORS, DIRECTIONS, KINDS
from odoo.addons.qing_ocr.services.local_ocr import RecognitionError

from ..services.fapiao_parser import parse_fields

RESULT_FIELDS = {'ocr_text', 'ocr_engine', 'recognized', 'fapiao_id'}
REVIEW_FIELDS = {'number', 'code', 'invoice_date', 'buyer_name', 'buyer_tax_id', 'seller_name',
                 'seller_tax_id', 'amount_untaxed', 'amount_tax', 'invoice_total'}


class FapiaoOcr(models.TransientModel):
    _name = 'qing.fapiao.ocr'
    _description = '本地税票识别'
    _check_company_auto = True

    def _lock(self):
        self.ensure_one()
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM qing_fapiao_ocr WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        if self.company_id != self.env.company:
            raise UserError(self.env._('请切换到识别任务所属公司。'))

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'name': self.env._('本地识别并核对'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    amount_tax = fields.Monetary(string='税额', default=0)
    amount_untaxed = fields.Monetary(string='不含税金额', default=0)
    buyer_name = fields.Char(string='购买方名称')
    buyer_tax_id = fields.Char(string='购买方税号')
    code = fields.Char(string='发票代码')
    color = fields.Selection(COLORS, string='Invoice Color', required=True, default='blue')
    company_id = fields.Many2one('res.company', string='公司', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', string='票面币种', required=True, default=lambda self: self.env.ref('base.CNY'))
    direction = fields.Selection(DIRECTIONS, string='Invoice Direction', required=True, default='in')
    fapiao_id = fields.Many2one('qing.fapiao', readonly=True, check_company=True)
    invoice_date = fields.Date(string='开票日期')
    invoice_total = fields.Monetary(string='票面价税合计', default=0)
    kind = fields.Selection(KINDS, string='Tax Invoice Type', required=True, default='other')
    number = fields.Char(string='票号')
    ocr_engine = fields.Char(string='识别方式', readonly=True)
    ocr_text = fields.Text(string='识别原文（逐项核对，未识别字段需补填）', readonly=True)
    original_number = fields.Char(string='原蓝字票号')
    partner_id = fields.Many2one('res.partner', string='往来单位（手动选择）', check_company=True)
    recognized = fields.Boolean(readonly=True)
    reviewed = fields.Boolean(string='已逐项核对原票、票种、方向、币种及金额')
    seller_name = fields.Char(string='销售方名称')
    seller_tax_id = fields.Char(string='销售方税号')
    source_file = fields.Binary(string='原始票据', required=True, attachment=False)
    source_filename = fields.Char(string='文件名', required=True)

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            if RESULT_FIELDS & vals.keys():
                raise AccessError(self.env._('识别结果只能由本地识别动作生成。'))
            prepared.append(dict(vals, recognized=False, ocr_text=False, ocr_engine=False, fapiao_id=False, reviewed=False))
        return super().create(prepared)

    def write(self, vals):
        if RESULT_FIELDS & vals.keys():
            raise AccessError(self.env._('识别结果只能由本地识别动作生成。'))
        for record in self:
            record._lock()
            if record.fapiao_id:
                raise UserError(self.env._('该任务已生成草稿，请到税票台账处理。'))
        if {'source_file', 'source_filename'} & vals.keys():
            vals = dict(vals, recognized=False, ocr_text=False, ocr_engine=False, reviewed=False, kind='other',
                        **{key: False for key in REVIEW_FIELDS})
        elif set(vals) - {'reviewed'} and 'reviewed' not in vals:
            vals = dict(vals, reviewed=False)
        return super().write(vals)

    def action_recognize(self):
        self._lock()
        if self.fapiao_id:
            raise UserError(self.env._('该任务已生成草稿。'))
        try:
            result = self.env['qing.ocr.service']._extract_text(
                bytes(self.source_file), language='chi_sim+eng', max_pages=1)
        except RecognitionError as error:
            raise UserError(str(error)) from error
        text = result['text']
        engine = self.env._('PDF 本地文字层') if result['pages'][0]['source'] == 'pdf_text' else self.env._(
            'Tesseract chi_sim+eng（本地）')
        values = {key: False for key in REVIEW_FIELDS}
        values['kind'] = 'other'
        values.update(parse_fields(text))
        values.update(ocr_text=text, ocr_engine=engine, recognized=True, reviewed=False)
        super().write(values)
        return self._reopen()

    def action_create_draft(self):
        self._lock()
        if self.fapiao_id:
            ticket = self.fapiao_id
        else:
            if not self.recognized or not self.reviewed:
                raise UserError(self.env._('请先本地识别，并逐项核对原票后勾选确认。'))
            if not all((self.number, self.invoice_date, self.partner_id, self.buyer_name, self.seller_name)):
                raise UserError(self.env._('请补齐票号、开票日期、往来单位及购销方名称。'))
            if not all(math.isfinite(value) and value >= 0 for value in (self.amount_untaxed, self.amount_tax, self.invoice_total)):
                raise UserError(self.env._('金额必须为非负有限数，红字金额也按正数填写。'))
            currency = self.currency_id
            if currency.is_zero(self.invoice_total) or currency.compare_amounts(
                    self.amount_untaxed + self.amount_tax, self.invoice_total):
                raise UserError(self.env._('不含税金额加税额必须等于票面价税合计，合计必须大于零。'))
            if self.color == 'red' and not self.original_number:
                raise UserError(self.env._('红字税票请补填原蓝字票号，并将各金额按正数核对。'))
            vals = {key: self[key] for key in REVIEW_FIELDS - {'invoice_total'}}
            vals.update(company_id=self.company_id.id, currency_id=currency.id, partner_id=self.partner_id.id,
                direction=self.direction, kind=self.kind, color=self.color, original_number=self.original_number,
                source_file=self.source_file, source_filename=self.source_filename,
                note='本地识别后人工核对导入；识别方式：%s\n识别原文（仅供参考）：\n%s' % (self.ocr_engine, self.ocr_text))
            ticket = self.env['qing.fapiao'].create(vals)
            super().write({'fapiao_id': ticket.id})
        return {'type': 'ir.actions.act_window', 'name': self.env._('税票草稿'),
                'res_model': 'qing.fapiao', 'res_id': ticket.id, 'view_mode': 'form', 'target': 'current'}
