{
    'name': 'WeCom / 企业微信',
    'summary': 'WeCom employee and department sync, QR login, messages, robots, menus and workbench',
    'description': '''
        QingERP WeCom (企业微信) integration, ported from the Ohm Solutions WeWork module.

        * Employee and department sync
        * OAuth / QR code login
        * Application messages and message templates
        * Group robots and scheduled robot messages
        * Intelligent bot (callback and WebSocket)
        * Application menus and workbench
        * Multi-company
    ''',
    'author': 'Qingdao Ohm Network Technology Co., Ltd.',
    'website': 'https://www.qingsolution.com/',
    'support': 'kevin@odoomommy.com',
    'category': 'Productivity',
    'version': '20.0.1.0.2',
    'license': 'LGPL-3',
    'depends': ['qing_base', 'hr', 'auth_oauth'],
    # Same models as the Solutions module: never install both.
    'excludes': ['mommy_wework'],
    'external_dependencies': {
        'python': ['odoo-wechatpy'],
    },
    'data': [
        'security/data.xml',
        'security/group.xml',
        'security/ir.access.csv',
        'views/views.xml',
        'views/res_company.xml',
        'views/hr_department.xml',
        'views/wework_message.xml',
        'views/wework_message_template.xml',
        'views/wework_media.xml',
        'views/ouath.xml',
        'views/hr_employee.xml',
        'views/wework.xml',
        'views/settings.xml',
        'views/res_users.xml',
        'views/contact_sync.xml',
        'views/wework_robot.xml',
        'views/wework_intelligent_bot.xml',
        'views/wework_intelligent_bot_media_message.xml',
        'views/robot_schedule.xml',
        'data/ir_cron.xml',
        'views/wework_menu.xml',
        'views/wework_workbench.xml',
        'wizard/wework_confirm_wizard.xml',
    ],
    'assets': {},
    'images': ['static/description/icon.png'],
    'application': True,
    'installable': True,
}
