# My Investment System 2.0

个人投资信息收集工作流。系统会从 AlphaPai、Notion 微信收藏和 AlphaPai 订阅公众号收集内容，转换为 Markdown，写入 Obsidian，并在可用时生成 AI 标签或 AI 评价。

## 工作流

统一入口：

```powershell
python workflow\workflow_launcher\run_workflow.py
```

默认人工模式会保留原有交互流程：

1. 选择运行结束后的电脑操作。
2. 确认将要执行的步骤。
3. 逐步执行并在失败时询问是否继续。

执行顺序：

1. `alpha_memo_downloader/alphapai_download.py --auto`
   下载 AlphaPai 会议纪要，写入 Obsidian 会议纪要目录，并进行 AI 标签/评价。
2. `notion_wechat_downloader/notion_collector.py`
   从 Notion 数据库读取未下载的微信文章，下载成功后标记为已下载。
3. `alpha_wechat_downloader/fetch_wechat_articles.py --auto`
   从 AlphaPai 订阅公众号列表拉取文章，转换为 Markdown，并生成 AI 评价。
4. `podcast_process/podcast_workflow.py`
   归档已读小宇宙笔记，刷新通义听悟登录态，从通义听悟下载已完成转录并生成 AI 要点，再拉取配置的小宇宙账号新节目上传到通义听悟。
5. 信息汇总日报
   如果本次是当天第一次正式完整运行，四条工作流结束后会基于当天新增的纪要、微信文章和播客笔记生成一篇日报。
6. 信息汇总周报
   如果本次是本周第一次正式完整运行，会在日报完成后基于本周所有日报生成一篇周报。

## 工作流详解

### 1. AlphaPai 会议纪要下载

入口：`alpha_memo_downloader/alphapai_download.py --auto`

这个工作流使用 `data/credentials/alphapai_info.txt` 和缓存 token 登录 AlphaPai，按 `config/config.yaml` 中 `memo.tag_configs` 配置的来源逐类拉取会议纪要。每条纪要会转换成 Markdown，写入 Obsidian 的 `B会议纪要` 对应子目录，并调用统一 AI 客户端生成标签和评价。下载历史写入 `data/history/memo_download_history.json`，用于跳过已处理纪要。

首次运行某个来源时按 `memo.new_source_download_count` 控制下载数量；已有历史记录后按 `memo.max_download_per_source` 控制每轮新增数量，避免长时间未运行后一次性处理过多内容。

### 2. Notion 微信收藏下载

入口：`notion_wechat_downloader/notion_collector.py`

这个工作流读取 `data/credentials/notion_token_and_databases_id.txt` 中的 Notion Token 和数据库 ID，查询 Notion 数据库里“是否已下载”不等于“已下载”的微信文章链接。文章会下载为 Markdown，写入 Obsidian 的微信文章 Inbox；下载成功后会回写 Notion 状态，并在 `data/history/notion_wechat_history.json` 记录本地处理状态，便于 Notion 状态更新失败后恢复去重。

Notion 微信收藏默认不限制待处理条数，会处理当前数据库里所有待下载项；Notion API 每页数量由 `notion.page_size` 控制。

### 3. AlphaPai 订阅公众号下载

入口：`alpha_wechat_downloader/fetch_wechat_articles.py --auto`

这个工作流从 AlphaPai 订阅公众号列表获取账号，再拉取每个账号的新文章。文章会通过公共微信下载器转换成 Markdown，按 AI 或规则判断的分类写入 Obsidian 的 `B微信文章`。图片会保存到配置的附件目录，失败文章会进入本地失败队列，后续运行可继续处理。下载历史写入 `data/history/wechat_download_history.json`。

新公众号按 `wechat.new_account_download_count` 控制首次下载数量；已有历史记录的公众号按 `wechat.max_download_per_account` 控制每轮新增数量。每个公众号的历史记录会按 `wechat.max_history_per_account` 和 `wechat.history_clean_threshold` 自动收缩。

### 4. 小宇宙播客与通义听悟

入口：`podcast_process/podcast_workflow.py`

这个工作流由三部分组成：先归档 Obsidian 中已勾选“是否已读”的播客笔记；再读取通义听悟已完成转录，导出 docx/txt，调用统一 AI 客户端整理成播客笔记；最后按 `podcast.accounts` 拉取小宇宙新节目，下载音频并上传通义听悟等待转录。处理历史写入 `data/history/podcast_download_history.json`。

