{
    'name': 'Qing Website Sale',
    'summary': 'Require login to see product prices in the online shop',
    'version': '20.0.1.0.3',
    'category': 'Website/eCommerce',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'website_sale'],
    'data': [
        'wizard/view_res_config_settings_form.xml',
        'views/website_sale_templates.xml',
    ],
    'application': False,
    'installable': True,
    'description': 'Configure login-required pricing per website. Visitors can browse products and must sign in before purchasing when enabled.',
}
