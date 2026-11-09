# OCR 验证复现

使用与目标部署一致的 Odoo 20 镜像、独立 PostgreSQL 16 数据库和私有测试网络。
需要 Tesseract、chi_sim/eng、Poppler，以及真实图像测试使用的 DejaVu Sans 字体。
依赖 Solutions 的匹配 20.0 分支。不得将以下操作指向共享租户数据库。

1. 仅安装 `qing_ocr`，运行 `--test-enable --test-tags=/qing_ocr --stop-after-init --workers=0`。
   核对 `account`、`qing_account_fapiao` 和 `qing_account_fapiao_ocr` 未安装。
2. 在新空库中安装 `qing_account_fapiao_ocr`，带 `--load-language=zh_CN`（当前父模块菜单数据要求启用该语言）。
3. 升级用数据库名称必须以 `qing_ocr_` 开头。先用拆分前 20.0.1.3 代码安装税票识别模块。
   在 Odoo shell 中设置 `QING_OCR_PROBE_MODE=seed` 并输入 `upgrade_probe.py`，写入合成历史向导与税票草稿。
4. 将 addons 路径切到新代码，只指定 `-u qing_account_fapiao_ocr --stop-after-init --workers=0`。
   核对新依赖 `qing_ocr` 自动安装，执行两个模块测试标签。
5. 使用 `QING_OCR_PROBE_MODE=verify` 运行同一脚本，核对记录、原文件哈希、菜单/权限 XML ID，
   检查已有向导重复生成仍返回原草稿、中文错误提示有效。
6. 保存测试日志后，清理自己创建的容器、测试库、网络和临时文件。

所有 PDF/图片均为合成文件；真实引擎测试只证明识别路径与数据流，不代表真实客户票据准确率基准。
