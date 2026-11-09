def post_init_hook(env):
    repairs = env['repair.order'].search([
        ('sale_order_id', '=', False),
        ('invoice_ids.repair_order_id', '!=', False),
    ])
    # Classify existing direct invoices without changing any commercial document.
    if repairs:
        env.cr.execute(
            'UPDATE repair_order SET qing_pricing_mode = %s WHERE id = ANY(%s)',
            ('direct', repairs.ids),
        )
        repairs.invalidate_recordset(['qing_pricing_mode'])
