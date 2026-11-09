"""Execute with exec(compile(open(path).read(), path, 'exec'), dict(globals(), __file__=path))."""
import runpy
assert env.cr.dbname == 'sms_qing_migrate'
a = env['qing.sms.account'].search([], limit=1)
a.enabled = True
env.flush_all()
runpy.run_path(__file__.replace('/sms/verify_boundary.py', '/migrate_qing_sms.py'))['migrate'](env.cr, '')
env.invalidate_all()
assert a.enabled
from odoo.addons.qing_sms.hooks import pre_init_hook
# A conflicting installed native adapter must block install, before any external action.
native = env['ir.module.module'].search([('name','=','mommy_aliyun_sms')], limit=1)
native.state = 'installed'
from odoo.exceptions import UserError
try:
    pre_init_hook(env)
except UserError:
    pass
else:
    raise AssertionError('Native/Qing conflict not rejected')
env.cr.rollback()
print('REPEAT_AND_BOUNDARY_PASS')
