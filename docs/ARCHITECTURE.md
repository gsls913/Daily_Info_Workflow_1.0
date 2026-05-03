# Architecture Notes

## Current Shape

The project is a local workflow-oriented system rather than a web service. The launcher runs independent collectors in sequence and each collector writes Markdown files into an Obsidian vault.

```text
workflow_launcher
  -> alpha_memo_downloader
  -> notion_wechat_downloader
  -> alpha_wechat_downloader
  -> podcast_process/podcast_workflow.py

common_libs
  -> config, paths, auth, history, article lifecycle, AI, notifications
```

## Shared Layer

`common_libs` is the source of truth for reusable logic. New shared code should be added there first. `workflow.ai` remains only as a compatibility package that forwards imports to `common_libs.ai`.

## State And Recovery

Runtime state is stored under `data/`:

- `data/history`: downloaded item history.
- `data/history/task_state.json`: item-level status for articles, memos, podcast episodes, and Tingwu transcripts.
- `data/logs/workflow_progress.json`: launcher step state.
- `data/logs/workflow_errors.log`: launcher diagnostics.
- `data/reports/daily_run_*.md`: one run summary per launcher execution.

The launcher records a step as `running` before starting its subprocess. If the process is interrupted, `--resume` treats that step as resumable and retries it.

Podcast state is stored in `data/history/podcast_download_history.json`:

- `processed_transcripts`: Tongyi Tingwu transcript IDs already converted to local notes.
- `uploaded_episodes`: Xiaoyuzhou episode IDs already downloaded/uploaded by podcast.
- `uploads`: mapping from Tingwu transcript IDs to local audio paths and episode metadata.

The podcast workflow refreshes the Tongyi Tingwu login state when it enters phases that need Tingwu, even if no new podcast episodes are eventually uploaded. Xiaoyuzhou episode metadata is parsed from page `__NEXT_DATA__`; episode duration is stored as seconds and formatted when writing Markdown.

Tingwu completed-transcript scans use paginated `getTransList` calls. The configured default page size is 48, and `podcast.completed_transcript_max_pages: 0` means scanning until the final page. After a transcript is exported, AI-processed, written to Markdown, and recorded in local history, the workflow calls Tingwu `delTrans` for that `transId`. This moves the cloud record to Tingwu recycle-bin status rather than permanently emptying it. If the record cannot be found while paging through the list, the workflow logs the exception and skips cloud deletion for that item.

Raw exported podcast transcript files under `data/podcast/transcripts/docx` and `data/podcast/transcripts/txt` are runtime artifacts. After a note is generated successfully, the workflow deletes the corresponding docx/txt by default and also cleans older raw transcript files according to `podcast.raw_transcript_retention_days`.

## Main Safety Rules

- Do not mark a Notion article downloaded until it has either succeeded or has been intentionally skipped.
- Write JSON state through a temporary file and `os.replace`.
- Keep credentials outside version control.
- Keep experimental browser automation isolated from the main workflow until it is stable.