成功生成 Markdown 后，默认会删除本次导出的原始转录文件，并把听悟云端记录移入回收站。相关行为由 `podcast.cleanup_raw_transcripts_after_process`、`podcast.raw_transcript_retention_days`、`podcast.delete_tingwu_record_after_process` 和安全开关共同控制。

### 5. 信息汇总日报

入口：统一启动器自动触发。

启动器在正式完整运行一开始判断“今天是否第一次运行”。如果是第一次，四条工作流全部跑完后，会读取 `data/history/task_state.json` 中当天状态为 `markdown_saved` 的文档路径，抽取可用于汇总的正文，并调用统一 AI 客户端生成日报。

正文抽取规则：

- 会议纪要：优先取 `# 会议全文`；没有该章节时取基本信息之后、末尾元信息之前的主体内容，不取开头基本信息和 `# AI 评价`。
- 微信文章：取 `# 正文` 之后的原文正文，不取开头基本信息和 `# AI 评价`。
- 小宇宙播客：只取 `# AI 要点`，不取后面的原始转录全文。

日报输出到：

```text
<obsidian_base_dir>\Bz日报周报\未读
```

文件名形如 `20260503_信息汇总日报.md`。日报开头包含日期、生成时间、来源文档数、`是否已读`、人工标签和我的评价。勾选 `- [x] **是否已读**` 后，下一次正式运行会把日报移动到：

```text
<obsidian_base_dir>\Bz日报周报\已读\2026年\2026年5月
```

日报不会按天数自动删除，会长期保留。

### 6. 信息汇总周报

入口：统一启动器自动触发。

启动器在正式完整运行一开始判断“本周是否第一次运行”。如果是本周第一次，会在日报生成流程结束后，扫描 `Bz日报周报\未读` 和 `Bz日报周报\已读` 下本周所有日报，把日报正文拼接后交给 AI 生成周报。周报不直接读取会议纪要、微信文章或播客原文。

周报文件仍输出到：

```text
<obsidian_base_dir>\Bz日报周报\未读
```

文件名使用本周第一天（周一）的日期，例如 `20260105_信息汇总周报.md`。已读后归档到同样的年份/月度目录，例如：

```text
<obsidian_base_dir>\Bz日报周报\已读\2026年\2026年1月
```

周报也不会按天数自动删除，会长期保留。

## 常用命令

```powershell
# 完整人工运行
python workflow\workflow_launcher\run_workflow.py

# 只运行某一步
python workflow\workflow_launcher\run_workflow.py --step 1
python workflow\workflow_launcher\run_workflow.py --step 2
python workflow\workflow_launcher\run_workflow.py --step 3
python workflow\workflow_launcher\run_workflow.py --step 4

# 查看状态
python workflow\workflow_launcher\run_workflow.py --status

# 从失败或中断步骤恢复
python workflow\workflow_launcher\run_workflow.py --resume

# 非交互运行，适合后续接入计划任务
python workflow\workflow_launcher\run_workflow.py --yes --power none --continue-on-error

# 非交互运行，任一步失败后停止
python workflow\workflow_launcher\run_workflow.py --yes --power none --fail-fast

# 预演模式：只展示将执行的步骤，不实际下载、写入、上传或删除
python workflow\workflow_launcher\run_workflow.py --dry-run
```

`--power` 可选值：

- `none`: 完成后无额外操作
- `sleep`: 完成后 5 分钟睡眠
- `hibernate`: 完成后 5 分钟休眠
- `shutdown`: 完成后 5 分钟关机

## 配置

主配置文件：

```text
config/config.yaml
```

它集中管理 Obsidian 路径、AlphaPai API、微信文章分类、会议纪要标签、Notion 字段和 AI 并发等配置。

运行产物清理配置在 `retention` 下：

- `log_days`: 各模块 `logs/*.log` 的保留天数。
- `log_max_files_per_dir`: 每个日志目录最多保留的 `.log` 文件数量。
- `workflow_error_log_max_kb`: 主错误日志 `data/logs/workflow_errors.log` 超过该大小后自动截尾。
- `report_days`: 每日运行摘要 `data/reports/daily_run_*.md` 的保留天数。
- `report_max_files`: 每日运行摘要最多保留数量。
- `task_state_days`: `data/history/task_state.json` 中长期未更新任务的保留天数。
- `task_state_max_entries`: `task_state.json` 最多保留任务数量。
- `tmp_days`: 异常中断残留 `.tmp` 文件的保留天数。

