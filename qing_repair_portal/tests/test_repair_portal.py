import base64
import io
import json
import uuid
from unittest.mock import patch

from PIL import Image
from PyPDF2 import PdfReader
from lxml import html

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestRepairPortal(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Repair portal customer'})
        cls.service = cls.env['product.product'].create({'name': 'Portal repair labour', 'type': 'service', 'taxes_id': [Command.clear()]})
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        image = io.BytesIO()
        Image.new('RGB', (120, 50), 'white').save(image, format='PNG')
        cls.signature = base64.b64encode(image.getvalue()).decode()
        cls.operator = new_test_user(cls.env, login='repair_signature_operator', groups='stock.group_stock_user', company_id=cls.env.company.id)
        cls.portal_user = new_test_user(cls.env, login='repair_signature_portal', groups='base.group_portal')

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_str('report.url', self.base_url())

    def _repair(self, **values):
        return self.env['repair.order'].create({
            'partner_id': self.partner.id, 'picking_type_id': self.warehouse.repair_type_id.id,
            'qing_pricing_mode': 'direct', 'qing_price_signature_enabled': True,
            'qing_acceptance_enabled': True, 'qing_device_model': 'Printer test model',
            'qing_photo_before': self.signature, 'internal_notes': 'PRIVATE_REPAIR_NOTE_2392',
            'repair_service_line_ids': [Command.create({'product_id': self.service.id, 'quantity': 1, 'qing_price_unit': 80, 'qing_tax_ids': [Command.clear()]})],
            **values,
        })

    def test_signature_stale_price_lock_and_separate_acceptance(self):
        repair = self._repair()
        old_hash = repair._qing_price_hash()
        repair.repair_service_line_ids.qing_price_unit = 90
        with self.assertRaises(UserError):
            repair._qing_sign('price', 'Customer', self.signature, old_hash)
        repair._qing_sign('price', 'Customer', self.signature, repair._qing_price_hash())
        self.assertTrue(repair.qing_price_confirmed)
        with self.assertRaises(UserError):
            repair.repair_service_line_ids.write({'qing_price_unit': 99})
        with self.assertRaises(UserError):
            repair._qing_sign('acceptance', 'Customer', self.signature, repair._qing_price_hash())
        repair.action_validate()
        repair.action_repair_end()
        repair._qing_sign('acceptance', 'Customer', self.signature, repair._qing_price_hash())
        self.assertTrue(repair.qing_acceptance_signature)
        self.assertTrue(repair.qing_price_signature)

    def test_administrator_reopens_confirmation_without_losing_history(self):
        repair = self._repair()
        repair._qing_sign('price', 'Customer', self.signature, repair._qing_price_hash())
        attachments = repair.message_ids.attachment_ids
        repair.action_qing_reopen_price()
        self.assertFalse(repair.qing_price_signature)
        repair.repair_service_line_ids.qing_price_unit = 95
        self.assertTrue(attachments.exists())
        self.assertFalse(repair.qing_price_confirmed)

    def test_public_page_pdf_photos_and_invalid_tokens(self):
        repair = self._repair()
        other = self._repair()
        url = repair.get_portal_url()
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Printer test model', response.text)
        self.assertNotIn('PRIVATE_REPAIR_NOTE_2392', response.text)
        self.assertIn('portal.signature_form', response.text)
        props = json.loads(html.fromstring(response.text).xpath("//owl-component[@name='portal.signature_form']/@props")[0])
        self.assertIn('/sign/price?', props['callUrl'])
        self.assertIn('price_hash=', props['callUrl'])
        self.assertEqual(props['defaultName'], repair.partner_id.name)
        self.assertEqual(self.url_open('/my/repairs/%s' % repair.id).status_code, 404)
        self.assertEqual(self.url_open('/my/repairs/%s?access_token=%s' % (other.id, repair.access_token)).status_code, 404)
        self.assertEqual(self.url_open('/my/repairs/%s/photo/internal_notes?access_token=%s' % (repair.id, repair.access_token)).status_code, 404)
        photo = self.url_open('/my/repairs/%s/photo/qing_photo_before?access_token=%s' % (repair.id, repair.access_token))
        self.assertEqual(photo.status_code, 200)
        self.assertIn('image/', photo.headers['Content-Type'])
        report = self.url_open(repair.get_portal_url(report_type='html'))
        self.assertEqual(report.status_code, 200)
        self.assertNotIn('PRIVATE_REPAIR_NOTE_2392', report.text)
        pdf = self.url_open(repair.get_portal_url(report_type='pdf'))
        self.assertEqual(pdf.status_code, 200)
        self.assertIn('application/pdf', pdf.headers['Content-Type'])
        self.assertNotIn('PRIVATE_REPAIR_NOTE_2392', pdf.text)
        repair.access_token = str(uuid.uuid4())
        self.assertEqual(self.url_open(url).status_code, 404)

    def test_customer_pdf_binary(self):
        repair = self._repair()
        self.env['ir.config_parameter'].sudo().set_str('report.url', self.base_url())
        content, filetype = self.env['ir.actions.report'].with_context(force_report_rendering=True)._render_qweb_pdf(
            'qing_repair_portal.action_report_repair', res_ids=repair.ids,
        )
        self.assertEqual(filetype, 'pdf')
        self.assertTrue(content.startswith(b'%PDF'))
        text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)
        self.assertIn('Printer test model', text)

    def _signature_wizard(self, repair, kind='price', user=None):
        user = user or self.operator
        action = (repair.with_user(user).action_qing_sign_price() if kind == 'price'
                  else repair.with_user(user).action_qing_sign_acceptance())
        return self.env['qing.repair.signature'].with_user(user).with_context(action['context']).create({
            'signed_by': 'Onsite Customer', 'signature': self.signature,
        })

    def test_backend_signatures_attach_signed_pdf_and_audit_operator(self):
        repair = self._repair()
        self._signature_wizard(repair).action_sign()
        self.assertTrue(repair.qing_price_confirmed)
        pdf = repair.message_ids.attachment_ids.filtered(lambda item: item.mimetype == 'application/pdf')
        self.assertEqual(len(pdf), 1)
        self.assertTrue(pdf.raw.content.startswith(b'%PDF'))
        evidence = repair.message_ids.attachment_ids.filtered(lambda item: item.name == 'price-price-snapshot.json')
        self.assertEqual(json.loads(evidence.raw.content)['submitted_by'], self.operator.id)
        with self.assertRaises(UserError):
            self._signature_wizard(repair).action_sign()
        repair.action_validate()
        repair.action_repair_end()
        self._signature_wizard(repair, 'acceptance').action_sign()
        self.assertEqual(repair.qing_accepted_by, 'Onsite Customer')
        self.assertEqual(len(repair.message_ids.attachment_ids.filtered(lambda item: item.mimetype == 'application/pdf')), 2)
        download = repair.with_user(self.operator).action_qing_download_pdf()
        self.assertIn('report_type=pdf', download['url'])
        self.assertIn('download=true', download['url'])

    def test_backend_signing_rejects_stale_prices_and_premature_acceptance(self):
        repair = self._repair()
        wizard = self._signature_wizard(repair)
        repair.repair_service_line_ids.qing_price_unit = 81
        with self.assertRaises(UserError):
            wizard.action_sign()
        self.assertFalse(repair.qing_price_signature)
        with self.assertRaises(UserError):
            self._signature_wizard(repair, 'acceptance').action_sign()

    def test_backend_signing_has_no_portal_or_direct_write_bypass(self):
        repair = self._repair()
        with self.assertRaises(AccessError):
            repair.with_user(self.portal_user).action_qing_sign_price()
        with self.assertRaises(AccessError):
            repair.with_user(self.operator).write({'qing_price_signature': self.signature})
        other = self.env['res.company'].create({'name': 'Isolated signature company'})
        other_warehouse = self.env['stock.warehouse'].search([('company_id', '=', other.id)], limit=1)
        if not other_warehouse:
            other_warehouse = self.env['stock.warehouse'].create({'name': 'Other signature warehouse', 'code': 'SIG', 'company_id': other.id})
        other_repair = self._repair(company_id=other.id, picking_type_id=other_warehouse.repair_type_id.id)
        wizard = self.env['qing.repair.signature'].with_user(self.operator).create({
            'repair_id': other_repair.id, 'kind': 'price', 'signed_by': 'Customer',
            'signature': self.signature, 'price_hash': other_repair._qing_price_hash(),
        })
        with self.assertRaises(AccessError):
            wizard.action_sign()
        self.assertFalse(other_repair.qing_price_signature)

    def test_failed_pdf_does_not_leave_a_partial_signature(self):
        repair = self._repair()
        wizard = self._signature_wizard(repair)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            with patch.object(type(self.env['ir.actions.report']), '_render_qweb_pdf', return_value=(b'not a PDF', 'pdf')):
                wizard.action_sign()
        self.assertFalse(repair.qing_price_signature)
        self.assertFalse(repair.qing_price_signed_by)
        self.assertFalse(repair.message_ids.attachment_ids)

    def test_company_signature_settings_do_not_change_existing_repairs(self):
        self.env.company.qing_repair_price_signature_enabled = False
        repair = self._repair(qing_price_signature_enabled=False)
        self.env.company.qing_repair_price_signature_enabled = True
        self.assertFalse(repair.qing_price_signature_enabled)
        new = self.env['repair.order'].create({'picking_type_id': self.warehouse.repair_type_id.id})
        self.assertTrue(new.qing_price_signature_enabled)

    def test_signed_parts_can_be_consumed_but_not_repriced(self):
        repair = self._repair()
        part = self.env['product.product'].create({'name': 'Signed repair part', 'type': 'consu', 'taxes_id': [Command.clear()]})
        move = self.env['stock.move'].create({
            'repair_id': repair.id, 'product_id': part.id, 'product_uom_qty': 1,
            'repair_line_type': 'add', 'company_id': repair.company_id.id,
            'qing_price_unit': 25, 'qing_tax_ids': [Command.clear()],
        })
        repair._qing_sign('price', 'Customer', self.signature, repair._qing_price_hash())
        repair.action_validate()
        move.quantity = 1
        with self.assertRaises(UserError):
            move.write({'qing_price_unit': 26})
        repair.action_repair_end()
        self.assertEqual(repair.state, 'done')
        self.assertTrue(repair.qing_price_confirmed)

    def test_public_signature_rpc_requires_token_and_current_price(self):
        repair = self._repair()
        values = {'jsonrpc': '2.0', 'method': 'call', 'params': {'name': 'Customer', 'signature': self.signature}, 'id': 1}
        headers = {'Content-Type': 'application/json'}
        denied = self.url_open('/my/repairs/%s/sign/price' % repair.id, data=json.dumps(values), headers=headers).json()
        self.assertIn('error', denied['result'])
        url = repair.get_portal_url(suffix='/sign/price', query_string='price_hash=' + repair._qing_price_hash())
        accepted = self.url_open(url, data=json.dumps(values), headers=headers).json()
        self.assertTrue(accepted['result']['force_refresh'])
        repair.invalidate_recordset()
        self.assertTrue(repair.qing_price_confirmed)

    def test_chinese_translations_and_portal_labels(self):
        self.env['res.lang']._activate_lang('zh_CN')
        self.env['ir.module.module']._load_module_terms(['qing_repair', 'qing_repair_portal', 'bgwy_repair'], ['zh_CN'], overwrite=True)
        Repair = self.env['repair.order'].with_context(lang='zh_CN')
        self.assertEqual(Repair.fields_get(['qing_device_model'])['qing_device_model']['string'], '设备型号')
        selections = dict(Repair.fields_get(['qing_pricing_mode'])['qing_pricing_mode']['selection'])
        self.assertEqual(selections['sale'], '关联报价单')
        self.assertEqual(selections['direct'], '直接计价')
        response = self.url_open('/web/webclient/translations?mods=qing_repair,qing_repair_portal,bgwy_repair&lang=zh_CN')
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), dict)
        repair = self._repair()
        repair.partner_id.lang = 'zh_CN'
        page = self.url_open(repair.get_portal_url())
        self.assertEqual(page.status_code, 200)
        self.assertIn('设备型号', page.text)
        self.assertIn('服务说明', page.text)

    def test_signature_hash_is_independent_of_translation_language(self):
        self.env['res.lang']._activate_lang('zh_CN')
        repair = self._repair()
        service = repair.repair_service_line_ids
        service.description = 'Repair labour'
        service.with_context(lang='zh_CN').description = '维修服务'
        chinese = repair.with_context(lang='zh_CN')
        english = repair.with_context(lang='en_US')
        self.assertEqual(chinese._qing_price_hash(), english._qing_price_hash())
        chinese._qing_sign('price', 'Customer', self.signature, chinese._qing_price_hash())
        self.assertTrue(english.qing_price_confirmed)
