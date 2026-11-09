"""Bridge Website's required column while its ORM extension is not loaded yet."""


def prepare_view_visibility(cr):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = 'ir_ui_view'
           AND column_name = 'visibility' AND is_nullable = 'NO'
           AND column_default IS NULL
    """)
    if not cr.fetchone():
        return
    # Transaction-local marker: rollback restores both DDL and the marker.
    cr.execute("""
        CREATE TEMP TABLE IF NOT EXISTS qing_view_visibility_guard
        (restore_default boolean NOT NULL) ON COMMIT DROP
    """)
    cr.execute("INSERT INTO qing_view_visibility_guard VALUES (true)")
    cr.execute("ALTER TABLE ir_ui_view ALTER COLUMN visibility SET DEFAULT 'public'")


def restore_view_visibility(cr):
    cr.execute("SELECT to_regclass('pg_temp.qing_view_visibility_guard')")
    if not cr.fetchone()[0]:
        return
    cr.execute("SELECT 1 FROM qing_view_visibility_guard WHERE restore_default LIMIT 1")
    if not cr.fetchone():
        return
    cr.execute("ALTER TABLE ir_ui_view ALTER COLUMN visibility DROP DEFAULT")
    cr.execute("DELETE FROM qing_view_visibility_guard")


def pre_init_hook(env):
    prepare_view_visibility(env.cr)


def post_init_hook(env):
    restore_view_visibility(env.cr)
    import_legacy_mommy_base_settings(env)
    initialize_brand_title(env)
    initialize_report_print_preview(env)
    initialize_product_units_and_packagings(env)
    initialize_customer_signup_invitation(env)
    disable_odoo_oauth_provider_by_default(env)
    disable_publisher_warranty_notification(env)
    schedule_installation_heartbeat(env)


def initialize_brand_title(env):
    """Initialize the shared title without replacing a customer customization."""
    parameters = env['ir.config_parameter'].sudo()
    if parameters.get_str('qing_base.title', default='') in ('', 'Odoo'):
        parameters.set_str('qing_base.title', 'QingERP 20.0')


def initialize_report_print_preview(env):
    """Enable preview as the product default without resetting later choices."""
    parameters = env['ir.config_parameter'].sudo()
    existing = parameters.search([('key', '=', 'qing_base.report.print_preview')], limit=1)
    if not existing:
        parameters.set_bool('qing_base.report.print_preview', True)


def initialize_product_units_and_packagings(env):
    """Enable Odoo's native UoM setting when QingERP is first installed."""
    internal_users = env.ref('base.group_user')
    units = env.ref('uom.group_uom')
    internal_users._apply_group(units)


def initialize_customer_signup_invitation(env):
    """Use invitation-only portal signup on a newly installed QingERP database."""
    parameters = env['ir.config_parameter'].sudo()
    if parameters.get_str('auth_signup.invitation_scope', default='') in ('', 'b2c'):
        parameters.set_str('auth_signup.invitation_scope', 'b2b')


def disable_odoo_oauth_provider_by_default(env):
    """Keep Odoo.com sign-in opt-in on a newly installed QingERP database."""
    provider = env.ref('auth_oauth.provider_openerp', raise_if_not_found=False)
    if provider and provider.enabled:
        provider.sudo().write({'enabled': False})


def disable_publisher_warranty_notification(env):
    """Stop the native weekly publisher job; the model override guards direct calls."""
    cron = env.ref('mail.ir_cron_module_update_notification', raise_if_not_found=False)
    if cron and cron.active:
        cron.sudo().write({'active': False})


def schedule_installation_heartbeat(env):
    """Delay first outbound heartbeat until after the installation transaction."""
    import secrets
    from datetime import timedelta

    from odoo import fields

    cron = env.ref('qing_base.ir_cron_qing_telemetry_heartbeat', raise_if_not_found=False)
    if cron:
        cron.sudo().nextcall = fields.Datetime.now() + timedelta(minutes=30 + secrets.randbelow(330))


LEGACY_IMPORT_MARKER = 'qing_base.legacy_mommy_base_imported'
LEGACY_PARAMETERS = (
    ('mommy.title', 'qing_base.title'),
    ('mommy.report.print_preview', 'qing_base.report.print_preview'),
    ('mommy.pdf_password_read', 'qing_base.pdf_password_read'),
    ('mommy.pdf_password_edit', 'qing_base.pdf_password_edit'),
    ('mommy.login.log.retention.days', 'qing_base.login_log_retention_days'),
)
LEGACY_COLUMNS = (
    ('res_company', 'mommy_login_ip_whitelist', 'qing_login_ip_whitelist'),
    ('res_company', 'mommy_login_ip_whitelist_enabled', 'qing_login_ip_whitelist_enabled'),
    ('res_company', 'mommy_login_ip_whitelist_exclude_portal', 'qing_login_ip_whitelist_exclude_portal'),
    ('ir_act_report_xml', 'attachment_merge_fields', 'qing_pdf_appendix_fields'),
    ('ir_act_report_xml', 'print_preview', 'qing_print_preview'),
    ('ir_act_report_xml', 'pdf_qpdf_enabled', 'qing_pdf_password_enabled'),
    ('ir_act_report_xml', 'pdf_password_read', 'qing_pdf_password_read'),
    ('ir_act_report_xml', 'pdf_password_edit', 'qing_pdf_password_edit'),
)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def import_legacy_mommy_base_settings(env):
    """Copy settings stored by mommy_base into Qing's own keys, once per database.

    While mommy_base stays installed it remains authoritative; the copy keeps the
    configuration when it is uninstalled later. The import runs only once, so it
    never overwrites choices made in Qing settings afterwards.
    """
    parameters = env['ir.config_parameter'].sudo()
    if parameters.get_bool(LEGACY_IMPORT_MARKER):
        return False
    for legacy_key, key in LEGACY_PARAMETERS:
        legacy = parameters.search([('key', '=', legacy_key)], limit=1)
        if legacy and legacy.value and parameters.get_str(key, default='') in ('', 'Odoo'):
            parameters.set_str(key, legacy.value)
    cr = env.cr
    for table, legacy_column, column in LEGACY_COLUMNS:
        if _column_exists(cr, table, legacy_column) and _column_exists(cr, table, column):
            # Identifiers come from the constant table above, never from input.
            cr.execute(f'UPDATE "{table}" SET "{column}" = "{legacy_column}" WHERE "{legacy_column}" IS NOT NULL')
    cr.execute("SELECT to_regclass('res_company_mommy_login_ip_whitelist_group_rel')")
    if cr.fetchone()[0]:
        cr.execute("""
            INSERT INTO res_company_qing_login_ip_group_rel (company_id, group_id)
            SELECT company_id, group_id FROM res_company_mommy_login_ip_whitelist_group_rel
            ON CONFLICT DO NOTHING
        """)
    parameters.set_bool(LEGACY_IMPORT_MARKER, True)
    env.invalidate_all()
    return True
