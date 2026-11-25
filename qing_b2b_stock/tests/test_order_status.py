from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestB2BOrderStatus(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env["qing.b2b.checkout"]
        partner = cls.env["res.partner"].create({"name": "Status Buyer"})
        cls.product = cls.env["product.product"].create({"name": "Status Tea", "is_storable": True, "list_price": 10})
        warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.env.company.id)], limit=1)
        cls.env["stock.quant"]._update_available_quantity(cls.product, warehouse.lot_stock_id, 10)
        cls.order = cls.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [Command.create({"product_id": cls.product.id, "product_uom_qty": 4})],
        })

    def _ship(self, quantity):
        picking = self.order.picking_ids.filtered(lambda record: record.state not in ("done", "cancel"))
        picking.action_assign()
        picking.move_ids.write({"quantity": quantity, "picked": True})
        picking.with_context(skip_backorder=True, skip_sms=True).button_validate()

    def _summary(self):
        self.order.invalidate_recordset()
        return self.service._order_summary(self.order), self.service._line_values(self.order.order_line)

    def test_quotation_reports_no_shipment(self):
        summary, line = self._summary()
        self.assertNotIn("delivery", summary)
        self.assertNotIn("delivered", line)

    def test_confirmed_order_reports_shipment_progress(self):
        self.order.action_confirm()
        summary, line = self._summary()
        self.assertEqual((summary["state"], summary["delivery"], line["delivered"]), ("sale", "pending", 0))
        self._ship(1)
        summary, line = self._summary()
        self.assertEqual((summary["delivery"], line["delivered"]), ("partial", 1))
        self._ship(3)
        summary, line = self._summary()
        self.assertEqual((summary["delivery"], line["delivered"]), ("full", 4))
