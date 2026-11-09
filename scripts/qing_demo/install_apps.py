"""Execute using `odoo shell --no-http -d qing_demo_cn_standard_test` on an isolated DB."""
from odoo.api import Environment, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.addons.qing_demo_server.services.profile_installer import validate_database
from odoo.addons.qing_demo_server.services.profile_registry import get_profile

database = env.cr.dbname
assert database.startswith("qing_demo_")
env.cr.commit()
for app, overlay in (("sale_management", "sale"), ("purchase", "purchase"),
                     ("stock", "stock"), ("crm", "crm"), ("mrp", "mrp")):
    registry = Registry.new(database, install_modules=[app])
    env.cr.rollback()
    with registry.cursor() as cr:
        current = Environment(cr, SUPERUSER_ID, {})
        validate_database(current, get_profile("cn_standard"))
        modules = current["ir.module.module"]
        installed = modules.search([("name", "like", "qing_demo_cn"), ("state", "=", "installed")])
        assert "qing_demo_cn_" + overlay in installed.mapped("name")
        print("PASS later app:", app, sorted(installed.mapped("name")), flush=True)
        cr.commit()
