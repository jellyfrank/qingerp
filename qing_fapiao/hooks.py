"""Move invoice provider data from the earlier Solutions mommy_fapiao addon.

QingERP no longer depends on mommy_fapiao. When qing_fapiao is installed on a
database that still has it, providers, tasks and audit events are copied with
their original IDs, so links held by business modules stay valid. The legacy
addon then stays installed but inert: its scheduler and providers are disabled
so an invoice can never be submitted twice.
"""

LEGACY_TABLES = (
    ('mommy_fapiao_provider', 'qing_fapiao_provider'),
    ('mommy_fapiao_order', 'qing_fapiao_order'),
    ('mommy_fapiao_event', 'qing_fapiao_event'),
)
LEGACY_MODELS = (
    ('mommy.fapiao.provider', 'qing.fapiao.provider'),
    ('mommy.fapiao.order', 'qing.fapiao.order'),
    ('mommy.fapiao.event', 'qing.fapiao.event'),
)


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", (table,))
    return bool(cr.fetchone()[0])


def _columns(cr, table):
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = %s
    """, (table,))
    return {row[0] for row in cr.fetchall()}


def import_legacy_rows(env):
    """Copy mommy_fapiao rows into Qing tables; return False when there is nothing to import."""
    cr = env.cr
    if not _table_exists(cr, 'mommy_fapiao_provider'):
        return False
    # Identifiers below come from the constant tables, never from input.
    for legacy, table in LEGACY_TABLES:
        common = sorted(_columns(cr, legacy) & _columns(cr, table))
        columns = ', '.join(f'"{column}"' for column in common)
        cr.execute(f'INSERT INTO "{table}" ({columns}) SELECT {columns} FROM "{legacy}" ON CONFLICT (id) DO NOTHING')
        cr.execute(f"""
            SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                          GREATEST((SELECT COALESCE(MAX(id), 0) FROM "{table}"), 1))
        """)
    for legacy_model, model in LEGACY_MODELS:
        cr.execute("UPDATE mail_message SET model = %s WHERE model = %s", (model, legacy_model))
        cr.execute("UPDATE ir_attachment SET res_model = %s WHERE res_model = %s", (model, legacy_model))
        cr.execute("UPDATE mail_followers SET res_model = %s WHERE res_model = %s", (model, legacy_model))
        cr.execute("""
            UPDATE mail_activity SET res_model = %s,
                   res_model_id = (SELECT id FROM ir_model WHERE model = %s)
             WHERE res_model = %s
        """, (model, model, legacy_model))
    legacy_group = env.ref('mommy_fapiao.group_fapiao_manager', raise_if_not_found=False)
    if legacy_group:
        cr.execute("""
            INSERT INTO res_groups_users_rel (gid, uid)
            SELECT %s, uid FROM res_groups_users_rel WHERE gid = %s
            ON CONFLICT DO NOTHING
        """, (env.ref('qing_fapiao.group_fapiao_manager').id, legacy_group.id))
    # Leave the legacy addon installed but unable to submit, query or be used by mistake.
    cr.execute("UPDATE mommy_fapiao_provider SET enabled = false")
    cr.execute("UPDATE mommy_fapiao_order SET pending_action = NULL, next_attempt_at = NULL")
    for xmlid in ('mommy_fapiao.ir_cron_fapiao_orders', 'mommy_fapiao.menu_fapiao_interface_root'):
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.sudo().active = False
    env.invalidate_all()
    return True


def import_legacy_columns(env, table, columns):
    """Copy columns that extension modules add, for rows imported by ``import_legacy_rows``.

    :param table: a Qing table from ``LEGACY_TABLES``
    :param columns: column names defined by the calling extension module
    """
    legacy = dict((qing, mommy) for mommy, qing in LEGACY_TABLES)[table]
    cr = env.cr
    if not _table_exists(cr, legacy):
        return False
    available = _columns(cr, legacy) & _columns(cr, table)
    for column in columns:
        if column not in available:
            continue
        cr.execute(f"""
            UPDATE "{table}" target SET "{column}" = legacy."{column}"
              FROM "{legacy}" legacy
             WHERE legacy.id = target.id AND target."{column}" IS NULL
        """)
    env.invalidate_all()
    return True


def post_init_hook(env):
    import_legacy_rows(env)
