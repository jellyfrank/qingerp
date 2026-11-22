from odoo import SUPERUSER_ID, api

from odoo.addons.qing_base.tools.telemetry import HEARTBEAT_CRON_XMLID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref(HEARTBEAT_CRON_XMLID, raise_if_not_found=False)
    if not cron:
        return

    action = cron.ir_actions_server_id
    action.flush_recordset(['name'])
    # Updating the module keeps existing field translations by default.
    cr.execute(
        """
        UPDATE ir_act_server
           SET name = jsonb_set(name, '{zh_CN}', to_jsonb(%s::text))
         WHERE id = %s AND name->>'zh_CN' = %s
        RETURNING id
        """,
        ['心跳检测', action.id, 'QingERP：安装情况上报'],
    )
    if cr.fetchone():
        action.invalidate_recordset(['name'])
        action.modified(['name'])
