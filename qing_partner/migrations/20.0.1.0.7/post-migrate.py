DESCRIPTION = (
    '客户与供应商角色管理\n'
    '\n'
    '按公司、负责人和团队控制联系人访问\n'
    '\n'
    '销售与采购业务准入检查\n'
    '\n'
    '联系人 API：查询、新增、修改和删除'
)
SUMMARY = '客户与供应商角色、联系人权限与 API 管理'
SHORTDESC = 'Qing联系人'


def migrate(cr, version):
    cr.execute(
        """UPDATE res_partner
              SET qing_api_uid = replace(gen_random_uuid()::text, '-', '')
            WHERE qing_api_uid IS NULL OR qing_api_uid = ''"""
    )
    cr.execute(
        """UPDATE ir_module_module
              SET shortdesc = jsonb_set(
                      COALESCE(shortdesc, '{}'::jsonb), '{zh_CN}', to_jsonb(%s::text), true
                  ),
                  summary = jsonb_set(
                      COALESCE(summary, '{}'::jsonb), '{zh_CN}', to_jsonb(%s::text), true
                  ),
                  description = jsonb_set(
                      COALESCE(description, '{}'::jsonb), '{zh_CN}', to_jsonb(%s::text), true
                  )
            WHERE id = (SELECT res_id FROM ir_model_data
                         WHERE module = 'base' AND name = 'module_qing_partner')""",
        (SHORTDESC, SUMMARY, DESCRIPTION),
    )
