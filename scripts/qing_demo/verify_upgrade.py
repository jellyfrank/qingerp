"""Check a real module upgrade preserves seeded record IDs and user edits."""
from odoo.api import Environment, SUPERUSER_ID
from odoo.modules.registry import Registry

database = env.cr.dbname
assert database.startswith("qing_demo_")
partner = env.ref("qing_demo_cn.partner_qingdao_huachuang")
original_name = partner.name
partner.name = "用户修改的中文客户名称"
model_data = env["ir.model.data"]
domain = [("module", "like", "qing_demo_")]
before = {record.complete_name: (record.model, record.res_id) for record in model_data.search(domain)}
modules = env["ir.module.module"].search([("name", "like", "qing_demo_"), ("state", "=", "installed")]).mapped("name")
env.cr.commit()
env.cr.commit()
registry = Registry.new(database, upgrade_modules=modules)
env.cr.rollback()
with registry.cursor() as cr:
    current = Environment(cr, SUPERUSER_ID, {})
    after = {record.complete_name: (record.model, record.res_id) for record in current["ir.model.data"].search(domain)}
    assert before == after, (before.keys() - after.keys(), after.keys() - before.keys())
    partner = current.ref("qing_demo_cn.partner_qingdao_huachuang")
    assert partner.name == "用户修改的中文客户名称"
    partner.name = original_name
    cr.commit()
print("PASS upgrade: stable external IDs, no duplicate datasets, user changes preserved")
