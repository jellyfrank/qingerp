# 验证记录

2026-09-23，在独立 PostgreSQL 16 数据库和独立 Odoo 20 Community 容器中安装 `qing_product`，随后执行单模块升级回归。运行镜像为 `ohm/saas-deploy:tenant-qingerp-200-764`；测试容器仅挂载本次代码和对应 20.0 `mommy_base`，未连接业务租户数据库。

- 新库安装成功；升级回归 5 项测试，0 failed、0 errors。
- 实际 HTTP 验证四类资源的 12 个增删改接口和 `/api/openapi.json` 条目。
- 覆盖 Bearer 用户的原生权限拒绝、关联资源验证、非空分类删除冲突、层级单位换算。
- PostgreSQL 已建立 `product_template`、`product_category`、`uom_uom` 的公开 ID 唯一约束；既有分类和单位的 ID 全部回填且无重复。
- Python 3.12 编译和 `git diff --check` 通过。

本次隔离测试没有安装模块到 `qingerp` 业务租户；部署后需在该租户打开 `/api/docs` 并确认接口可见。
