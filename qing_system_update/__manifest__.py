{
    'name': 'QingERP System Update',
    'summary': 'Check QingERP release information from Settings',
    'description': """
Shows the installed and latest QingERP Community release in General Settings.
Update execution is not yet available in this development version.
""",
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'category': 'Administration',
    'version': '20.0.1.0.1',
    'license': 'LGPL-3',
    'depends': ['base_setup'],
    'data': [
        'wizard/view_res_config_settings_form.xml',
    ],
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,
}
