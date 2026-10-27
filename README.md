# Dota2 Tracker for AstrBot

追踪群友的 DOTA2 对局：订阅本群、绑定 SteamID，自动追踪比赛并推送图文战报，播报段位变动，定时发布日报/周报，并支持比赛、玩家、英雄、物品、今日英雄和群友名册查询。

本插件是 [@sjtdev/koishi-plugin-dota2tracker](https://github.com/sjtdev/koishi-plugin-dota2tracker) 的 AstrBot 移植版。

> **平台支持**：QQ OneBot v11（AstrBot 平台类型 `aiocqhttp`，如 NapCat、Lagrange）是主要平台，功能最完整并经过验收。Telegram、Discord 等其他适配器通过 AstrBot 通用接口兼容：指令、图片战报、自动追踪、段位播报和日报周报都能用，但没有提示撤回、离群过滤和群名片/头像（见“已知平台限制”）。

## 安装

1. 在 AstrBot 插件市场安装，或把本仓库放到 `data/plugins/astrbot_plugin_dota2tracker/`。
2. AstrBot 会按 `requirements.txt` 安装依赖（`httpx[socks]`、`aiosqlite` 等，大部分 AstrBot 已自带）。
3. 在插件配置中填入 **STRATZ API Token**（<https://stratz.com/api> 获取）。未配置 Token 时插件可以加载，但无法查询，后台追踪也不会启动。
4. 图片渲染使用 AstrBot 的文转图服务（`html_render`）。默认的在线 t2i 服务可以直接用；自建 t2i 服务时请在 AstrBot 配置中设置 `t2i_endpoint`。

数据保存在 `data/plugin_data/astrbot_plugin_dota2tracker/`：

| 文件 | 内容 |
| --- | --- |
| `dota2tracker.sqlite3` | 频道订阅、玩家绑定、段位记录、比赛扩展数据、已发送比赛、等待解析的比赛、缓存和审计日志 |
| `raw_data/patch_notes/` | 历史补丁说明（英雄/物品模板的改动历史） |

## 指令

指令写法与原插件一致：中文别名（如 `/绑定`），或原 Koishi 的指令名 `dota2tracker.<子指令>`（如 `/dota2tracker.bind`）。下表“指令”列为子指令名。

| 指令 | 中文别名 | 说明 |
| --- | --- | --- |
| `help` | `DOTA2指南` `DOTA2帮助` `DOTA2说明` | 帮助表格图片 |
| `subscribe` | `订阅本群` | 订阅本群，启用战报追踪与播报 |
| `unsubscribe` | `取消订阅` | 取消订阅 |
| `bind <steam_id> [nick_name]` | `绑定` | 绑定 SteamID，可附带 20 字以内的昵称 |
| `unbind` | `取消绑定` | 解除自己的绑定 |
| `rename <nick_name>` | `改名` | 修改绑定昵称 |
| `query-members` | `查询群友` | 群友名册图片（胜率、最近比赛时间） |
| `query-match <match_id> [-p] [-t 模板]` | `查询比赛` | 查询比赛；`-p` 等待解析完成后再发布 |
| `query-recent-match [SteamID/昵称] [-p] [-t 模板]` | `查询最近比赛` | 查询自己或指定玩家的最近比赛 |
| `query-player <SteamID/昵称> [-o 英雄]` | `查询玩家` | 玩家信息图片，可指定英雄 |
| `query-hero <英雄> [-r]` | `查询英雄` | 英雄数据图片；`-r` 随机英雄 |
| `query-item <物品>` | `查询物品` | 物品图片，支持别名和模糊搜索 |
| `hero-of-the-day [SteamID/昵称] [-d 天数]` | `今日英雄` | 按近期表现和天梯热门推荐英雄 |

发送 `/DOTA2指南` 获取全部指令的帮助表格图片；在任意指令后加 `-h`（如 `/绑定 -h`）查看该指令的文字帮助；发送 `/dota2tracker` 列出全部子指令。

## 配置

基础配置在插件配置页完成（`_conf_schema.json`），字段与原插件一一对应：

- **基础**：`stratz_api_token`（secret）、`data_parsing_timeout_minutes`、`proxy_address`（secret）、`suppress_api_network_errors`
- **OpenDota 回退**：`enable_opendota_fallback`、`opendota_api_key`（secret）、`opendota_ip_stack`
- **消息**：`default_locale`、`use_hero_nicknames`、`url_in_message_type`、`max_send_item_count`、`show_item_list_at_too_much_items`、`custom_item_alias`、`auto_recall_tips`
- **段位**：`rank_broad_switch`、`rank_broad_star`、`rank_broad_leader`、`rank_broad_fun`
- **报告**：`daily_report_switch`、`daily_report_hours`、`daily_report_show_combi`、`weekly_report_switch`、`weekly_report_day_hours`、`weekly_report_show_combi`
- **模板**：`template_match`（`match_1`/`match_2`/`match_2+`）、`template_player`、`template_hero`、`player_rank_estimate`、`patch_notes_retrieval_depth`
- **渲染**（新增）：`render_scale`、`render_image_type`、`render_jpeg_quality`

日报和周报按 AstrBot 进程所在的时区计时。`weekly_report_day_hours` 填 `[周几, 小时]`，周几取 1–7（7 为周日）。

### 字体

插件不内置、也不嵌入任何字体，图片直接使用渲染服务（t2i）所在机器的系统默认字体。使用 AstrBot 默认的远程 t2i 时字体由该服务决定；自建 t2i 时，在那台机器上安装中文字体（如 Noto Sans CJK）即可改变出图字体。

## 可视化视图

在 AstrBot WebUI 的插件详情页可以打开四个视图：

| 视图 | 功能 |
| --- | --- |
| 概览 `overview` | 配置状态、调度器、最近轮询时间、等待解析的比赛、OneBot 成员缓存、今日 OpenDota 调用次数、最近错误 |
| 订阅 `subscriptions` | 查看订阅频道与绑定玩家；取消/恢复订阅、设置频道语言、改名、解绑；导出和导入（备份/恢复） |
| 模板 `templates` | 用内置样例数据预览全部模板（中英文、变体）；渲染统计 |
| 诊断 `diagnostics` | 脱敏配置、缓存统计与清理、API 连通性测试、审计日志、下载诊断包 |

视图跟随 WebUI 的语言和亮暗主题。所有修改操作都会写入审计日志，导出文件和诊断包不包含 Token、代理认证等敏感信息。

## 备份与恢复

在“订阅”视图点“导出”可下载频道、绑定和段位数据（不含 Token）。“导入”只接受本插件导出的文件：先预检显示将新增和跳过的条目，确认后才写入；已存在的绑定不会被覆盖，重复导入是安全的。也可以停止插件后直接备份 `dota2tracker.sqlite3`。

本插件不提供从 Koishi 版迁移数据的功能，需要在 AstrBot 中重新订阅和绑定。

## 已知平台限制

- **非 OneBot 平台**（Telegram、Discord 等）：提示消息不撤回；不做离群过滤，已绑定玩家始终计入；名称和头像使用绑定昵称、Steam 名称和 Steam 头像。Telegram 对图片尺寸有限制（宽+高 ≤ 10000 像素），很长的物品列表图可能发送失败。
- **提示撤回**：仅在 OneBot 上可用，通过 `delete_msg` 实现。协议端拒绝撤回时直接忽略。
- **离群过滤**：通过 OneBot 的 `get_group_member_list` 判断。接口失败时不过滤（保守放行），并记录告警。
- **群名片和头像**：通过 `get_group_member_info` 获取。获取失败时依次回退到绑定昵称、Steam 名称和 Steam 头像。
- **私聊等待解析**：`查询比赛 -p` 在私聊中同样会推送到原会话 UMO。
- **成员缓存刷新**：与原插件一致，订阅和绑定时立即刷新，之后每小时全量刷新一次；也可以在概览视图里手动刷新。

## 开发

```bash
python -m pytest            # 单元/渲染/任务测试（不需要网络和 Token）
ruff check . && ruff format --check .
python scripts/preview_template.py match_1 zh-CN out.png   # 需要 Playwright，本地模拟 t2i
python scripts/views/build.py   # 由 scripts/views/src 生成 views/*/index.html（勿直接修改生成文件）
```

## 许可证与来源

本项目以 **GPL-3.0** 发布（见 [`LICENSE`](./LICENSE)），沿用原项目的许可证。GraphQL 查询、模板、图片和文案来自 [sjtdev/koishi-plugin-dota2tracker](https://github.com/sjtdev/koishi-plugin-dota2tracker)，版权归原作者所有。游戏数据来自 [STRATZ](https://stratz.com)、[OpenDota](https://www.opendota.com) 和 Valve 官方 datafeed；`dotaconstants` 数据来自 [odota/dotaconstants](https://github.com/odota/dotaconstants)（MIT）。Dota 2 是 Valve Corporation 的商标。
