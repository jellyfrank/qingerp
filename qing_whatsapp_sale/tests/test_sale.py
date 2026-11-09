import json
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError, UserError
from odoo.tests.common import TransactionCase, new_test_user


class TestWhatsappSale(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = new_test_user(cls.env, login='wa_sales', groups='qing_whatsapp.group_user,sales_team.group_sale_salesman')
        cls.partner = cls.env['res.partner'].create({'name': 'Quotation customer', 'phone': '+32499000000'})
        cls.account = cls.env['qing.whatsapp.account'].create({'name': 'Sales WA', 'phone_uid': '321654',
            'waba_uid': '321654', 'operator_ids': [(6, 0, cls.operator.ids)]})
        cls.order = cls.env['sale.order'].with_user(cls.operator).create({'partner_id': cls.partner.id, 'user_id': cls.operator.id})
        cls.conversation = cls.env['qing.whatsapp.conversation']._find_or_create(cls.account, cls.partner.phone, cls.partner)
        cls.conversation.last_customer_at = fields.Datetime.now()

    def wizard(self):
        with patch.object(type(self.env['ir.actions.report']), '_render_qweb_pdf', return_value=(b'%PDF-1.4 test', 'pdf')):
            action = self.order.action_qing_whatsapp()
        return self.env['qing.whatsapp.compose'].with_user(self.operator).browse(action['res_id'])

    def test_pdf_link_queue_and_business_association(self):
        wizard = self.wizard()
        self.assertIn('/my/orders/', wizard.body)
        wizard.action_send()
        message = self.env['qing.whatsapp.message'].search([('sale_order_id', '=', self.order.id)])
        self.assertEqual(message.kind, 'document')
        self.assertEqual(message.attachment_id.raw.content, b'%PDF-1.4 test')
        self.assertEqual(self.order.qing_conversation_id, self.conversation)
        self.assertTrue(message.discuss_message_id)
        self.assertEqual(message.sender_id, self.operator)

    def test_template_mapping_and_closed_window(self):
        wizard = self.wizard()
        self.conversation.last_customer_at = False
        template = self.env['qing.whatsapp.template'].create({'name': 'quotation_pdf', 'account_id': self.account.id,
            'status': 'APPROVED', 'components_json': json.dumps([{'type': 'HEADER', 'format': 'DOCUMENT'},
                {'type': 'BODY', 'text': 'Hello {{1}}, order {{2}}: {{3}}'}]),
            'sale_variable_ids': [(0, 0, {'source': source, 'position': i}) for i, source in enumerate(['customer', 'order', 'portal_url'], 1)]})
        wizard.write({'kind': 'template', 'template_id': template.id})
        wizard._fill_sale_variables()
        self.assertEqual(wizard.parameter_values.splitlines()[0], self.partner.name)
        wizard.action_send()
        message = self.env['qing.whatsapp.message'].search([('sale_order_id', '=', self.order.id)])
        self.assertEqual(message.template_id, template)
        self.assertEqual(message.media_kind, 'document')

    def test_changed_order_or_recipient_is_rejected(self):
        wizard = self.wizard()
        wizard.number = '+32499000001'
        with self.assertRaises(ValidationError):
            wizard.action_send()
        wizard.number = self.partner.phone
        self.order.note = 'Changed after preview'
        self.order.flush_recordset()
        self.env.cr.execute("UPDATE sale_order SET write_date=write_date + interval '1 second' WHERE id=%s", [self.order.id])
        self.order.invalidate_recordset()
        with self.assertRaises(ValidationError):
            wizard.action_send()

    def test_send_time_permissions_and_cancelled_order(self):
        wizard = self.wizard()
        wizard.action_send()
        message = self.env['qing.whatsapp.message'].search([('sale_order_id', '=', self.order.id)])
        self.order.state = 'cancel'
        with self.assertRaises(UserError):
            message._preflight()

    def test_create_quotation_needs_contact(self):
        self.conversation.partner_id = False
        with self.assertRaises(UserError):
            self.conversation.with_user(self.operator).action_new_quotation()
