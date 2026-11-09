# Qing API 验证记录

验证日期：2026-09-17。

## 实际运行时

- Odoo `saas~19.4` Community 派生运行镜像；
- Python 3.12；
- 独立 PostgreSQL 16 容器与隔离数据库 `qing_trade_20`；
- 测试代码、网络、数据库和容器均与 t194 租户隔离。

## 结果

- Python 编译、XML 解析和 Git whitespace 检查通过；
- `qing_api` 定向安装／升级测试共 13 项：0 failed、0 errors；
- 覆盖 Bearer Key、scope 拒绝、Request ID、Schema 严格校验、OpenAPI 生成和 ETag 304；
- 覆盖 `/api/docs` 与 `/api/openapi.json` 默认仅登录用户可见，以及配置为公开后匿名访问成功；
- 覆盖 Scalar 默认界面及 Swagger UI、ReDoc 切换，三种界面均只引用模块本地静态资产；
- 覆盖配置项继承 `qing_base` 的“高级设置”入口，不再创建独立设置侧栏项；
- `zh_CN` 实际请求返回中文拒绝消息，配置选择项实际解析为“登录后可见／允许公开访问”；
- 贸易模块源码语言回归中，`qing_account` 最终 71 项测试通过；其余财务、中国本地化、税票、OCR、暂估和 Web UI 目标模块在同一隔离库的综合回归中均无业务测试失败；
- `qing_account` 从 `saas~19.4.1.12` 升级到 `saas~19.4.1.13` 的迁移脚本实际执行成功，CSV 分隔符技术值迁移为 `comma`、`semicolon`、`tab`；
- 使用 `fields_get(lang='zh_CN')` 验证银行导入、纳税人类型、期初比较、税票、暂估及 API 文档权限的英文技术值／英文源码标签均正确显示中文翻译。

## 边界

本次没有部署或升级 t194，也没有读写其业务数据库。验证只使用临时隔离数据库。

## 2026-09-19：门户 API Key 默认一年

- 对照正式 Odoo 20 源码确认：门户组原生 `api_key_duration` 未设值时，API Key 向导只提供一天；门户弹窗即使提供多种期限，原生 JavaScript 仍优先选择 30 天。
- `qing_api` 将门户组允许期限设为 365 天，并让门户弹窗与后端向导默认选择原生的一年选项；一天和一个月选项保留，内部用户的后端默认值不变。
- Python 编译、XML 解析、模板 XPath 目标、期限选择表达式和 `git diff --check` 均通过。
- 本次未运行 Odoo 20 数据库测试或浏览器端到端测试，也未升级 qingerp 租户；新增的 Odoo 回归测试仍需在隔离环境执行。
