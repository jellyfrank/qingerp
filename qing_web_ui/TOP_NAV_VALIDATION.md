# 顶部应用导航验证 — 2026-09-17

## 范围

桌面应用切换移至顶部常驻导航；按实际字体与翻译名称测量宽度，空间不足时收纳，优先保留当前应用。更多入口可搜索全部有权限的应用。侧栏应用切换按钮改为当前应用标题，业务菜单和原生手机导航保留。表单/关联列表面包屑使用独立第二行。配色使用主题变量。

基于 origin/20.0 fbb9a54 的独立任务分支开发，未混入原工作区未提交文件。未改 Odoo 核心。

## 复用与版本

- 目标为 saas~19.4 Community，不是最终 Odoo 20。
- 复核运行镜像 `ohm/saas-deploy:tenant-t194-194-580` 的 NavBar JS/XML；navbar.js SHA256 为 `7a6f70872a911c8812b60ef92b1dcb1937163dc2862a48728892d78c89c6f4ae`。
- 检查 Solutions origin/19.0 mommy_base manifest 与导航相关搜索，没有可复用的顶部菜单布局工具。继续复用 qing_web_ui 导航 patch、图标 registry、原生 menu service；不增加基础模块依赖。

## 已验证

- 使用同一运行镜像、新建隔离数据库 qing_nav_test_20260917，安装 qing_web_ui 成功；独立端口 8079 升级进程退出成功后才重启隔离服务。
- 浏览器正常压缩资源加载，无 JS 页面异常。首次发现 libsass 将 CSS min() 当 Sass 函数处理，改用 Min() 后重新编译通过。
- 搜索更多应用、切换中文长名称应用、当前应用保留、1440/1024 桌面收纳、深色模式与收起侧栏。
- 表单打开后面包屑在应用栏下方正常显示，深色表单无页面异常。
- 390px 下原生手机入口保留。
- fitQingApps 宽度边界 70–699px 共630项通过，另验证全展示/当前项保留/无空间/空菜单。
- XML解析、JS语法和 git diff --check 通过。

HOOT用例已加入现有套件，完整HOOT套件尚未执行。隔离测试菜单为原生 ir.ui.menu 和 res.partner 动作，不将测试菜单写入 t194。未在真实受限用户、多公司账号下逐项验收。

本次交付代码；t194 在线实例未发布此改动。

## 2026-09-18 正式 Odoo 20 修复

目标已切换为 qingerp.qingsolution.com / Odoo 20.0 Community，核心 SHA
8738f2d4d99e02630999f56008eda1de2922ce73。正式版 NavBar.root 是 signal ref；
旧 root.el 读取静默返回 undefined，使宽度测量与下拉菜单定位跳过。
修正为 root()，包含关闭菜单后的焦点回退路径。模块版本 20.0.1.0.7。

新增真实 NavBar 挂载回归，使用 signal ref 测量并验证宽→窄→宽时可见应用更新。
隔离空库浏览器确认宽屏显示 Discuss/Apps/Settings、更多弹层锚定顶部按钮、
Apps 路由可打开、850px 收纳至 Discuss/Apps，未出现 JS 页面异常。

正式20.0隔离HOOT完整模块套件9/9通过。

## 2026-09-19 Developer mode shortcut (20.0.1.0.8)

- Added a systray shortcut at sequence 100, before messaging, for every internal user (including non-administrators). Portal/public users do not get the shortcut.
- Uses the native router to activate debug mode, preserving the active route. Once active, the native developer menu replaces the shortcut and provides Leave Debug Mode; no duplicated debug icon or additional access rights.
- Isolated fresh database on the current qingerp Odoo 20.0 Community image: all 13 qing_web_ui HOOT tests passed, including four real-navbar visibility/activation cases.
- Browser verification: activation and deactivation succeeded, /odoo/discuss was preserved, no JavaScript errors, shortcut visible at desktop 1440px and mobile 390px.

## 2026-09-19 Theme-aware developer ladybug (20.0.1.0.9)

- Replaced the inactive shortcut and native active developer icon with one inline SVG ladybug: closed wings when inactive, open wings when active. The native developer dropdown and permissions remain unchanged.
- Active color and subtle background use the selected theme accent. Two brief wingbeats on activation settle into a static open-wing state; OS and theme reduced-motion preferences disable animation.
- Fresh isolated database on the current qingerp Odoo 20.0 Community image: all 14 qing_web_ui HOOT tests passed, including the native active developer dropdown regression.
- Real browser verification passed for the teal light theme, amber dark theme, reduced motion, and Leave Debug Mode. No JavaScript page errors; compiled SCSS and template inheritance loaded successfully.
- Production authenticated interaction was not exercised; the browser checks above used the isolated test database.
