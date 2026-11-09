# 在线开票统一认证与升级指南

## 使用方式

客户端继续在“在线开票设置”中申请接入、同步开通状态，service 管理员继续审核每个销售方。
接入申请管理入口迁到 **QingERP 在线服务 → 接入申请管理**，申请表和列表显示所属注册实例。
“在线服务实例审核”和“销售方开票审核”是两项授权；有效的开票服务订阅仍是创建新任务的条件。

客户端所有在线开票请求共用 `qing.service.instance` 的 HTTPS 地址、实例 UUID 和实例密钥，
由 `qing_service_client._request` 发送。服务端使用 Qing API 的 `api_route`、Schema、OpenAPI、
错误响应和请求编号；认证复用在线服务中心的实例校验。新接入不会生成独立的开票认证密钥。
实例身份用来认证，销售方接入编号用来选择业务记录，两者必须在服务端匹配。

## 统一 API

所有请求必须使用 HTTPS，携带：

```http
Authorization: Bearer <instance_token>
X-Qing-Instance-Uid: <instance_uid>
Content-Type: application/json
```

除提交接入申请外，开票接口还需 `X-Qing-Client-Uid: <client_id>`。
这些 UUID 是路由身份标识，不能替代实例密钥。OpenAPI 安全方案为 `qingInstanceBearerAuth`，
不是 Odoo 用户 API Key；无需为每个销售方创建 Odoo 用户。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/service/fapiao/applications` | 申请或更新销售方接入 |
| GET | `/api/v1/service/fapiao/status` | 查询开通及允许修改资料状态 |
| POST | `/api/v1/service/fapiao/orders` | 提交开票请求快照 |
| GET | `/api/v1/service/fapiao/order?request_key=...` | 查询原任务 |
| POST | `/api/v1/service/credentials/rotate` | 暂存共享实例新密钥 |
| POST | `/api/v1/service/credentials/confirm` | 用新密钥确认轮换 |

申请 JSON 示例：

```json
{
  "connection_uid": "eb958902-f2d3-44da-813c-51f17f3a28fa",
  "company_name": "示例企业",
  "seller_name": "示例企业",
  "seller_tax_id": "示例销方税号",
  "client_version": "20.0"
}
```

`connection_uid` 由客户端生成并持久保存，在同一实例内幂等。重复提交返回同一 `client_id`。
后续申请更新可携带 `client_id`；不得携带其他实例的编号。
响应保留 `client_id`、`state`、`registration_editable`。服务端重置资料后，客户端同步状态，
修改销售方资料并重新申请，消费本次修改许可，等待重新审核。已有任务及请求幂等编号不变。

开票提交和查询的请求/响应继续使用 Mommy 发票协议 v1。新的业务请求仍检查实例状态、订阅
有效期和额度、销售方审核、服务商环境及请求快照。暂停或订阅失效时不得创建新任务；凭证
仍有效的所属实例可以查询历史任务。鉴权失败返回 Qing API 标准 `error` 对象；业务拒绝
仍通过 `result.status=rejected` 和失败请求审计返回。网络结果不明时查询原任务。

## 已有数据兼容

旧 `/api/v1/fapiao/*` 在 OpenAPI 标为 deprecated；`/qingerp/fapiao/v1/*` 兼容入口仍保留，
旧客户端可继续按原凭证工作。新客户端不自动回退到旧认证。

已绑定在线服务实例的开票申请，使用实例密钥及原 `client_id` 即可继续访问；首次更新申请会
记录新的 `connection_uid`。尚未绑定实例的历史申请，必须在更新申请时额外提供原
`legacy_token` 证明归属，才能绑定到当前已审核实例。跨实例迁移被拒绝，原任务绑定不重建。
客户端保留历史密钥供首次迁移使用，确认统一接口成功后不再随申请发送；新申请不创建该密钥。

“轮换接入密钥”和“确认新密钥已保存”按钮保留，底层现在轮换共享实例密钥，影响该实例的
全部在线服务。轮换采用两阶段交接：服务端暂存新摘要，客户端保存新密钥后用新密钥确认；
确认之前旧、新密钥均有效，确认之后旧密钥失效。service 不保存明文密钥。

## 升级顺序

1. 先发布 service 仓库，升级 `qingerp_service_center,qingerp_fapiao_service`，完成重启和健康检查。
2. 再发布客户端仓库，升级 `qing_service_client,qing_account_fapiao_service`，完成重启和健康检查。
3. 打开“在线开票设置”同步状态；未绑定实例的旧记录用“申请接入／更新申请”完成归属迁移。
4. 在 service 在线服务菜单核对实例、订阅、销售方申请和原任务历史。

版本对应：service center `20.0.1.0.2`，service invoice `20.0.1.0.12`，
client instance `20.0.1.0.1`，client invoice `20.0.1.0.8`。
不得先升级客户端并期望未更新的 service 接受新路径；本改动不自动开通服务或发起真实税票。

## 本次验证（2026-09-27）

在 Odoo 20 Community 独立合成数据库完成安装和升级验证，未使用生产数据：

- 初次安装：28 项测试通过，0 失败、0 错误。
- 扩充真实 HTTP 路由测试后，四模块升级回归：26 项测试通过，0 失败、0 错误。
- 最后调整共享服务地址默认值后，客户端模块回归：4 项测试通过。
- 覆盖实例认证、跨实例隔离、重复申请、旧凭证归属迁移、重置后更新、订阅额度、
  新任务提交、重复请求、暂停后拒绝新任务及查询历史、共享密钥轮换和菜单归属。
- HTTP 测试仅模拟 HTTPS 终止标记；请求经过实际 Qing API 路由、Schema 和数据库。
  开票使用 mock 适配器，未调用诺诺开具真实税票。
- Python/XML 语法与 Git 空白检查通过。尚未进行生产部署和登录浏览器验收。
