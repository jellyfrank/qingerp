# QingERP Community Edition

[中文](#qingerp-社区免费版) | [English](#qing-erp-community-edition)

QingERP Community Edition is a modular business application suite built on **Odoo 20.0 Community**. This repository contains QingERP add-ons; it does not include or replace the Odoo server or Odoo's standard applications. Install only the modules your organization needs.

## QingERP 社区免费版

QingERP 社区免费版基于 **Odoo 20.0 Community**，以可选模块扩展日常经营流程。本仓库提供 QingERP 扩展模块，不包含也不替代 Odoo 服务端和官方标准应用。请按需安装模块，并核对各模块的依赖和许可证。

### 功能概览

- **客户关系与销售**：客户和供应商角色管理、CRM 商机与跟进、销售报价和订单流程、销售订单分批采购等。
- **采购与库存**：供应商账单、采购退货和库存估值衔接、仓库收发货及调拨扫码、批次和序列号、盘点、设备租赁和归还。
- **财务与税票**：客商对账与账龄、财务报表、会计配置检查、税票登记、税票本地 OCR 辅助录入和人工核对。
- **协作与效率**：后台通用体验增强、列表和明细序号、文件在线预览、工作簿、统一样式的原生仪表板、知识文章和经营展示屏。
- **可选连接器**：青云 AI 及可选模型服务商、短信和阿里云短信、企业微信、WhatsApp、邮件及 QingERP 在线开票服务连接。
- **网站商城**：可按网站启用登录后查看商品价格，访客浏览商品时显示登录与注册入口。
- **中文演示数据**：可选择安装中文、人民币的销售、采购、库存、CRM、制造和财务演示数据。

以上能力由不同的 `qing_*` 模块提供；具体功能取决于已安装的 Odoo 标准应用、模块依赖、外部服务配置、服务订阅和对应模块许可证。OCR 结果需要人工核对；在线开票、短信、AI、企业微信和 WhatsApp 等连接器需要各自的服务账号、凭据或服务方开通。

### 安装方式

**环境要求：** Odoo 20.0 Community，及所选模块 `__manifest__.py` 中列出的 Odoo 模块、QingERP 模块和 Python 依赖。本仓库模块不依赖 Solutions 共享模块。企业微信模块需要安装 `odoo-wechatpy`；智能机器人 WebSocket 模式还需要 `websocket-client`。OCR 识别还需要系统安装 Tesseract；报表 PDF 加密需要系统安装 qpdf。请只在使用相应可选模块时安装它们所需的外部依赖。

1. 将本仓库克隆到 Odoo 的自定义插件目录：

   ```bash
   git clone --branch 20.0 https://github.com/jellyfrank/qingerp.git /opt/odoo/custom-addons/qingerp
   ```

2. 在 Odoo 配置文件的 `addons_path` 中加入 Odoo 官方插件路径和本仓库路径。例如：

   ```ini
   addons_path = /opt/odoo/odoo/addons,/opt/odoo/custom-addons/qingerp
   ```

   从旧版本升级且数据库中仍装有 `mommy_base`、`mommy_mail` 或 `mommy_fapiao` 时，升级期间保留原 Solutions 路径。升级会把相关设置和开票数据复制到 QingERP 模块，并让旧模块的同类功能保持主导或停用，避免重复执行。

3. 确认所有依赖均为兼容的 20.0 版本后重启 Odoo。在 **应用** 中更新应用列表，再搜索并安装需要的 `QingERP` 模块。创建新数据库时也要先配置好插件路径。

各模块的完整依赖以模块清单为准。生产环境安装前，请先在测试数据库验证选定模块和依赖的安装及升级。

### 许可证与技术支持

本仓库每个模块的许可证以其 `__manifest__.py` 为准，目前 60 个模块全部使用 LGPL-3，许可证全文见 `LICENSE` 与 `COPYING`。第三方组件保留各自的许可证，详见 [第三方组件声明](THIRD_PARTY_NOTICES.md)。用户使用条款见 [中文版](USER_TERMS.md) 和 [英文版](USER_TERMS.en.md)。

- 网站 / Website: [www.qingsolution.com](https://www.qingsolution.com/)
- 技术支持 / Technical support: [kevin@odoomommy.com](mailto:kevin@odoomommy.com)

关注微信公众号 **OdooHub**，请在微信中搜索“OdooHub”或扫描下方二维码：

[![微信公众号 OdooHub 二维码](qing_whatsapp/static/description/images/odoohub-wechat-qr.png)](qing_whatsapp/static/description/images/odoohub-wechat-qr.png)

## Qing ERP Community Edition

### Features

- **CRM and sales:** customer and vendor roles, CRM opportunities and follow-ups, quotations and sales orders, and sales-driven purchasing.
- **Purchasing and inventory:** vendor bills, purchase-return and valuation integrations, barcode-based receipts, deliveries and transfers, lots and serial numbers, stock counts, and equipment rental and returns.
- **Accounting and tax invoices:** partner statements and aging, financial reports, accounting setup checks, a tax-invoice register, and locally assisted OCR entry with manual review.
- **Productivity:** shared backend enhancements, row numbers for lists and line items, online file previews, workbooks, knowledge articles, and business display screens.
- **Optional connectors:** Qing AI with optional model providers, SMS and Aliyun SMS, WeCom, WhatsApp, email, and the QingERP online invoicing connection.
- **Online shop:** optionally require login to view product prices per website, with login and signup links for visitors.
- **Chinese demo data:** optional Chinese and CNY demo data for sales, purchasing, inventory, CRM, manufacturing, and accounting.

These capabilities are delivered by separate `qing_*` add-ons. Availability depends on the Odoo standard apps, module dependencies, external service configuration, service subscriptions, and the license of each add-on. OCR results require human review. Online invoicing, SMS, AI, WeCom, and WhatsApp connectors need their own service accounts, credentials, or provider approval.

### Installation

**Requirements:** Odoo 20.0 Community and the Odoo modules, QingERP add-ons and Python dependencies listed in each selected module's `__manifest__.py`. The add-ons in this repository do not depend on shared Solutions add-ons. The WeCom module requires `odoo-wechatpy`; intelligent-bot WebSocket mode also requires `websocket-client`. OCR recognition requires the Tesseract system executable, and PDF report passwords require qpdf. Install optional external dependencies only when using the add-ons that require them.

1. Clone this repository into an Odoo custom-addons directory:

   ```bash
   git clone --branch 20.0 https://github.com/jellyfrank/qingerp.git /opt/odoo/custom-addons/qingerp
   ```

2. Add the Odoo add-ons path and this repository to Odoo's `addons_path`. For example:

   ```ini
   addons_path = /opt/odoo/odoo/addons,/opt/odoo/custom-addons/qingerp
   ```

   When upgrading a database that still has `mommy_base`, `mommy_mail` or `mommy_fapiao` installed, keep the Solutions path during the upgrade. The upgrade copies the related settings and invoicing data into QingERP add-ons and keeps the legacy features either authoritative or disabled so nothing runs twice.

3. Make sure every dependency is on a compatible 20.0 version, then restart Odoo. Update the Apps list and install the QingERP modules you need. Configure the add-on paths before creating a new database as well.

Check each module manifest for its full dependency list. Validate installation and upgrade of the selected modules in a test database before production use.

### Licensing and support

The license for each add-on is specified in its `__manifest__.py`. All 60 current modules are licensed under LGPL-3; the full texts are in `LICENSE` and `COPYING`. Third-party components retain their own licenses; see the [third-party notices](THIRD_PARTY_NOTICES.md). Read the user terms in [Chinese](USER_TERMS.md) or [English](USER_TERMS.en.md).

- Website: [www.qingsolution.com](https://www.qingsolution.com/)
- Technical support: [kevin@odoomommy.com](mailto:kevin@odoomommy.com)

Follow our official WeChat account **OdooHub** by searching for “OdooHub” in WeChat. The QR code in the Chinese section above can also be scanned.

### 原生仪表板展示

`qing_dashboard` 为已安装 `qing_web_ui` 和 `spreadsheet_dashboard` 的环境自动提供统一卡片、图表和明细表样式；升级已有环境时可安装该模块启用。仅调整只读展示，不改变统计或权限。公司切换菜单的宽度、名称换行及选中态由 `qing_web_ui` 提供。
