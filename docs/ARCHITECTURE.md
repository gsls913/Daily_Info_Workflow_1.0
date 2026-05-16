# Architecture Notes

## Current Shape

The project is a local workflow-oriented system rather than a web service. The launcher runs independent collectors in sequence and each collector writes Markdown files into an Obsidian vault.

```text
investment_system/launcher
  -> investment_system/collectors/alpha_memo
  -> investment_system/collectors/notion
  -> investment_system/collectors/alpha_wechat
  -> investment_system/collectors/podcast/podcast_workflow.py

investment_system/common
  -> config, paths, auth, history, article lifecycle, AI, notifications
```

The real implementation now lives under `investment_system/`. Legacy top-level directories are thin compatibility wrappers for existing scripts, shortcuts, and imports.

- `investment_system/launcher/launcher_ui.py`: terminal panels, menus, prompts, and width-aware rendering.
- `investment_system/launcher/workflow_steps.py`: the five top-level workflow step definitions.
- `investment_system/common/runtime/task_result.py`: normalized task result shape used by the launcher and reports.
- `investment_system/common/runtime/recycle_bin.py`: local recycle-bin handling for Markdown and image deletes.

## Shared Layer

`investment_system/common` is the source of truth for reusable logic. New shared code should be added there first. `common_libs` and `workflow.ai` remain only as compatibility packages that forward old imports to the new package layout.

## State And Recovery

Runtime state is stored under `data/`:

- `data/history`: downloaded item history.
- `data/history/task_state.json`: item-level status for articles, memos, podcast episodes, and Tingwu transcripts.
- `data/logs/workflow_progress.json`: launcher step state.
- `data/logs/workflow_errors.log`: launcher diagnostics.
- `data/reports/daily_run_*.md`: one run summary per launcher execution.

AI runtime configuration is stored in `data/config/ai_models.json`. `config/config.yaml` still holds general workflow settings, but the active provider/model/API/runtime parameters are read from the JSON AI config.

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
- User-facing Markdown and image deletes should go through the local recycle bin, not permanent deletion. Runtime logs, temp files, and bounded state cleanup may still be deleted according to retention rules.
- AI responses should pass through the shared AI client / quality layer so hidden thinking tags are stripped before downstream use.
