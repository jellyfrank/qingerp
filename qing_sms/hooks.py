from odoo.exceptions import UserError


def pre_init_hook(env):
    modules = env['ir.module.module'].sudo().search([
        ('name', 'in', ['mommy_sms', 'mommy_aliyun_sms']),
        ('state', 'in', ['installed', 'to install', 'to upgrade']),
    ])
    if modules:
        raise UserError('请先执行 scripts/migrate_qing_sms.py 迁移已有短信模块；不要直接卸载旧模块。')
