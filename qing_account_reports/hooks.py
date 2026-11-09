def post_init_hook(env):
    env['qing.financial.report.scheme']._bootstrap_existing_companies()
