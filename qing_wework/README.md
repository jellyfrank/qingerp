# qing_wework · WeCom / 企业微信

QingERP free-edition WeCom integration (LGPL-3), ported from the Ohm Solutions
`mommy_wework` module (Solutions `20.0` @ `0233a26f`).

- Employee / department sync, OAuth and QR login
- Application messages and templates, group robots and schedules
- Intelligent bot (callback and WebSocket), application menus, workbench

Advanced features (attendance, approvals, calendar, live chat) live in the
commercial `qing_wework_pro` module.

Requirements: Python package `odoo-wechatpy` (MIT). WebSocket intelligent bots also require `websocket-client` (Apache-2.0).

OAuth and intelligent-bot callback URLs use the `/qing_wework/...` prefix. The application message callback remains `/wework/message`; configure the applicable URLs in the WeCom admin console. `qing_wework` declares `excludes: mommy_wework`
because both modules define the same `wework.*` models.
