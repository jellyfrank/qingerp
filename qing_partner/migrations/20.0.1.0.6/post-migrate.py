from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    view = env.ref('qing_partner.view_partner_role_search', raise_if_not_found=False)
    if not view:
        return

    # Existing translated architectures can retain the removed modifier.
    cr.execute(
        """UPDATE ir_ui_view
              SET arch_db = jsonb_set(
                  arch_db, '{zh_CN}',
                  to_jsonb(replace(
                      arch_db->>'zh_CN',
                      %s, %s
                  ))
              )
            WHERE id = %s
              AND arch_db->>'zh_CN' LIKE %s""",
        (
            '<field name="team_id" invisible="role != \'supplier\'"/>',
            '<field name="team_id"/>',
            view.id,
            '%<field name="team_id" invisible="role != \'supplier\'"/>%',
        ),
    )
