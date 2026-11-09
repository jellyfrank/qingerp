from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.qing_sms.tests.test_sms import SmsFixture


@tagged('post_install', '-at_install')
class TestStockSms(SmsFixture):
    def test_done_hook_captures_one_delivery_event(self):
        self.remote._write_synced({'variables': ['consignee', 'phone'],
            'content': '收件人 ${consignee} 电话 ${phone}',
            'normalized_content': '收件人 ${consignee} 电话 ${phone}'})
        template = self.env['sms.template'].create({'name': 'Delivery SMS', 'body': 'Delivery SMS',
            'model_id': self.env['ir.model']._get('stock.picking').id})
        binding = self.env['qing.sms.binding'].create({'name': 'Delivery', 'account_id': self.account.id,
            'remote_template_id': self.remote.id, 'sign_id': self.sign.id, 'template_id': template.id})
        binding.action_prepare_variables()
        binding.action_review()
        self.sender.group_ids = [Command.link(self.env.ref('stock.group_stock_user').id)]
        scenario = self.env['qing.sms.scenario'].create({'name': 'Delivery',
            'company_id': self.env.company.id, 'event_code': 'stock.picking.delivered',
            'model_id': template.model_id.id, 'sender_user_id': self.sender.id})
        scenario.action_set_binding(binding)
        scenario.action_enable()
        partner = self.env['res.partner'].create({'name': 'Recipient', 'phone': '13800138000'})
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'), ('company_id', '=', self.env.company.id)], limit=1)
        picking = self.env['stock.picking'].create({'partner_id': partner.id,
            'picking_type_id': picking_type.id, 'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id})
        product = self.env['product.product'].create({'name': 'SMS test goods', 'type': 'consu'})
        move = self.env['stock.move'].create({'product_id': product.id,
            'product_uom_qty': 1, 'uom_id': product.uom_id.id, 'picking_id': picking.id,
            'location_id': picking.location_id.id, 'location_dest_id': picking.location_dest_id.id})
        picking.action_confirm()
        move.quantity = 1
        with patch('odoo.addons.stock_sms.models.stock_picking.modules.module.current_test', False):
            result = picking.button_validate()
            if isinstance(result, dict) and result.get('res_model') == 'confirm.stock.sms':
                self.env['confirm.stock.sms'].browse(result['res_id']).with_context(result['context']).send_sms()
        self.assertEqual(picking.state, 'done', f'validate returned {result}')
        key = f'stock.picking:{picking.id}:delivery'
        event = self.env['qing.sms.event'].search([('event_key', '=', key)])
        self.assertEqual(len(event), 1)
        self.assertEqual(event.state, 'pending', f'{event.reason_code}: {event.reason_message}')
        self.assertEqual(picking.qing_sms_status, 'Waiting for preparation')
        self.assertTrue(event.mail_message_id)
        message = event._prepare_one()
        self.assertEqual(message.state, 'queued')
        self.assertEqual(message.event_id, event)
        self.assertEqual(message.sms_id.mail_message_id, event.mail_message_id)
        self.assertEqual(len(self.env['mail.notification'].search([('sms_id_int', '=', message.sms_id.id)])), 1)
        self.adapter.send.assert_not_called()

    def test_stock_notification_enqueued_once_with_company(self):
        self.env.company.qing_sms_delivery_binding_id = self.binding
        self.remote._write_synced({'variables': ['consignee', 'phone'],
            'content': '收件人 ${consignee} 电话 ${phone}', 'normalized_content': '收件人 ${consignee} 电话 ${phone}'})
        self.binding.variable_ids.unlink()
        self.binding.action_prepare_variables()
        self.binding.action_review()
        partner = self.env['res.partner'].create({'name': 'Test recipient', 'phone': '+8613800138000'})
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'), ('company_id', '=', self.env.company.id)], limit=1)
        picking = self.env['stock.picking'].create({'partner_id': partner.id, 'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id})
        with self.assertRaises(UserError):
            picking.action_qing_send_sms()
        product = self.env['product.product'].create({'name': 'Manual SMS goods', 'type': 'consu'})
        move = self.env['stock.move'].create({'product_id': product.id, 'product_uom_qty': 1,
            'uom_id': product.uom_id.id, 'picking_id': picking.id,
            'location_id': picking.location_id.id, 'location_dest_id': picking.location_dest_id.id})
        picking.action_confirm()
        move.quantity = 1
        result = picking.button_validate()
        if isinstance(result, dict) and result.get('res_model') == 'confirm.stock.sms':
            self.env['confirm.stock.sms'].browse(result['res_id']).with_context(result['context']).send_sms()
        self.assertEqual(picking.state, 'done')
        self.sender.group_ids = [Command.link(self.env.ref('stock.group_stock_user').id)]
        native_template = self.env['sms.template'].create({'name': 'Native delivery test',
            'body': 'Native delivery test', 'model_id': self.env['ir.model']._get('stock.picking').id})
        native_binding = self.env['qing.sms.binding'].create({'name': 'Native delivery test',
            'account_id': self.account.id, 'remote_template_id': self.remote.id,
            'sign_id': self.sign.id, 'template_id': native_template.id})
        native_binding.action_prepare_variables()
        native_binding.action_review()
        native_mail = picking.with_user(self.sender)._message_sms_with_template(
            template=native_template, partner_ids=partner.ids)
        self.assertFalse(self.env['sms.sms'].search([('mail_message_id', '=', native_mail.id)]))
        self.assertFalse(self.env['qing.sms.message'].search([
            ('event_key', '=', f'stock.picking:{picking.id}:delivery')]))
        picking.with_user(self.sender).action_qing_send_sms()
        picking.with_user(self.sender).action_qing_send_sms()
        messages = self.env['qing.sms.message'].search([('event_key', '=', f'stock.picking:{picking.id}:delivery')])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages.company_id, picking.company_id)
        self.assertEqual(messages.state, 'queued')
        self.assertTrue(messages.sms_id.mail_message_id)
        self.assertEqual(messages.event_id.trigger, 'manual')
        messages._preflight()
        self.adapter.send.assert_not_called()
