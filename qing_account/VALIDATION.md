# 验证记录

2026-09-12；20.0 产品分支，实际测试版本 saas~19.4 Community。

## 原生基线和复用审计

- 测试镜像：`ohm/odoo-runtime:19.4-community`，镜像 ID
  `sha256:ca418a9e39f12b4f9bab8d9247b28b2fb8d0be9f2eeb6b3655bdc6a07bbe7146`。
- 对截图 t194 容器进行了只读源码检查：原生动作
  `account.accountant_reset_to_draft_entries_action` 存在，原绑定组为
  `account.group_account_user`；表单 button_draft 绑定开票组。
- 部署源文件 SHA-256：account_move.py
  `21c5a3755b5318c62725a0b4605d63771d6213c8c1b0b78c788c93adca04a722`；
  account_move_views.xml
  `61539fb803423889b4f103df61ec6ac142e0724a68f7ffa32a40a94c8083eebc`。
- 已检查 Solutions 20.0 mommy_base 的 models、tools、controllers 和其他
  mommy_* 的 account.move / button_draft / 批量重置调用；没有适用的公共工具。
  因此直接复用原生动作，不增加自定义会计模型方法。

## 已执行

独立 Docker 网络、PostgreSQL 16 和新建空数据库；无生产数据复制，
未连接租户数据库。新模块安装及随后 qing_account 升级成功。

6 个业务测试通过，0 failed / 0 errors，进程退出码 0：

1. 仅开票组用户能看到唯一原生动作，可将两张待审核已过账发票批量重置并刷新。
2. 无财务权限的内部用户不能执行动作。
3. 哈希锁定凭证仍无法重置。
4. 混选草稿及已过账单据，保持原生部分成功行为。
5. 已审核发票保持原生财务角色校验。
6. 卸载钩子移除新增开票组，保留原生 Accountant 组。

初次测试暴露测试数据过账后进入已审核状态，按原生权限规则不允许普通
开票用户重置。成功路径明确使用待审核单据，并另加已审核权限拒绝测试。
没有为通过测试而提升执行权限或修改原生审核逻辑。

Python AST、manifest 数据文件、XML 解析和 git diff --check 通过。
没有运行真实浏览器点击验收；动作菜单通过 get_bindings 和原生 server action.run
调用验证。卸载覆盖钩子逻辑，未执行完整卸载/重装循环。
未部署 t194，未修改截图中的发票；正式 Odoo 20.0 仍需另行兼容性验证。

## 中文金额转换（2026-09-14）

qing_account saas~19.4.1.1、qing_account_cn saas~19.4.1.2，实际运行saas-19.4 Community。
按用户要求参考Solutions16.0 mommy_qweb_chinese_num，将转换工具和QWeb组件放入qing_account。
20.0 mommy_base及既有调用中无该工具；旧独立模块没有移入20.0，本次不增加它的运行依赖。

隔离PostgreSQL16/internal Docker网络，空库安装与双模块升级均通过；最终升级24项业务测试0失败0错误：
原重置草稿6项、金额转换5项、中国基础配置与凭证13项。安装日志另外包含2项原生web测试。

转换回归覆盖整数/float/字符串/Decimal，零、负数、负零、分角、万亿连续零位、
小写、元/圆、人民币前缀、四舍五入进位、非有限值、非法值及最大金额边界。
实际QWeb渲染验证t-field与t-out，以及小写选项；凭证在cn2an不可用时输出“柒佰元整”。
采用Decimal(str(value))及ROUND_HALF_UP到分，不依赖外部转换库。
Python/XML静态检查和git diff --check通过；未部署共享租户。

## 社区版日记账分录入口（2026-09-14）

版本 saas~19.4.1.2。已核对 t194 容器原生 account_menuitem.xml：
会计、交易、日记账分录三层均仅绑定 group_account_readonly。
Solutions20.0 mommy_base/mommy_account 未发现可复用的菜单开放实现。

隔离 internal Docker 网络与全新 PostgreSQL16 数据库，使用实际19.4 Community镜像：
- 安装 qing_account_cn 及依赖成功，单独升级 qing_account 成功。
- 原生 _visible_menu_ids(debug=False) 验证四类独立账号：会计管理员可见、
  会计只读可见、普通开票不可见、内部用户不可见。
- 管理员不属于 group_account_readonly，仍有 account.move 读取权限；
  get_bindings('account.move') 包含“检查选中期初分录”。
- 入口仍指向原生 account.action_move_journal_line / account.move。
- 卸载钩子移除三层新增管理员组，保留原生只读组；验证事务回滚。
- Python AST、XML、manifest文件引用及git diff --check通过。

未执行真实浏览器点击或完整卸载循环；本轮未部署/升级 t194。

## 2026-09-14 CNF-05 客商对账与截止日账龄

