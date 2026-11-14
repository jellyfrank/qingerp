from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestNativeFormCompatibility(TransactionCase):
    def test_native_form_reads_existing_partner_without_adapter(self):
        partner = self.env['res.partner'].create({'name': 'Form compatibility'})
        with Form(partner) as form:
            self.assertEqual(form.name, 'Form compatibility')
