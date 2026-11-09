# 开票接口协议 v1

本协议版本 `1.0` 独立于 Odoo 版本；当前实现与验收目标为正式 Odoo 20.0 Community。
公共模块只依赖 Odoo 原生 `mail`，不依赖其他 Qing 业务模块、OCA、payment 或 account_edi。

## 模块边界

- `qing_fapiao`：供应商账号、不可变开票任务、标准报文、异步执行、回调事件及审计。
- `qing_fapiao_<provider>`：认证、供应商格式转换、请求、查询、票文件与回调验签。
- 业务衔接模块：审批、来源单据、额度、业务登记。通过继承公共私有钩子实现。
- `qing_fapiao_mock`：合成数据适配器，只允许测试模式，不发送网络请求。

`qing_fapiao` 不导入或引用 Qing 业务模块的模型、XMLID、字段。Qing 衔接模块依赖公共模块，
并扩展任务模型；公共模块不反向查找 Qing 记录。不存在另一个需要独立部署的微服务。

## 供应商能力与扩展方法

`qing.fapiao.provider` 保存公司、销售方税号、适配器 code、测试／正式环境和启用状态。
任务建立后，账号身份被冻结；停用账号只禁止新任务，仍允许查询已有任务。

| 方法 | 约定 |
| --- | --- |
| `_get_capabilities()` | 返回 `issue_blue/query/documents/callback` 布尔值，以及 `invoice_types/currencies` 列表 |
| `_check_configuration()` | 校验本供应商必要配置；凭据只能由账号配置或受保护的密钥存储读取 |
| `_submit_invoice(order)` | 使用任务稳定 request_key 提交；禁止由适配器另生成业务请求号 |
| `_query_invoice(order)` | 按相同请求号查询；没有返回结果不等于明确失败 |
| `_fetch_documents(order)` | 补充同一已开票结果的文件，不创建另一张票 |
| `_verify_notification(headers, body)` | 对原始 body 验签，返回 event_id、request_key、result |

钩子返回统一结果，不直接写任务状态、税票台账或会计分录。
服务商不支持的功能应显式拒绝；禁止把未知状态统一映射成失败。
红字申请、作废、认证等后续能力须分别定义操作和原票引用；v1 不提供这些税务动作。

## 输入

所有金额、数量、税率均为非负十进制字符串，不接受浮点数、NaN、科学计数法。
金额按币种精度校验；税率使用 `0.13` 表示 13%，而非 `13`。

```json
{
  "schema_version": "1.0",
  "request_key": "stable-unique-request-id",
  "invoice_type": "normal",
  "color": "blue",
  "currency": "CNY",
  "buyer": {"name": "合成购买方", "tax_id": "SYNTHETIC-BUYER"},
  "seller": {"name": "合成销售方", "tax_id": "SYNTHETIC-SELLER"},
  "lines": [{
    "line_key": "line-1",
    "description": "合成测试商品",
    "quantity": "1",
    "amount_untaxed": "100.00",
    "amount_tax": "13.00",
    "tax_rate": "0.13",
    "tax_code": "SYNTHETIC-CODE",
    "price_include": false
  }],
  "amount_untaxed": "100.00",
  "amount_tax": "13.00",
  "amount_total": "113.00"
}
```

同请求号、相同快照返回已有任务；同请求号而内容不同必须拒绝。
来源单据、额度归属和审批在业务模块校验，不泄露到供应商适配器。
税收分类编码目前为人工确认值，适配器接入时必须增加该服务商的目录、行级字段与舍入校验。
不能通过修改审批金额来消除服务商差异；应返回待处理差异并保留原请求。

## 输出与文件

统一字段：`status`、`external_id`、`error_code`、`message`、`invoices`。
`status` 为 `pending/unknown/issued/rejected`。

已开票结果包含税票列表，每票包含 `number/code/date/currency/buyer/seller/amount_untaxed/amount_tax/files`。
`files` 为 `{name, mimetype, data}` 列表，data 为 base64。
购方名称及税号、销方税号、金额及税额均须与审批快照一致；多票的金额、税额分别求和。
销方名称保留服务商返回的实际登记名称（不能为空），允许与本地公司显示名称不同；不得用申请名称覆盖票面名称。
`external_id` 在受理后保持服务商流水号，不得在后续查询中改成客户端请求号。
结果先落地后登记，文件暂未就绪时可以补取同一张票的文件。
一单多票允许由协议表达，但 Qing 首版只登记一票，多票保留结果并提示暂不支持，不丢弃其中任何一张。

## 执行、回调与恢复

1. 用户动作仅持久化任务或查询指令，不在界面事务中调用开票接口。
2. 执行器先以独立事务持久化“可能已发送”记录，再调用服务商。
3. 超时、连接中断、格式错误或进程退出后，保留未知状态，后续只查询同一请求。
4. 已受理、结果未知或已开票的任务禁止直接撤销或换供应商；未发送任务可取消。
5. 供应商回调由其专属路由验签后接入私有 `_process_notification`；公共模块不开放通用匿名路由。
6. 验签后的事件持久保存，重复事件相同内容幂等；同事件号不同内容拒绝。乱序消息不得覆盖已经确认的出票结果。
7. 远端 `issued` 与业务 `registration_state` 独立。登记失败只重试本地业务钩子，不重新开票。

数据库事务回滚无法撤销远端已开票事实，因此不能靠普通 ORM 事务重试保证外部开票幂等。
真实供应商必须能按客户请求号查询；尚未确认幂等和查询契约的供应商不得启用自动开票。

## 接入验收

供应商适配器至少验证：成功、明确拒绝、超时但实际已受理、未知请求、票文件晚到、重复及乱序回调、
验签错误、跨公司事件、金额/税额/主体不一致、重复提交、进程在提交后退出、本地登记失败恢复。
保存脱敏的请求号、状态、错误及事件；凭据、Authorization Header 不写日志或协议快照。

模拟供应商仅验证公共契约和恢复逻辑，不代表任何真实开票服务商联调通过。
