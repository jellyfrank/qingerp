from pathlib import Path
from unittest.mock import patch

from psycopg2 import IntegrityError
from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.qing_ocr.services.local_ocr import RecognitionError
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tools.binary import BinaryBytes
from odoo.tools.pdf import merge_pdf

from ..services.fapiao_parser import parse_fields

TEXT = '''仅测试样票
发票号码：12345678901234567890
开票日期：2026年09月16日
购买方名称：合成采购公司
购买方税号：TESTBUYER
销售方名称：合成销售公司
销售方税号：TESTSELLER
不含税金额：100.00
税额：13.00
价税合计：113.00
'''

COLUMN_TEXT = '''电子发票（普通发票）
发票号码：12345678901234567890
开票日期：2026年09月16日
购 名称：合成购方有限公司                销 名称：合成销方有限公司
买                                  售
方                                  方
信 统一社会信用代码/纳税人识别号：TESTBUYER000000001   信 统一社会信用代码/纳税人识别号：TESTSELLER00000001
息                                  息
项目名称  数量  单价  金额  税率/征收率  税额
合成项目  1  100.00  100.00  13%  13.00
合 计                  ￥100.00             ￥13.00
价税合计（小写）：￥113.00
'''


@tagged('post_install', '-at_install')
class TestFapiaoOcr(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='ocr_manager', groups='account.group_account_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.billing = new_test_user(cls.env, login='ocr_billing', groups='account.group_account_invoice')

    def _wizard(self):
        return self.env['qing.fapiao.ocr'].with_user(self.manager).create({
            'source_file': BinaryBytes(b'synthetic'), 'source_filename': 'synthetic.png',
            'partner_id': self.partner_a.id})

    def _recognize(self, wizard, text=TEXT, source='ocr'):
        result = {'text': text, 'engine': 'local',
                  'pages': [{'number': 1, 'text': text, 'source': source}], 'warnings': []}
        with patch.object(type(self.env['qing.ocr.service']), '_extract_text', return_value=result) as extract:
            wizard.action_recognize()
        extract.assert_called_once_with(b'synthetic', language='chi_sim+eng', max_pages=1)

    def _text_pdf(self, pages=1):
        document = (Path(__file__).parent / 'fixtures' / 'synthetic_tax_invoice.pdf').read_bytes()
        return document if pages == 1 else merge_pdf([document] * pages)

    def test_real_pdf_recognition_and_reviewed_draft(self):
        document = self._text_pdf()
        wizard = self._wizard()
        wizard.write({'source_file': BinaryBytes(document), 'source_filename': 'synthetic-tax-invoice.pdf'})
        wizard.action_recognize()
        self.assertTrue(wizard.recognized)
        self.assertFalse(wizard.reviewed)
        self.assertEqual(wizard.ocr_engine, 'PDF 本地文字层')
        self.assertEqual(wizard.number, '12345678901234567890')
        self.assertEqual(wizard.invoice_date.isoformat(), '2026-09-16')
        self.assertEqual(wizard.buyer_name, '合成采购公司')
        self.assertEqual(wizard.seller_name, '合成销售公司')
        self.assertEqual(wizard.amount_untaxed, 100)
        self.assertEqual(wizard.amount_tax, 13)
        self.assertEqual(wizard.invoice_total, 113)
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        wizard.reviewed = True
        action = wizard.action_create_draft()
        ticket = self.env['qing.fapiao'].browse(action['res_id'])
        self.assertEqual(ticket.state, 'draft')
        self.assertEqual(ticket.amount_total, 113)
        self.assertEqual(bytes(ticket.source_file), document)
        self.assertEqual(ticket.source_filename, 'synthetic-tax-invoice.pdf')

    def test_real_multipage_pdf_is_rejected_by_tax_wizard(self):
        wizard = self._wizard()
        wizard.write({'source_file': BinaryBytes(self._text_pdf(pages=2)), 'source_filename': 'two-pages.pdf'})
        with self.assertRaises(UserError):
            wizard.action_recognize()
        self.assertFalse(wizard.recognized)
        self.assertFalse(wizard.reviewed)
        self.assertFalse(wizard.ocr_text)
        self.assertFalse(wizard.fapiao_id)

    def test_parse_only_unambiguous_values(self):
        vals = parse_fields(TEXT)
        self.assertEqual(vals['number'], '12345678901234567890')
        self.assertEqual(vals['invoice_date'], '2026-09-16')
        self.assertEqual(vals['invoice_total'], 113)
        self.assertEqual(vals['buyer_name'], '合成采购公司')
        self.assertNotIn('number', parse_fields(TEXT + '\n发票号码：87654321'))
        self.assertNotIn('invoice_date', parse_fields('开票日期：2026年02月31日'))
        self.assertNotIn('amount_tax', parse_fields('税额：-13.00'))
        self.assertNotIn('number', parse_fields('发票号码：O2345678'))

    def test_vertical_party_columns_and_totals(self):
        values = parse_fields(COLUMN_TEXT)
        self.assertEqual(values['buyer_name'], '合成购方有限公司')
        self.assertEqual(values['seller_name'], '合成销方有限公司')
        self.assertEqual(values['buyer_tax_id'], 'TESTBUYER000000001')
        self.assertEqual(values['seller_tax_id'], 'TESTSELLER00000001')
        self.assertEqual(values['amount_untaxed'], 100)
        self.assertEqual(values['amount_tax'], 13)
        self.assertEqual(values['kind'], 'normal')
        # Never infer totals from a detail row or force a conflicting subtotal.
        mismatch = parse_fields(COLUMN_TEXT.replace('￥113.00', '￥112.00'))
        self.assertNotIn('amount_untaxed', mismatch)
        no_total = parse_fields(COLUMN_TEXT.replace('合 计                  ￥100.00             ￥13.00', ''))
        self.assertNotIn('amount_tax', no_total)
        ambiguous = parse_fields(COLUMN_TEXT.replace('购 名称', '名称').replace('销 名称', '名称'))
        self.assertNotIn('buyer_name', ambiguous)
        self.assertNotIn('buyer_tax_id', ambiguous)

    def test_recognition_detects_kind_and_resets_old_kind(self):
        wizard = self._wizard()
        self._recognize(wizard, text=COLUMN_TEXT, source='pdf_text')
        self.assertEqual(wizard.kind, 'normal')
        self.assertEqual(wizard.buyer_tax_id, 'TESTBUYER000000001')
        self.assertEqual(wizard.ocr_engine, 'PDF 本地文字层')
        self._recognize(wizard)
        self.assertEqual(wizard.kind, 'other')
        self.assertEqual(wizard.ocr_engine, 'Tesseract chi_sim+eng（本地）')

    def test_service_failure_keeps_wizard_unrecognized(self):
        wizard = self._wizard()
        with patch.object(type(self.env['qing.ocr.service']), '_extract_text',
                          side_effect=RecognitionError('invalid_image', 'The document could not be read.')):
            with self.assertRaisesRegex(UserError, 'The document could not be read'):
                wizard.action_recognize()
        self.assertFalse(wizard.recognized)
        self.assertFalse(wizard.reviewed)
        self.assertFalse(wizard.ocr_text)
        self.assertFalse(wizard.fapiao_id)

    def test_review_draft_only_and_idempotent(self):
        wizard = self._wizard()
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self._recognize(wizard)
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        wizard.reviewed = True
        before = self.env['account.move'].search_count([])
        action = wizard.action_create_draft()
        ticket = self.env['qing.fapiao'].browse(action['res_id'])
        self.assertEqual(ticket.state, 'draft')
        self.assertEqual(ticket.amount_total, 113)
        self.assertEqual(bytes(ticket.source_file), b'synthetic')
        self.assertIn(TEXT, ticket.note)
        self.assertEqual(wizard.action_create_draft()['res_id'], ticket.id)
        self.assertEqual(self.env['account.move'].search_count([]), before)
        with self.assertRaises(UserError):
            wizard.write({'source_filename': 'changed.png'})

    def test_replacement_invalidates_review_and_extraction(self):
        wizard = self._wizard()
        self._recognize(wizard)
        wizard.reviewed = True
        wizard.write({'source_file': BinaryBytes(b'new'), 'reviewed': True})
        self.assertFalse(wizard.recognized)
        self.assertFalse(wizard.reviewed)
        self.assertFalse(wizard.number)
        with self.assertRaises(UserError):
            wizard.action_create_draft()

    def test_amount_mismatch_and_duplicate(self):
        wizard = self._wizard()
        self._recognize(wizard)
        wizard.write({'invoice_total': 200, 'reviewed': True})
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        wizard.write({'invoice_total': 113, 'reviewed': True})
        wizard.action_create_draft()
        second = self._wizard()
        self._recognize(second)
        second.reviewed = True
        with self.assertRaises((ValidationError, IntegrityError)), self.cr.savepoint():
            second.action_create_draft()

    def test_access_and_forged_context(self):
        wizard = self._wizard()
        with self.assertRaises(AccessError):
            wizard.with_user(self.billing).action_recognize()
        with self.assertRaises(AccessError):
            wizard.write({'recognized': True})
        model = self.env['qing.fapiao.ocr'].with_user(self.manager).with_context(default_recognized=True, default_ocr_text=TEXT)
        forged = model.create({'source_file': BinaryBytes(b'x'), 'source_filename': 'x.png'})
        self.assertFalse(forged.recognized)
        self.assertFalse(forged.ocr_text)

    def test_temporary_ocr_is_private_in_odoo20(self):
        wizard = self._wizard()
        other = new_test_user(self.env, login='ocr_other_manager', groups='account.group_account_manager',
            company_id=self.env.company.id, company_ids=[Command.set(self.env.company.ids)])
        self.assertFalse(self.env['qing.fapiao.ocr'].with_user(other).search([('id', '=', wizard.id)]))
        with self.assertRaises(AccessError):
            wizard.with_user(other).read(['source_file'])
        with self.assertRaises(AccessError):
            wizard.with_user(other).action_recognize()
