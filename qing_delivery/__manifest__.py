{
    'name': 'Qing Delivery Foundation',
    'summary': 'Shared building blocks for China carrier connectors',
    'description': """
Shared foundation for QingERP carrier connectors such as SF Express.

- Sender address policy: company, warehouse or a fixed contact.
- Customer order reference policy, stored per transfer so cancellations reuse it.
- Default goods description per delivery method, overridable per transfer.
- Contact payloads built from the QingERP China address structure
  (province, city, district, subdistrict) with phone normalization.
- Carrier calls wrapped with credential redaction and readable errors.
- Shipment labels attached to the transfer chatter.
- Stored tracking history refreshed on demand.
""",
    'version': '20.0.1.0.0',
    'category': 'Inventory/Delivery',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'qing_address_cn', 'stock_delivery'],
    'data': [
        'views/view_delivery_carrier_form.xml',
        'views/view_stock_picking_form.xml',
    ],
    'installable': True,
    'application': False,
}
