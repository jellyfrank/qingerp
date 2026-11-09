from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['qing.financial.report.scheme']._bootstrap_existing_companies()
