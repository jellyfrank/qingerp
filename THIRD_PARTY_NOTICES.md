# Third-party notices / 第三方声明

QingERP Community Edition add-ons are licensed per module as declared in each
`__manifest__.py` (all modules in this repository: LGPL-3). The full license
texts are in `LICENSE` (GNU LGPL v3) and `COPYING` (GNU GPL v3, which the LGPL
supplements). Odoo Community itself is not included in this repository.

The components below keep their own copyright notices and licenses.

## Bundled front-end libraries

| Component | Version | License | Location | Notices |
| --- | --- | --- | --- | --- |
| Redoc | 2.5.4 | MIT | `qing_api/static/lib/redoc/` | `LICENSE`, `redoc.standalone.js.LICENSE.txt` |
| Scalar API Reference | 1.68.0 | MIT | `qing_api/static/lib/scalar/` | `LICENSE` |
| Swagger UI | 5.32.11 | Apache-2.0 | `qing_api/static/lib/swagger-ui/` | `LICENSE`, `NOTICE` |
| Apache ECharts | 5.6 | Apache-2.0 | `qing_display/static/lib/echarts/` | `LICENSE`, `NOTICE` |
| Milkdown Crepe, CodeMirror 6 and their dependencies | see module notice | MIT, BSD, ISC, MPL-2.0 OR Apache-2.0 | `qing_knowledge/static/lib/` | `qing_knowledge/THIRD_PARTY_NOTICES.md`, `markdown_editors.js.LEGAL.txt` |

## Python packages (installed separately, not bundled)

| Package | Pinned version | License (PyPI metadata) | Used by |
| --- | --- | --- | --- |
| markdown-it-py | 2.2.0 | MIT | `qing_ai`, `qing_knowledge` |
| jieba | 0.42.1 | MIT | `qing_knowledge` |
| dnspython | 2.8.0 | ISC | deployment requirements |
| aliyun-sms | 0.0.7 | AGPL-3.0 | `qing_sms_aliyun` |
| phonenumbers | — | Apache-2.0 | `qing_sms`, `qing_whatsapp` |
| requests | — | Apache-2.0 | `qing_whatsapp`, provider adapters |
| Pillow | — | MIT-CMU | `qing_ocr` |
| odoo-wechatpy | — | [MIT](https://pypi.org/project/odoo-wechatpy/) | `qing_wework` (imported as `wechatpy`) |
| websocket-client | — | [Apache-2.0](https://pypi.org/project/websocket-client/) | optional for `qing_wework` intelligent-bot WebSocket mode |

`aliyun-sms` is declared AGPL-3.0 on PyPI. Installing `qing_sms_aliyun` brings
that package into the deployment; review its terms before distributing a build
that includes it.

## Data and artwork

- `qing_address_cn` administrative-division data is derived from the Solutions
  `mommy_address_china` snapshot (2020) of the same rights holder; provenance is
  recorded in `qing_address_cn/data/source.json`.
- `qing_account/tools/chinese_amount.py` is adapted from the LGPL-3
  `mommy_qweb_chinese_num` module by lmch; the source note is kept in the file.
- Menu icons in `qing_web_ui` are original artwork. Sign-in illustrations in
  `qing_base/static/img/` were generated for QingERP.

## System dependencies installed separately

The OCR image configuration installs Tesseract OCR, the Simplified Chinese language package and Poppler tools. Tesseract and the upstream tessdata project use Apache-2.0; see the [Tesseract license](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE) and [tessdata license](https://github.com/tesseract-ocr/tessdata/blob/main/LICENSE). See the [Poppler project](https://poppler.freedesktop.org/) and the copyright notices supplied with the actual installed distribution package for Poppler's applicable terms.

PDF report password functionality may use qpdf installed by the environment; see the [qpdf license](https://github.com/qpdf/qpdf/blob/main/LICENSE.txt) and the notices supplied with the installed version. Versions and transitive dependencies depend on the actual system or distribution image. Retain all applicable bundled and package-provided copyright and license notices when redistributing.

Third-party online services have their own service terms, separate from the software licenses listed here. The presence of a connector does not mean this repository distributes the service provider's server software.
