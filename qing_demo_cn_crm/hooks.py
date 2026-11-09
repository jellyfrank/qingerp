from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready


def post_init_hook(env):
    env = demo_environment(env)
    create_once(env, "qing_demo_cn_crm.opportunity_office", "crm.lead", {
        "name": "青岛华创：新办公室 IT 设备采购", "type": "opportunity",
        "partner_id": env.ref("qing_demo_cn.partner_qingdao_huachuang").id,
        "company_id": env.company.id, "expected_revenue": 30990, "probability": 60,
        "contact_name": "张伟", "description": "十套办公座椅及设备安装服务，计划本月完成采购。",
    })
    mark_ready(env, "qing_demo_cn_crm")
