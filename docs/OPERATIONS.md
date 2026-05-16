# 运行与维护手册

## 日常运行

推荐从统一启动器运行：

```powershell
python workflow\workflow_launcher\run_workflow.py
```

`workflow\workflow_launcher\run_workflow.py` 是兼容入口；真实实现位于 `investment_system\launcher\run_workflow.py`。新代码和新引用应使用 `investment_system` 包。

非交互式运行：

```powershell
python workflow\workflow_launcher\run_workflow.py --yes --power none
```

预演模式不会下载、上传、写入或删除：

```powershell
python workflow\workflow_launcher\run_workflow.py --dry-run
```

## 运行前检查

主启动器会先做三类工作：

- 配置校验：检查关键路径、数量配置、小宇宙 URL、AI 配置文件和 API Key 是否存在。
- 运行产物清理：非 dry-run 时清理过期日志、每日摘要、临时文件，并限制任务状态和主错误日志大小。
- AI 预检：向当前默认 AI 模型发送一个极短请求。若失败，后续所有工作流会停止，并弹出系统通知。

如果 AI 预检失败，优先检查：

- `data/credentials/AI_api_keys.txt`
- `data/config/ai_models.json`
- MiniMax 或当前 AI 服务的套餐、余额、token plan、限流状态

如果当前 provider 是 `zhongxin`，预检失败提示里会额外提醒检查 VPN。中信 AI 在开启 VPN 时可能无法访问；关闭 VPN 后重试通常是第一步排查。

## 每日运行摘要

每次正式运行或 dry-run 都会生成摘要：

```text
data/reports/daily_run_YYYYMMDD_HHMMSS.md
```

摘要包含：

- 是否 dry-run
- AI 预检结果
- 配置提醒
- 每个工作流成功/失败状态
- 用时
- 当前限量策略说明

主启动器会按 `retention.report_days` 和 `retention.report_max_files` 清理最早的每日摘要，避免 `data/reports/` 无限增长。

## 信息汇总日报

正式完整运行时，启动器会在程序开始阶段判断当天是否第一次完整运行。如果是，五条采集工作流执行完毕并生成运行摘要后，会触发信息汇总日报。

日报生成逻辑：

- 从 `data/history/task_state.json` 找出当天 `markdown_saved` 的纪要、微信文章、Notion 微信收藏和播客笔记。
- 会议纪要优先读取 `# 会议全文`；没有该章节时读取基本信息后的主体内容，并排除 `# AI 评价` 和末尾元信息。
- 微信文章读取 `# 正文` 后的正文原文，并排除 `# AI 评价` 和末尾元信息。
- 播客只读取 `# AI 要点`，不读取后面的原始转录全文。
- 生成时会临时写入 `data/logs/daily_digest_sources_YYYYMMDD.md` 用于拼接来源内容；日报生成成功或失败后都会尽量清理该临时文件。

MiniMax 官方文档列出的 M2.7 输入输出总 token/context window 为 204800。项目仍默认使用保守配置：`daily_digest.max_input_chars=120000`，超过后按 `daily_digest.chunk_chars` 分段提炼，再进行最终汇总。

日报输出目录：

```text
<obsidian_base_dir>\Bz日报周报\未读
```

文件名形如：

```text
20260503_信息汇总日报.md
```

日报开头包含日期、生成时间、来源文档数、`是否已读`、人工标签和我的评价。勾选已读后，下一次正式运行会移动到年份/月度归档目录，例如：

```text
<obsidian_base_dir>\Bz日报周报\已读\2026年\2026年5月
```

日报不参与过期删除，会长期保留。

## 信息汇总周报

正式完整运行时，启动器也会在程序开始阶段判断本周是否第一次完整运行。如果是，五条采集工作流执行完毕、日报流程结束后，会触发信息汇总周报。

周报生成逻辑：

- 从 `Bz日报周报\未读` 和 `Bz日报周报\已读` 下递归查找本周所有 `*_信息汇总日报*.md`。
- 本周按周一到周日计算。
- 周报只使用日报正文，不直接读取纪要、微信文章或播客原文。
- 生成时会临时写入 `data/logs/weekly_digest_sources_YYYYMMDD.md`，其中 `YYYYMMDD` 是本周周一日期；周报生成成功或失败后都会尽量清理该临时文件。

周报输出到与日报相同的未读目录：

```text
<obsidian_base_dir>\Bz日报周报\未读
```

周报文件名使用本周第一天（周一）日期，例如：

```text
20260105_信息汇总周报.md
```

勾选已读后，会与日报一样归档到年份/月度目录：

```text
<obsidian_base_dir>\Bz日报周报\已读\2026年\2026年1月
```

周报不参与过期删除，会长期保留。

## 运行产物清理

每次正式运行统一启动器时，会执行一次轻量维护清理。清理范围只限项目运行产物，不会删除凭证文件，也不会删除 Obsidian 中未归档的正文内容。

配置项都在 `config/config.yaml` 的 `retention` 下：