版本saas~19.4.1.3。Solutions gitlab/20.0 mommy_base/mommy_account中无可直接复用的截止日账龄工具；
核对实际19.4 Community account.partial.reconcile源码，max_date为借贷行日期最大值，
amount/debit_amount_currency/credit_amount_currency分别用于本币/两侧原币回算。
测试镜像ohm/odoo-runtime:19.4-community，sha256:ca418a9e39f12b4f9bab8d9247b28b2fb8d0be9f2eeb6b3655bdc6a07bbe7146。

隔离空库cnf_statement安装成功。首轮1项失败为测试按列表首行误取付款行，改按分录ID定位。
最终升级70项业务回归通过（qing_account22、qing_account_cn48），新增11项客商对账测试。
覆盖跨月部分付款、应付/反向未结、外币、账龄边界、草稿排除、空伙伴、角色与公司、
Form及CSV/HTML转义、撤销核销回算、伙伴过滤/无到期日、跨公司隔离及超量拒绝。
随后补强所有CSV文本单元格的公式转义，相关Form/CSV测试再次单独通过。
AST、XML解析与git diff --check通过；无会计写路径，无新增外部依赖。

未部署/升级t194；未做浏览器点击、真实公司数据和物理文件客户接收验收。
专用非AR/AP预收预付科目及核销操作衔接待后续批次；不宣称CNF-05整体验收完成。
用户已安排全流程完成后统一使用公司数据测试。

## 2026-09-14 CNF-05 原生预付与核销回归

新增test_native_advance，使用真实account.payment、account.move供应商账单、
js_assign_outstanding_line、account.payment.register.action_create_payments和原生解除核销。
覆盖先付300/账单1000/次月尾款700、未使用预付款退回、解除匹配后重用付款及历史对账。
银行待付款项侧仍未核销，明确与供应商往来核销分开，不宣称银行对账完成。
本次仅新增测试与操作说明，业务模块版本不变，不引入专用科目及自定义核销引擎。
Solutions20.0 mommy_base与mommy_account当前余额核销扩展不需引入，使用原生方法。

隔离cnf_native / PostgreSQL16 / ohm/odoo-runtime:19.4-community空库安装通过（27项含原生）。
未部署t194，未执行真实银行付款或公司数据写入，未做浏览器点击验收。
最终升级25项业务测试全部通过（新增3项），含账单原生付款登记向导。AST与diff检查通过。

## 2026-09-14 CNF-05 客户预收、贷项和多单分配

新增test_native_customer_receipts四项原生回归：一笔700收款分配400/600发票后下月收300；
部分贷项200与收款800抵扣1000；未用预收500退回；解除第二张发票匹配不影响第一张且重用原付款。
沿用原生付款、贷项向导、添加未匹配付款、付款登记向导与核销方法。
Solutions20.0 mommy_base/mommy_account无须引入新工具，本批不增加自定义会计逻辑。
不修改manifest版本（仅测试与文档），不涉及中国税票红冲或真实银行动作。

原生19.4贷项向导直接ORM创建时需显式提供journal_id；仅传active_ids或move_ids不能代替必填值。
首轮及首次复测因此失败，补齐与原发票相同的日记账后重跑；未改动原生模块。
最终隔离cnf_customer / PostgreSQL16 / 19.4 Community升级29项业务测试全部通过，新增4项。
AST与git diff --check通过，未部署t194、未做真实公司数据或浏览器点击验收。

## 2026-09-14 CNF-05 付款取消、冲销与银行清算

新增4项原生会计流程回归，覆盖取消恢复账单、冲销保留历史凭证、哈希取消拒绝和银行清算撤销。
Solutions20.0 mommy_base/mommy_account无须新增运行依赖；使用实际19.4原生方法。
银行清算走statement.line.create的counterpart_account_id服务端参数与原生reconcile，
并调用action_undo_reconciliation恢复待认领。不是银行界面点击测试，不代表CE完整工作台已交付。
仅新增测试与文档，不修改业务代码或manifest版本；未升级t194，未涉及真实资金与公司数据。
隔离cnf_clearing / PostgreSQL16 / 19.4 Community空库安装通过：35 post-tests（33业务+2原生），0失败0错误。
AST、git diff --check通过；本批无运行逻辑变更，不重复进行模块升级。

## 2026-09-14 银行流水第一批

版本 saas~19.4.1.4；隔离 19.4 Community + PostgreSQL 16 空库。
新增 13 项银行导入与匹配回归，qing_account 合计 46 项业务测试；最终全新安装 48 post-tests（包含 2 项原生测试），升级 46 post-tests，均 0 failed / 0 errors。原生 Form 与 XML 视图校验通过；未做浏览器点击和真实银行样本验收。

首轮发现 res.partner.bank 账号字段已改为 account_number，修正旧 acc_number 引用。整笔匹配须先在暂记阶段设置银行流水 partner_id，再改暂记明细为待清算科目并 reconcile；核销后修改 partner_id 会触发原生同步重建明细并被已过账删除保护拦截。修正操作顺序后匹配和原生撤销均通过，不绕过原生保护。

未部署 t194，所有测试使用合成数据。

## 2026-09-14 专用银行对账工作台

