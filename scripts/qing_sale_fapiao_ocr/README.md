# 销售上传开票验证

仅在独立 PostgreSQL 16 与 Odoo 20.0 Community 测试环境执行。使用匹配版本的 Solutions、
本仓库代码以及包含 Tesseract、`chi_sim/eng` 和 Poppler 的镜像。无需生产数据库副本。

1. 新建 `qing_sale_invoice_` 前缀的空数据库，安装 `qing_sale_fapiao_ocr`，带 `--load-language=zh_CN`。
2. 使用 `-u qing_sale_fapiao_ocr,qing_account_fapiao_ocr --test-enable
   --test-tags=/qing_sale_fapiao_ocr,/qing_account_fapiao_ocr --stop-after-init --workers=0` 验证业务流程和父向导兼容。
3. 在 Odoo shell 中执行 `concurrency_probe.py`。脚本以独立事务验证原生开票与上传共用的订单锁、
   旧快照重试和剩余可开票量；仅在测试库提交合成数据。
4. 以环境变量 `QING_UPLOAD_UI_PASSWORD` 提供测试用户密码，执行 `ui_seed.py`，
   浏览器使用输出的订单上传 `qing_account_fapiao_ocr/tests/fixtures/synthetic_tax_invoice.pdf`，
   点击识别、勾选复核，再生成客户发票。
5. 以 `QING_UPLOAD_UI_VERIFY=1` 再次执行 `ui_seed.py`，核对客户发票、税票及文件内容。
6. 保存日志与截图，删除自己创建的测试数据库、容器、临时网络、端口隧道及文件。

所有数据均为合成文件或测试订单。测试不评价真实客户票据 OCR 准确率，也不执行对外开票、
自动过账、税票登记或发送邮件。生产部署与隔离验证分别记录。
