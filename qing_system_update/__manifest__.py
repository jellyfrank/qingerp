{
    'name': 'QingERP System Update',
    'summary': 'Check and request QingERP updates from Settings',
    'description': """
Shows the installed and latest QingERP Community release in General Settings.
Administrators can request verified, maintenance-mode updates on supported self-hosted installations.
""",
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'category': 'Administration',
    'version': '20.0.1.0.2',
    'license': 'LGPL-3',
    'depends': ['base_setup'],
    'data': [
        'wizard/view_res_config_settings_form.xml',
    ],
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
}
