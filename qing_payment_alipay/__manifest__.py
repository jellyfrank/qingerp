{
    'name': 'Alipay Payment',
    'summary': 'Mainland Alipay and Antom global checkout for Odoo payments',
    'description': """
Connect Mainland China Alipay and Antom global checkout to Odoo eCommerce,
sales orders and customer invoices.

- Production and sandbox credentials.
- Signed domestic notifications and server-side trade queries.
- Antom global payment inquiry and idempotent, queued refunds.
""",
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'category': 'Accounting/Payment Providers',
    'version': '20.0.1.0.0',
    'license': 'LGPL-3',
    'depends': ['qing_payment'],
    'excludes': ['mommy_payment_alipay'],
    'external_dependencies': {
        'python': ['alipay-sdk', 'pycryptodome'],
    },
    'data': [
        'views/views.xml',
        'views/view_payment_transaction_form.xml',
        'views/templates.xml',
        'security/data.xml',
        'data/ir_cron.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
}
