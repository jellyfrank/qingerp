{
    'name': 'Qing Sale Purchase',
    'summary': 'Create partial, vendor-specific RFQs from confirmed sales orders',
    'version': '20.0.1.6',
    'category': 'Sales/Sales',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'sale_purchase'],
    'data': [
        'security/ir.access.csv',
        'wizard/view_purchase_wizard_form.xml',
        'views/view_sale_order_form.xml',
        'views/view_purchase_order_form.xml',
    ],
    'installable': True,
    'description': 'Select quantities and vendors on confirmed sales orders to create partial purchase RFQs.',
}
