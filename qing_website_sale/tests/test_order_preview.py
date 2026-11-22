from lxml import html

from odoo import Command
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestOrderPreview(HttpCase):

    def test_portal_order_with_blank_note(self):
        customer = self.env['res.partner'].create({'name': 'Portal Preview Customer'})
        product = self.env['product.product'].create({
            'name': 'Portal Preview Service',
            'type': 'service',
            'default_code': 'PREVIEW-SERVICE',
        })
        order = self.env['sale.order'].create({
            'partner_id': customer.id,
            'order_line': [
                Command.create({'product_id': product.id}),
                Command.create({'display_type': 'line_note', 'name': False, 'sequence': 20}),
                Command.create({'display_type': 'line_note', 'name': 'Delivery instructions', 'sequence': 30}),
            ],
        })
        order.action_confirm()

        response = self.url_open(order.get_portal_url())

        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.content)
        self.assertEqual(
            page.xpath("normalize-space(//*[@name='span_product_name'])"),
            product.name,
        )
        self.assertIn('Delivery instructions', page.xpath("//*[@name='sol_table']")[0].text_content())
