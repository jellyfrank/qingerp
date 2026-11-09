"""Run in isolated Odoo shell after HTTP smoke tests for all three profiles."""
import os
import requests
from odoo.api import Environment, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.modules import db
from odoo.sql_db import db_connect
from odoo.addons.qing_demo_cn.hooks import pre_init_hook
from odoo.exceptions import UserError

assert env.cr.dbname.startswith("qing_demo_")
for name, demo, country, currency in (
    ("qing_demo_empty_test", False, "CN", "CNY"),
    ("qing_demo_odoo_us_test", True, "US", "USD"),
):
    with Registry(name).cursor() as cr:
        current = Environment(cr, SUPERUSER_ID, {})
        modules = current["ir.module.module"]
        assert modules.search([("name", "=", "base")]).demo == demo
        assert not modules.search([("name", "like", "qing_demo_cn"), ("state", "=", "installed")])
        company = current.ref("base.main_company")
        assert company.country_id.code == country and company.currency_id.name == currency
        try:
            pre_init_hook(current)
        except UserError:
            pass
        else:
            raise AssertionError("CN dataset incorrectly accepted an incompatible database")

url = "http://127.0.0.1:8069/web/database/create"
defaults = dict(master_pwd="qing-demo-test-only", lang="en_US", country_code="CN", phone="",
                login="admin@example.com", password=os.environ["QING_TEST_PASSWORD"], demo_profile="cn_standard")
for name, changes in (
    ("qing_demo_wrong_password", {"master_pwd": "incorrect-password"}),
    ("qing_demo_unknown_profile", {"demo_profile": "../../../arbitrary_module"}),
    ("qing_demo_cn_standard_test", {}),
):
    response = requests.post(url, data=dict(defaults, name=name, **changes), timeout=30)
    assert response.status_code == 422
    with db_connect("postgres").cursor() as cr:
        cr.execute("SELECT 1 FROM pg_database WHERE datname=%s", [name])
        assert bool(cr.fetchone()) == (name == "qing_demo_cn_standard_test")
print("PASS empty/US isolation, manual-install guard, wrong password, invalid profile, existing DB preservation")
env.cr.rollback()