- `log_days`: 各模块 `logs/*.log` 的保留天数。
- `log_max_files_per_dir`: 每个日志目录最多保留的 `.log` 文件数量，超过时删除最早的文件。
- `workflow_error_log_max_kb`: `data/logs/workflow_errors.log` 超过该大小后，只保留尾部最近内容。
- `report_days`: `data/reports/daily_run_*.md` 的保留天数。
- `report_max_files`: 每日摘要最多保留数量，超过时删除最早的文件。
- `task_state_days`: `data/history/task_state.json` 中已稳定完成且长期未更新任务的保留天数。
- `task_state_max_entries`: `task_state.json` 最多保留任务数量，超过时保留最近更新的任务。
- `tmp_days`: `.tmp` 和 `.tmp.*` 临时文件的保留天数。

如果 `safety.allow_local_delete=false`，启动器会跳过运行产物清理。

## 防止欠账过多

连续多天没有运行时，各来源可能积累很多新内容。默认限量策略在 `config/config.yaml` 中调整：

- `wechat.new_account_download_count`: 新公众号首次下载数量。
- `wechat.max_download_per_account`: 已有历史记录的公众号，本轮最多下载数量。
- `podcast.new_account_download_count`: 新小宇宙账号首次上传节目数量。
- `podcast.max_download_per_account`: 已有历史记录的小宇宙账号，本轮最多上传节目数量。
- `memo.new_source_download_count`: 新纪要来源首次下载数量。
- `memo.max_download_per_source`: 已有历史记录的纪要来源，本轮最多下载数量。
- Notion 微信收藏默认不限制下载数量，会处理当前数据库中所有待下载项。

## 来源与标签配置

公众号、小宇宙播客账号和 AI 标签候选项统一维护在 `data/config/set_config.xlsx`：

- `wechat_account`: 公众号名称、简称、分类、单篇/聚合。
- `podcast_account`: 播客名称、url、简称。
- `memo_tag_options`: 公司、行业。

可以从启动器进入 `配置与状态 -> 管理来源与标签配置` 增删这些条目。小宇宙账号不再以 `config/config.yaml` 的 `podcast.accounts` 为运行来源；如果 Excel 中缺少 `podcast_account` sheet，系统会从旧 YAML 配置自动创建一次。

## 安全开关

`config/config.yaml` 的 `safety` 控制危险操作：

- `dry_run`: 全局默认 dry-run 开关，通常保持 `false`，临时预演用命令行 `--dry-run` 更清晰。
- `allow_local_delete`: 是否允许删除本地运行产物，例如旧原始转录文件、过期已读笔记。
- `allow_cloud_delete`: 是否允许删除云端记录，例如听悟 `delTrans`。
- `confirm_destructive_actions`: 交互式主程序是否展示危险操作提醒。
- `recycle_bin_dir`: 本地 Markdown 和图片删除前移入的回收站目录，默认在 Obsidian `信息收集器\_overall\_recycle_bin` 下。
- `recycle_bin_retention_days`: 回收站保留天数，默认 10 天；超过后由启动器维护清理彻底删除。

日志、每日运行摘要、临时文件和任务状态属于运行产物，仍按 `retention` 直接清理。Markdown 正文和图片附件这类用户内容会优先移入本地回收站。

## 实用微程序

主界面选择 `实用微程序` 后，可运行独立小工具，不启动完整信息流。

- 有道云文档链接转 Markdown：支持一次输入多个有道云分享链接。
- AlphaPai 纪要链接转 Markdown：支持一次输入多个 AlphaPai 详情链接，保存到会议纪要 Inbox，并复用批量下载流程生成 Markdown、标签和 AI 评价。

AlphaPai 链接微程序支持：

```text
https://alphapai-web.rabyte.cn/reading/self-summary-detail?id=...
https://alphapai-web.rabyte.cn/reading/home/meeting/detail?articleId=...
```

如果 Markdown 已存在但还没有 `# AI 评价`，再次运行同一链接会尝试补充 AI 标签和评价。

## AI 配置

AI 实际生效配置在：

```text
data/config/ai_models.json
```

可在主界面进入 `其他操作 -> 更改 AI API / 模型配置` 调整 provider、模型、并发、API 地址、超时/重试和 `tag_judgment`、`short_text`、`long_thinking` 三类场景参数。

AI 客户端会在统一出口清理 `<think>...</think>` 和 `<thinking>...</thinking>`，这些模型思考内容不会写入本地 Markdown、日报、周报或 AI 评价。

## 任务状态

细粒度任务状态写入：

```text
data/history/task_state.json
```

它记录每篇文章、每条纪要、每期播客、每条听悟转录最近的处理状态，例如：

- `downloading`
- `markdown_saved`
- `uploaded_to_tingwu`
- `ai_processing`
- `failed`
- `skipped_downloaded`

这个文件用于排查“哪一步失败了”，不建议手动编辑。

启动器会按 `retention.task_state_days` 和 `retention.task_state_max_entries` 自动裁剪这个文件。状态为 `running` 或 `failed` 的任务会优先保留，方便排查未完成问题。
