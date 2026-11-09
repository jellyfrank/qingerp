# 品牌图标验证

2026-09-15，产品分支 20.0 / 运行版本 saas~19.4 Community。

- 原图逐字节复制，1254×1254 RGBA PNG，未修改图形。
- Solutions 20.0 审计提交：90bea4622918b4b86988a3698c7c052c03c72872；检查 mommy_base 登录模板、品牌配置与已有调用，无合适的 favicon/PWA 通用工具。
- 只读检查实际租户镜像 ID：sha256:dbcfbd69fb404e1586f45db495a36cee7dc0eef350761959240e50f84ea10d67。镜像没有提供上游 commit 标签，以实际源文件指纹固定本次契约：
  - webclient_templates.xml SHA256 b741dbea5b28941a18092ea5e3d6b9bbea959a347926f121d0de8dc8c34ddf4f
  - webmanifest.py SHA256 0f68ede4299a303a1671da7c924095c51337740d22b1e744ff4ac7a17a6de298

独立 internal Docker 网络、PostgreSQL16 和空库，无生产数据复制。
全新安装成功，最终升级两个 HttpCase 测试通过，0 failed / 0 errors：

1. 登录页 favicon/Logo、PWA 主 manifest scope/start_url、192/512 图标尺寸、非法尺寸 404、离线 Logo。
2. 登录后的普通与 debug=assets bootstrap、Apple Touch 180 图标尺寸、公司 Logo 字节不变。

初次新增公司 Logo 断言比较了 BinaryValue 包装对象，失败后改为比较 to_base64() 内容，最终测试通过；未改产品逻辑来绕开测试。
Python AST、XML、原图完整性和 git diff --check 通过。测试为真实 HTTP/模板渲染，未执行浏览器截图、桌面 PWA 安装或第三方主题验收。
尚未部署 t194；本次只读核查发现其代码已使用 SaaS 独立 release 挂载，不再使用旧环境记录中的共享 Solutions 挂载。部署应通过现有租户仓库发布流程更新，不直接覆盖 release 目录。
数据库管理页无库级插件上下文，维持网关阻断；不修改原生静态资源。业务应用图标及公司/报表 Logo 保留。

## Website 已安装时升级修复（2026-09-15）

版本 saas~19.4.1.2。复现路径：独立空库安装 website，再安装旧 qing_base 1.0，升级到品牌版 1.1，稳定复现 ir_ui_view.visibility NOT NULL 失败。
Website 扩展字段默认 public，但加载 qing_base 数据时该可选模块尚未进入当前 registry；数据库列已存在且无 SQL 默认值。之前仅基础 Web 的测试漏掉此场景。

修复通过安装钩子与版本 pre/post migration，在存在无默认值的必填 visibility 列时，事务内临时提供与原生相同的 public 默认值。完成数据加载后恢复无默认值状态；失败则 PostgreSQL 事务回滚 DDL。已有 SQL 默认值不覆盖、不删除；无 Website 列时不操作。
不引入 Website 强制依赖，不修改核心，不移除 NOT NULL，不修改已有视图权限。

同一复现库升级 1.0 → 1.2 成功；两项 HttpCase 测试 0 failed / 0 errors。事后查询 visibility 仍 NOT NULL、column_default 为空，5 个品牌视图均为 public。

### mommy_base 登录页叠加（1.3）

t194 发布后发现已安装 mommy_base 替换了登录 card-body，原基于 Odoo 文本的 footer XPath 不再存在。改用两者共有的页脚容器，并明确优先级18（晚于 mommy_base 的16，早于 website 的20）。新增第三方页脚替换回归；Website 隔离库升级后三项 HTTP 测试全部通过。

### 用户确认公共基础依赖（1.4）

按用户要求 depends 改为 mommy_base，登录品牌视图直接继承 mommy_base.login_layout。
测试使用 t194 发布镜像和其实际 Solutions release 中的 mommy_base，在已有 Website 的隔离库中升级，3 项 HTTP 回归全部通过。重复使用测试数据库但测试容器 filestore 未共享，日志存在旧测试附件缺失警告；HTTP 所需 assets 已重新生成，不涉及租户附件。

## 高级设置统一入口（1.10，2026-09-17）

`qing_base` 继承并接管 mommy_base 设置应用，技术名称统一为 `qing_advanced_settings`，显示名称为“高级设置”，使用 QingERP 图标，并固定在“一般设置”之后。`qing_api` 通过继承 `qing_base.view_res_config_settings_form` 将 API 文档配置追加到同一入口，不再创建独立的 Qing API 设置应用。

在 t194 对应的 `saas~19.4` Community 镜像中完成空库安装及 `qing_base,qing_api` 联合升级测试：15 个测试方法，0 failed / 0 errors。自动断言覆盖唯一入口、名称、图标、排序及 API 配置继承；浏览器实测设置侧栏只有“一般设置”和“高级设置”，点击“高级设置”可见原基础配置及 API Documentation 配置块。未部署或升级 t194 业务数据库。

### Website 数据库升级修复（1.11）

t194 从 1.9 升级 1.10 时，新增设置视图触发 `ir_ui_view.visibility` NOT NULL 异常。原 1.2 迁移只在跨越 1.2 时执行，无法覆盖 1.9 → 1.10。1.11 复用既有事务级保护，在本版本 pre-migrate 临时设置原生 `public` 默认值，数据加载完成后由 post-migrate 恢复无默认值状态；失败仍由 PostgreSQL 事务整体回滚。

使用 t194 对应镜像，在独立 PostgreSQL 16 空库中先安装 Website 与 qing_base 1.9，确认 `visibility` 为 NOT NULL 且没有 SQL 默认值，再升级 1.9 → 1.11。升级成功，4 个 qing_base 测试方法 0 failed / 0 errors；升级后模块版本为 1.11，新设置视图为 `public`，字段仍保持 NOT NULL 且无 SQL 默认值。测试环境已清理，未操作 t194 业务数据库。
