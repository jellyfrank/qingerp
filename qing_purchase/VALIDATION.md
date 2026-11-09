# Qing Purchase 验证记录

日期：2026-09-22。模块版本：`20.0.1.0.0`。

## 环境

- Odoo 20.0 final Community，原生源码基线 `8738f2d4d99e02630999f56008eda1de2922ce73`。
- 使用已核验的同版本运行镜像，专用 Docker 网络和 PostgreSQL 16 空库 `qing_purchase_test`；未复制线上业务数据。
- 仅安装 `qing_purchase` 及其依赖执行业务测试；浏览器阶段额外安装项目 `qing_web_ui`，启用中文合成采购账号。
- Solutions 20.0 复用审计完成：`mommy_base` 无采购开票 helper；直接沿用原生 `action_create_invoice`，没有引入预付款/尾款扩展。

## 结果

- 全新安装成功，继承视图通过 Odoo 校验。
- `/qing_purchase`：11 项测试通过，0 failure / 0 error；测试进程退出码 0。
- 覆盖无附件创建草稿、供应商/公司/币种/采购行关联和税额、按订单/按收货开票、部分收货后的剩余数量、已有草稿及已过账账单重复拦截、取消后重开、草稿RFQ/取消订单拒绝、采购用户可创建但不可过账、无采购权限及跨公司访问拒绝、组合视图按钮位置与显示条件。
- 中文真实浏览器：创建前“创建账单”位于“Upload Bill”前；点击直接打开 10 件 × 100、税额 130、合计 1,130 的原生草稿账单。返回采购订单，已开票量变为 10，“创建账单”隐藏，上传入口保留。
- 已有上述草稿账单时再次升级 `qing_purchase` 成功；升级前后账单与账单行关键业务字段、数量及金额快照一致。
- 升级后中文按钮仍为“创建账单”，应用目录记录为 `installed / 20.0.1.0.0 / official`。
- Python AST、XML 解析、PO 翻译与 `git diff --check` 静态检查通过。独立代码审查未发现实质问题。

## 复现

将当前代码的 `qing_purchase`、`qing_base` 放入测试主机的 `$TEST_ROOT/addons`，提供对应运行镜像和 Solutions 路径后执行：

```bash
TEST_ROOT=/tmp/qing-purchase-test \
ODOO_IMAGE=<verified-odoo20-image> \
SOLUTIONS_PATH=<solutions-20-checkout> \
bash scripts/qing_purchase/run_isolated_tests.sh
docker logs -f qing-purchase-test
docker inspect qing-purchase-test --format '{{.State.ExitCode}}'
```

原始日志保存在项目知识库 `testing/qing-purchase-20260922/`。测试只使用隔离合成库，未在共享租户安装模块或创建真实账单。新入口的顺序重复校验不提供并发幂等保证；原生上传及批量创建方法不在本次修改范围。
