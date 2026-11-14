{
    'name': 'WeChat Pay',
    'summary': 'WeChat Pay Native QR, H5 and JSAPI checkout for Odoo payments',
    'description': """
WeChat Pay provider for Odoo eCommerce, sales orders and customer invoices.

- Desktop Native QR code checkout with automatic status polling.
- Mobile H5 (MWEB) and in-WeChat JSAPI checkout.
- Signed payment notifications, manual status query and refunds.
""",
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'category': 'Accounting/Payment Providers',
    'version': '20.0.1.0.1',
    'license': 'LGPL-3',
    'depends': ['qing_payment', 'website'],
    'excludes': ['mommy_payment_wechatpay'],
    'external_dependencies': {
        'python': ['qrcode', 'odoo-wechatpy'],
    },
    'data': [
        'views/templates.xml',
        'security/data.xml',
        'views/views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
}
