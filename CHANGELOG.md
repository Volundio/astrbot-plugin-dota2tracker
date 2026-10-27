# Changelog

## [1.1.1] - 2026-10-27

全量代码审查后的缺陷修复。

### 修复

- 战报推送：渲染或格式化失败时不再把比赛标记为已发送，改为保留并在下一轮重试（最多 3 轮）。
- 战报推送：发布期间通过 `-p` 加入的订阅者不再丢失；发布中途重载插件后只补发尚未发送的目标，不再重复推送。
- 战报推送：超过解析超时 24 小时仍无数据的比赛自动放弃，不再无限占用轮询和 OpenDota 配额。
- 日报：当天比赛 MVP 分全为负时不再导致所有群的日报发送失败；单个群的数据异常不再影响其他群，周报同理。
- 日报：统计区间限定为前一自然日，凌晨的比赛不再被计入两份日报。
- 配置：代理地址格式无效时提示错误并忽略代理，不再导致插件无法加载。
- 存储：数据库事务在开始阶段被取消时正确回滚，不再导致后续写入失败。
- 指令：在 AstrBot 中重命名指令或修改别名后，参数解析保持正确。
- 指令：恢复 `-h` / `--help` 指令帮助，新增根指令 `dota2tracker` 列出全部子指令。
- 指令：内部错误不再把原始异常文本回复到聊天中。
- 图片：昵称或 Steam 名中伪造的图片占位符不再被替换为图片数据。
- WebUI：导入改为先整体校验、再单事务写入；无效 SteamID 和缺少 UMO 的记录被忽略并计数，未订阅的频道也能正确恢复。

### 移除

- 指令：移除 `/dota2tracker <子指令>` 指令组写法（只给其中一种写法设置权限时，另一种仍对所有人开放），请使用中文别名或 `dota2tracker.<子指令>`。

### Fixed

- Match reports: A render or formatting failure no longer marks the match as sent; it stays pending and is retried on the next round (up to 3 rounds).
- Match reports: Subscribers added with `-p` while a report is being published are no longer lost; after a reload mid-publication only the remaining destinations are sent, without duplicates.
- Match reports: Matches that still have no data 24 hours after the parse timeout are dropped instead of being polled forever and using OpenDota quota.
- Daily report: A day where every MVP score is negative no longer stops the reports of all channels; a channel with inconsistent data no longer affects the others, and the same applies to the weekly report.
- Daily report: The report covers exactly the previous calendar day, so early-morning matches are no longer counted in two reports.
- Config: An invalid proxy address is reported and ignored instead of preventing the plugin from loading.
- Storage: A database transaction cancelled while starting is rolled back and no longer breaks later writes.
- Commands: Arguments are still parsed correctly after a command is renamed or re-aliased in AstrBot.
- Commands: Restored `-h` / `--help` command help and added the root command `dota2tracker` listing all subcommands.
- Commands: Internal errors no longer send raw exception text to the chat.
- Images: Fake image placeholders in nicknames or Steam names are no longer replaced with image data.
- WebUI: Import validates the whole file first and writes in a single transaction; invalid SteamIDs and rows without a UMO are ignored and counted, and unsubscribed channels are restored correctly.

### Removed

- Commands: Removed the `/dota2tracker <subcommand>` command-group form (a permission set on one form left the other open to everyone); use the Chinese aliases or `dota2tracker.<subcommand>`.

**Full Changelog**: https://github.com/Volundio/astrbot-plugin-dota2tracker/compare/v1.1.0...v1.1.1

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
