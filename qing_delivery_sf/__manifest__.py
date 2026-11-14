{
    'name': 'SF Express',
    'summary': 'SF Express (顺丰速运) shipping connector: rates, waybills, labels, cancellation and tracking',
    'description': """
Connect SF Express (Fengqiao open platform) to QingERP delivery methods.

- Shipping rate estimate on sales orders.
- Waybill creation on validation or manually before validation.
- Electronic label (cloud print) attached to the transfer; reprint on demand.
- Cancellation with the stored customer order number.
- Route tracking stored on the transfer.
- Product type, payment method and cross-border declared value per carrier or per transfer.

Built on Qing Delivery Foundation: sender policy, order references, China structured
addresses and credential redaction are shared with other QingERP carriers.
Requires an SF Fengqiao account and the sf-sdk Python package.
""",
    'version': '20.0.1.0.1',
    'category': 'Inventory/Delivery',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_delivery'],
    'excludes': ['mommy_delivery_sf'],
    'external_dependencies': {
        'python': ['sf-sdk'],
    },
    'data': [
        'security/ir.access.csv',
        'data/qing.delivery.sf.template.csv',
        'data/delivery_carrier_data.xml',
        'views/view_delivery_carrier_form.xml',
        'views/view_stock_picking_form.xml',
        'views/view_res_partner_form.xml',
    ],
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
}
