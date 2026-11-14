{
    'name': 'Payment Foundation for China',
    'summary': 'Shared helpers for Alipay and WeChat Pay providers',
    'description': """
Shared foundation for the QingERP Alipay and WeChat Pay payment providers.

- Marks transactions started from a mobile page so providers can choose a
  mobile checkout (H5 / JSAPI).
""",
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'category': 'Accounting/Payment Providers',
    'version': '20.0.1.0.1',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'payment'],
    'excludes': ['mommy_payment'],
    'assets': {
        'web.assets_frontend': [
            'qing_payment/static/src/js/payment_form.js',
        ],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
}