版本 saas~19.4.1.5；不增加 OCA 或 Enterprise 依赖。
新增 9 项工作台业务测试，合计 55 项业务测试在隔离 saas~19.4 Community 升级回归中通过，0 failed / 0 errors。覆盖候选过滤排序、确认/撤销、流水及付款变更拦截、已清算候选排除、搜索、日期、跨公司、受限用户、外币提示及分页。Python/XML 解析通过。

首次空库安装测试包含 2 项原生测试，共 57 项；其中外币测试因合成 EUR 未启用出现 1 项错误。启用测试币种后，最终升级回归 55 项全部通过；未再次执行完整空库安装。

浏览器验收被用户中断，尚未完成工作台点击流程与布局验收。2026-09-14 用户明确要求将当前改动提交到 20.0；此交付不代表浏览器验收或真实业务验收完成，也未部署到 t194。

## 2026-09-14 工作台 Owl 3 模板修复

版本 saas~19.4.1.6。修复 t194 报告的 company 渲染错误：组件属性显式使用 this.state，事件通过组件方法调用；proxy 状态字段使用 value + input/change 绑定，避免 Owl 3 t-model 要求带 set 方法的函数所引发的第二处错误。

在同镜像隔离 Community 环境使用合成数据完成浏览器验收：debug=assets 和普通压缩资源模式均正常打开；候选选择后差额 0.00；确认后队列 3→2 并切换下一笔；跳过切换；撤销后队列恢复 3；不存在的搜索词显示空候选；银行与开始日期筛选显示空队列。XML/manifest 解析与 git diff --check 通过。本次仅改前端模板，未重复执行上一批 55 项后端测试。

合成账号初始只有管理员组，确认时被原生流水写权限拦截；为该隔离账号补充原生 group_account_user 后匹配通过。未修改产品权限、未绕过原生 ACL，未操作 t194 业务数据。本次代码交付后 t194 仍需同步代码并升级 qing_account、刷新前端资源。

## 2026-09-14 银行流水手工维护

saas~19.4.1.7：开放新建、编辑和删除；匹配或部分核销后输入字段只读，ORM 拦截编辑/删除，Reset 复用原生撤销并清理自定义匹配标记。管理员银行流水 CRUD 使用限定银行日记账类型的 ir.access，保留原生规则与凭证限制。Solutions 20.0 mommy_base/mommy_account 未发现可直接复用的流水编辑保护。

隔离 saas~19.4 Community 升级及 58 项业务回归通过，0 failed / 0 errors。新增 3 项：管理员实际 Form 新建/编辑/删除、匹配锁定→Reset→编辑删除、混合批次原子拦截；原有匹配/撤销及导入回归通过。未另做浏览器点击验收；未部署 t194。

## 2026-09-14 手工新建必填提示修复

saas~19.4.1.8：原生 move_id 为 required=True / readonly=True，新建时由 native create 自动生成。自定义表单显式 required=0 并仅在已有记录显示，避免浏览器校验尚不存在的分录。模型必填约束不变。上一批服务端 Form 测试未覆盖浏览器只读必填校验，此为验证边界。

隔离 Community 模块升级、视图校验和管理员手工 Form 新建/编辑/删除专项测试通过（1 项，0 failed / 0 errors）；未做本轮浏览器验收，未部署 t194。

## 2026-09-14 零金额新建与伪匹配锁定

saas~19.4.1.9。原生 _compute_is_reconciled 对零余额返回 True，Reset 重建零金额暂记后仍然为 True。自定义锁定必须区分该状态与真实匹配；零金额且无付款标记或核销关系的旧记录允许修正/删除。新建与改金额均按日记账币种精度拒绝零金额，表单展示“已匹配”锁定状态，旧零金额记录提示修正，并纳入待认领列表。未修改原生状态算法；实际付款匹配仍然锁定。

新增零金额/精度内零值拒绝、历史零金额修正和删除、修正后匹配及 Reset 回归。历史样本通过测试 mock 模拟旧版本保存行为，产品无上下文绕过开关。

隔离 saas~19.4 Community 升级及最终 60 项业务测试通过，0 failed / 0 errors。首轮历史样本 mock 未覆盖 ORM 缓存的约束函数，调整测试样本构造后复跑通过。未做本轮浏览器点击验收，未部署 t194。

## 2026-09-15 CNF-05 收尾

saas~19.4.1.10：原生银行日记账收/付款清算字段对管理员开放，新增只读配置诊断与无候选提示。Solutions mommy_base 无同类诊断工具；mommy_account 的支付方式行入口不适配本次原生日记账内编辑，未引入依赖。

最终隔离升级63项业务测试通过（0 failed / 0 errors）。首轮发现 XPath 只命中一个清算字段，改为两条精确路径；实测修正 CE 留空时原生回退科目的预期，新增实际付款分录验证。一次中途测试因测试容器重启未计作通过，最终完整重跑通过。浏览器完成两侧科目配置/诊断、1000账单抵扣300再登记700、300银行匹配/撤销不影响账单结清。未部署t194、未操作真实数据。
