# Changelog

## [1.1.0] - 2026-10-27

移除字体配置，图片改用渲染机系统默认字体。

### 移除

- 字体：删除字体目录扫描、监听与子集化内嵌，模板使用渲染服务所在机器的系统默认字体。
- 配置：移除 `font_path`、`fonts`（sans/serif/mono）和 `font_embed_max_kb` 配置项。
- WebUI：移除 `fonts/rescan` 接口和模板视图中的字体卡片，改为显示渲染统计。
- 依赖：移除 `fonttools` 与 `watchfiles`。

### Removed

- Fonts: Removed font directory scanning, watching and subset embedding; templates use the default system fonts of the t2i renderer machine.
- Config: Removed the `font_path`, `fonts` (sans/serif/mono) and `font_embed_max_kb` options.
- WebUI: Removed the `fonts/rescan` API and the fonts card in the templates view; it now shows render statistics.
- Dependencies: Dropped `fonttools` and `watchfiles`.

**Full Changelog**: https://github.com/Volundio/astrbot-plugin-dota2tracker/commits/v1.1.0

## [1.0.0] - 2026-10-27

从 koishi-plugin-dota2tracker 移植到 AstrBot 的首个版本。

### 新增

- 指令：订阅/取消订阅、绑定/解绑/改名、群友名册、比赛/最近比赛/玩家/英雄/物品查询、今日英雄和帮助，共 13 条，支持中文别名。
- 追踪：Stratz 比赛自动追踪、请求解析与超时发布，可选 OpenDota 回退及每日调用计数。
- 播报：图文战报、段位星级/冠绝变动播报（普通与整活模板）、日报与周报。
- 模板：全部 13 个原有图片模板改写为 Jinja2，支持中英文。
- 平台：QQ OneBot v11 为主要平台（提示撤回、离群过滤、群名片/头像）；Telegram、Discord 等平台通用兼容。
- WebUI：概览、订阅、模板、诊断四个可视化视图，支持订阅数据导出/导入备份。
- 存储：SQLite 持久化订阅、绑定、段位、比赛扩展与待解析比赛，重载后自动恢复。

### Added

- Commands: 13 commands (subscribe/unsubscribe, bind/unbind/rename, members, match/recent match/player/hero/item lookup, hero of the day, help) with Chinese aliases.
- Tracking: Automatic Stratz match tracking, parse requests and timed publishing, with optional OpenDota fallback and daily call counter.
- Broadcasts: Image match reports, rank star/leaderboard change broadcasts (normal and fun templates), daily and weekly reports.
- Templates: All 13 original image templates ported to Jinja2, in Chinese and English.
- Platforms: QQ OneBot v11 as the primary platform (tip recall, member filtering, group cards/avatars); best-effort support for Telegram, Discord and others.
- WebUI: Overview, subscriptions, templates and diagnostics views, with export/import backups.
- Storage: SQLite for subscriptions, bindings, ranks, match extensions and pending matches, restored after reload.