统一启动器在非 dry-run 运行时会先执行这些维护清理；dry-run 不会删除或截断任何文件。

小宇宙账号配置在 `config/config.yaml` 的 `podcast.accounts`：

```yaml
podcast:
  accounts:
    - name: "播客名称"
      url: "https://www.xiaoyuzhoufm.com/podcast/xxxxxxxxxxxxxxxxxxxxxxxx"
      short_name: "播客简称"
```

小宇宙工作流还可以在 `podcast` 下配置：

- `refresh_tingwu_login_each_run`: 每次运行播客工作流时都主动刷新通义听悟登录态。
- `completed_transcript_page_size`: 查询听悟已完成转录时每页请求数量，默认 48。
- `completed_transcript_max_pages`: 查询听悟已完成转录的最大页数；`0` 表示扫描到最后一页。
- `delete_tingwu_record_after_process`: 成功生成本地 Markdown 后是否删除听悟云端转录记录。听悟会先移入回收站，不是永久清空。
- `tingwu_list_page_size`: 下载/删除时遍历听悟记录列表的每页数量，默认 48。
- `transcript_chunk_size_chars`: 文字稿超过该字符数才分段整理。
- `ai_single_max_tokens` / `ai_chunk_max_tokens` / `ai_synthesis_max_tokens`: 播客 AI 整理输出长度上限。
- `cleanup_raw_transcripts_after_process`: 成功生成 Markdown 后是否删除导出的 docx/txt 原始转录文件。
- `raw_transcript_retention_days`: 清理历史原始转录文件的保留天数。

播客笔记输出到：

```text
<obsidian_base_dir>\B小宇宙\未读
```

开始处勾选 `- [x] **是否已读**` 后，下次运行会移动到 `B小宇宙\已读`，并按配置的保留天数清理旧笔记。

播客 Markdown 的基本信息区会包含节目名称、播客账号、发布日期、节目时长和小宇宙节目链接。节目时长来自小宇宙页面 `__NEXT_DATA__` 中单集的 `duration` 秒数字段，并在本地格式化。

日报/周报配置在 `daily_digest` 和 `weekly_digest` 下：

- `daily_digest.base_dir`: 日报周报在 Obsidian 中的根目录，默认 `Bz日报周报`。
- `unread_folder_name` / `read_folder_name`: 未读和已读目录名。
- `max_input_chars`: 单次交给 AI 的最大字符数，默认 120000。
- `chunk_chars`: 分段提炼时每段字符数，默认 80000。
- `daily_digest.max_tokens` / `chunk_max_tokens` / `synthesis_max_tokens`: 日报生成、分段提炼和最终汇总的输出 token 上限。
- `weekly_digest.max_tokens` / `chunk_max_tokens` / `synthesis_max_tokens`: 周报生成、分段提炼和最终汇总的输出 token 上限。

本地凭证和运行状态放在：

```text
data/credentials/
data/history/
data/logs/
```

这些目录包含账号、token、API key、历史记录和运行日志，不应提交到版本控制。

每次主程序运行会生成一份每日运行摘要到 `data/reports/`，细粒度任务状态写入 `data/history/task_state.json`。运行、限量、安全开关和故障处理说明见 `docs/OPERATIONS.md`。

## 依赖安装

```powershell
pip install -r requirements.txt
playwright install chromium
```

## 目录说明

- `workflow/workflow_launcher/`: 统一启动器、进度恢复和错误诊断。
- `common_libs/`: 公共库。新代码优先放这里。
- `alpha_memo_downloader/`: AlphaPai 会议纪要下载。
- `notion_wechat_downloader/`: Notion 微信收藏下载。
- `alpha_wechat_downloader/`: AlphaPai 公众号文章下载。
- `tools/`: 辅助工具。
- `podcast_process/`: 播客/通义听悟相关代码；`podcast_workflow.py` 是正式入口，`tingwu_python_workflow/` 是正式听悟登录、上传和导出实现。
- `podcast_process/podcast_workflow.py`: 小宇宙播客正式工作流入口。

## 维护约定

- 新的共享逻辑放入 `common_libs`。
- `workflow.ai` 仅作为旧 import 路径兼容层保留。
- 写入 JSON 状态文件时应使用原子写入，避免中断后留下损坏文件。
- 失败重试应依赖 `data/history` 和 `data/logs/workflow_progress.json`，不要靠手工猜测。
