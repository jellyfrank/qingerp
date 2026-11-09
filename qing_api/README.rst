Qing API
========

QingERP 的稳定业务 API 基础模块。模块使用 Odoo 原生 HTTP Controller、API Key、ACL 和 Record Rule，提供：

* 声明式 ``@api_route`` 与 Endpoint 注册表；
* 严格的请求/响应 Schema 和 OpenAPI 3.0.3 生成；
* Bearer API Key、统一 JSON 错误、Request ID；
* 可配置为公开访问或仅登录用户可见的 ``/api/docs`` 和 ``/api/openapi.json``；
* 可配置 Scalar、Swagger UI 或 ReDoc，默认 Scalar；
* 所有文档界面使用本地固定版本资源，不依赖 CDN；
* Odoo 20 原生 Bearer scope 与独立 QingERP API Key。
* 可针对公开业务凭证路由关闭 Session 保存，并为大请求声明 body 大小上限。

最小示例::

   from odoo import http
   from odoo.addons.qing_api.api import ApiSchema, String, api_route


   class PingResponse(ApiSchema):
       status = String(required=True)


   class PingController(http.Controller):
       @api_route(
           "/api/v1/example/ping",
           methods=["GET"],
           operation_id="pingExample",
           tags=["Example"],
           summary="Ping example",
           response_model=PingResponse,
           readonly=True,
       )
       def ping(self):
           return {"status": "ok"}

使用非 Odoo 客户凭证的公开业务路由时，可以通过 ``auth="public"`` 自行验证业务凭证，
并用 ``save_session=False`` 避免创建 Session。对于有明确上限的 JSON 请求，可设置
``max_body_bytes``；超限请求统一返回 413。业务服务仍须负责身份、租户范围和业务规则校验。
若业务凭证使用 Bearer 格式，可再设置 ``security_scheme`` 和 ``security_description``，让 OpenAPI
将它表达为独立的 HTTP Bearer 安全方案；不要把 ``Authorization`` 当普通 header 参数声明。

Odoo 20 为 API Key 使用独立的 ``qing_api`` scope。API Controller 始终要求请求显式携带 ``Authorization: Bearer ...``，不会把浏览器登录 Session 当作 API 凭证。

默认仅登录用户可查看 API 文档。可以在“设置 / 高级设置 / API Documentation”中切换为公开访问，并选择 ``/api/docs`` 使用的文档界面。

启用 Odoo 门户设置中的“Customers can generate API Keys”后，门户用户新建 API Key
默认选择一年（365 天），仍可手动选择一天或一个月。此变更只影响新建 Key，
不会延长或重新签发已有 Key。
