# Podcast Process

小宇宙播客和通义听悟处理模块，已接入主工作流第 4 步。

正式入口：

- `podcast_workflow.py`: 归档已读播客笔记、处理听悟已完成转录、拉取小宇宙新节目并上传听悟。
- `xiaoyuzhou_parser.py`: 解析小宇宙播客主页和单集信息。
- `tingwu_python_workflow/`: 通义听悟登录态、上传、转录状态查询、导出下载和云端记录删除的正式实现。

运行机制：

- 每次运行需要通义听悟的阶段时，会先尝试刷新听悟登录态，即使当天没有新的小宇宙节目，也会访问听悟以领取每日登录赠送时长。
- 小宇宙页面通过 `__NEXT_DATA__` 解析播客和单集数据；单集时长来自 `duration` 秒数字段，节目链接由 `episode/{eid}` 生成。
- 短/中等文字稿默认一次交给 MiniMax-M2.7 整理；超过 `config/config.yaml` 中 `podcast.transcript_chunk_size_chars` 的超长文字稿才会分段整理并最终合并。
- 查询听悟已完成转录时默认每页请求 48 条；`completed_transcript_max_pages: 0` 表示持续翻页直到最后一页。
- 成功生成 Markdown 后，默认会删除本次导出的听悟 docx/txt 原始转录文件，并按配置清理历史原始转录文件。
- Markdown 成功写入、历史状态保存后，默认会调用听悟 `delTrans` 删除对应云端转录记录；该操作会把记录移入听悟回收站状态，不是永久清空。

Markdown 基本信息：

- 播客账号
- 节目名称
- 发布日期
- 时长
- 小宇宙节目链接

常用命令：

```powershell
python workflow\workflow_launcher\run_workflow.py --step 4
python podcast_process\podcast_workflow.py --phase archive
python podcast_process\podcast_workflow.py --phase process-completed
python podcast_process\podcast_workflow.py --phase upload-new
```

新增播客请维护 `data/config/set_config.xlsx` 中的 `podcast_account` sheet，或从启动器进入 `配置与状态 -> 管理来源与标签配置` 添加。
