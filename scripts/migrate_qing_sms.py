"""Odoo 20 --pre-upgrade-scripts entrypoint; requires official odoo/upgrade-util.

Run ONLY with all application/cron workers stopped and a verified backup.
This script changes metadata before registry loading. Never uninstall old modules.
Use -u qing_sms,qing_sms_aliyun (and qing_sms_stock if old plus was installed).
"""
from odoo.upgrade import util

MODULES = (
    ('mommy_sms', 'qing_sms'),
    ('mommy_aliyun_sms', 'qing_sms_aliyun'),
    ('mommy_aliyun_sms_plus', 'qing_sms_stock'),
)


def migrate(cr, version):
    cr.execute("SELECT name, state FROM ir_module_module WHERE name IN %s", [tuple(x for pair in MODULES for x in pair)])
    states = dict(cr.fetchall())
    if states.get('mommy_sms') != 'installed':
        if states.get('qing_sms') in ('installed', 'to upgrade'):
            return  # already migrated; must not disable an account again
        raise RuntimeError('Expected installed mommy_sms; use normal installation for a fresh database')
    cr.execute("SELECT state FROM ir_module_module WHERE name='qing_base'")
    if cr.fetchone() != ('installed',):
        raise RuntimeError('Install qing_base before migrating the SMS namespace')
    for old, new in MODULES:
        if new in states:
            cr.execute('SELECT 1 FROM ir_model_data WHERE module=%s LIMIT 1', [new])
            if states[new] != 'uninstalled' or cr.fetchone():
                raise RuntimeError('Destination has installed state or owned data: ' + new)
            # An Apps-list scan may have registered the new name without installing it.
            util.remove_module(cr, new)
        if states.get(old) not in (None, 'installed', 'uninstalled'):
            raise RuntimeError('Resolve pending module operations before migration: ' + old)
    cr.execute("SELECT model FROM ir_model WHERE model LIKE 'mommy.sms.%' OR model LIKE 'mommy.aliyun.sms.%'")
    models = [row[0] for row in cr.fetchall()]
    for old, new in MODULES:
        if old in states:
            util.rename_module(cr, old, new)
    for old in models:
        new = old.replace('mommy.aliyun.sms.', 'qing.sms.aliyun.').replace('mommy.sms.', 'qing.sms.')
        util.rename_model(cr, old, new)
    # Update persisted XMLID/model literals used in inherited views and server actions.
    replacements = [(old + '.', new + '.') for old, new in MODULES]
    replacements += [('mommy.aliyun.sms.', 'qing.sms.aliyun.'), ('mommy.sms.', 'qing.sms.')]
    for old, new in replacements:
        cr.execute("UPDATE ir_act_server SET code=replace(code,%s,%s) WHERE strpos(code,%s)>0", [old, new, old])
        cr.execute("UPDATE ir_ui_view SET arch_db=replace(arch_db::text,%s,%s)::jsonb WHERE strpos(arch_db::text,%s)>0", [old, new, old])
    cr.execute("""UPDATE qing_sms_account SET enabled=false, auto_sync=false""")
    cr.execute("UPDATE qing_sms_binding SET review_hash=NULL")
    cr.execute("""UPDATE ir_cron SET active=false WHERE id IN
                  (SELECT res_id FROM ir_model_data WHERE module='qing_sms' AND model='ir.cron')""")
    # Native extension field ownership follows renamed modules; existing sms IDs/UUIDs and FK values stay intact.
    # Record names owned by modules but generated from model names are renamed by util.rename_model.
