def post_init_hook(env):
    """Give existing master data stable API identifiers without triggering business writes."""
    for table in ("product_template", "product_category", "uom_uom"):
        env.cr.execute(
            f"UPDATE {table} "
            "SET qing_api_uid = replace(gen_random_uuid()::text, '-', '') "
            "WHERE qing_api_uid IS NULL OR qing_api_uid = ''"
        )
