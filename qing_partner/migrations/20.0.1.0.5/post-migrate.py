from odoo import SUPERUSER_ID, api


OLD_MENU_TRANSLATIONS = (
    ('menu_roles', '业务角色', '角色'),
    ('menu_team', '业务团队', '团队'),
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xml_id, old_label, new_label in OLD_MENU_TRANSLATIONS:
        menu = env.ref(f'qing_partner.{xml_id}', raise_if_not_found=False)
        if not menu:
            continue
        cr.execute(
            """UPDATE ir_ui_menu
                  SET name = jsonb_set(name, '{zh_CN}', to_jsonb(%s::text))
                WHERE id = %s AND name->>'zh_CN' = %s""",
            (new_label, menu.id, old_label),
        )
