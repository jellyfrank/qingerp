from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleLabels(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('zh_CN')
        cls.env['ir.module.module'].search([('name', '=', 'qing_sale')])._update_translations(
            filter_lang='zh_CN', overwrite=True,
        )

    def _form_arch(self, lang):
        return etree.fromstring(self.env.ref('sale.view_order_form').with_context(lang=lang).get_combined_arch())

    def test_proforma_labels_in_both_states_are_chinese(self):
        arch = self._form_arch('zh_CN')
        for button_id in ('send_proforma_primary', 'send_proforma'):
            [button] = arch.xpath(f"//button[@id='{button_id}']")
            self.assertEqual(button.get('string'), '发送形式发票')
            self.assertEqual(button.get('name'), 'action_quotation_send')
            self.assertIn('proforma', button.get('context'))
            self.assertEqual(button.get('groups'), 'sale.group_proforma_sales')

    def test_order_line_controls_are_chinese(self):
        arch = self._form_arch('zh_CN')
        for name, label in (('add_product_control', '添加行'),
                            ('add_section_control', '添加章节'),
                            ('add_note_control', '添加备注')):
            [control] = arch.xpath(f"//list[@name='sol_list']/control/create[@name='{name}']")
            self.assertEqual(control.get('string'), label)
        [section] = arch.xpath("//list[@name='sol_list']/control/create[@name='add_section_control']")
        [note] = arch.xpath("//list[@name='sol_list']/control/create[@name='add_note_control']")
        self.assertIn('line_section', section.get('context'))
        self.assertIn('line_note', note.get('context'))

    def test_english_labels_and_proforma_conditions_are_retained(self):
        arch = self._form_arch('en_US')
        for button_id in ('send_proforma_primary', 'send_proforma'):
            [button] = arch.xpath(f"//button[@id='{button_id}']")
            self.assertEqual(button.get('string'), 'Send PRO FORMA Invoice')
        labels = {
            control.get('name'): control.get('string')
            for control in arch.xpath("//list[@name='sol_list']/control/create")
        }
        self.assertEqual(labels['add_product_control'], 'Add Line')
        self.assertEqual(labels['add_section_control'], 'Add Section')
        self.assertEqual(labels['add_note_control'], 'Add Note')
        mobile = {
            control.get('name'): control.get('string')
            for control in arch.xpath("//field[@name='order_line']/kanban/control/create")
        }
        self.assertEqual(mobile['add_product_control'], 'Line')
        self.assertEqual(mobile['add_section_control'], 'Section')
        self.assertEqual(mobile['add_note_control'], 'Note')

    def test_mobile_order_line_controls_are_chinese(self):
        arch = self._form_arch('zh_CN')
        for name, label in (('add_product_control', '添加行'),
                            ('add_section_control', '添加章节'),
                            ('add_note_control', '添加备注')):
            [control] = arch.xpath(f"//field[@name='order_line']/kanban/control/create[@name='{name}']")
            self.assertEqual(control.get('string'), label)
