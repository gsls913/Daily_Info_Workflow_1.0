# Tongyi Tingwu Python Workflow

This folder contains a reusable Python implementation for Tongyi Tingwu:

1. Keep a dedicated persistent browser profile for login.
2. Upload a local audio/video file and start transcription.
3. Query transcription status.
4. Export completed transcript text as a `.docx` file.
5. Delete processed cloud transcript records.

Do not put real passwords, cookies, or browser profile folders into a shared zip.
Runtime files are intentionally ignored by `.gitignore`.

## Files

- `tingwu_common.py`: shared helpers for cookies, request headers, and credential parsing.
- `tingwu_profile.py`: manages the dedicated browser profile and login state.
- `tingwu_api_upload.py`: requests upload credentials, uploads to OSS, and starts transcription.
- `tingwu_export_download.py`: exports a completed transcript and downloads the `.docx`.
- `tingwu_delete_record.py`: lists and deletes Tingwu transcript records after local processing.
- `tingwu_login_probe.py`: older login probe kept for reference/debugging.
- `tongyi_password.example.txt`: credential file format example.
- `examples/workflow_commands.ps1`: common commands.

## Requirements

Python 3.10+ is recommended. Install dependencies:

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

The scripts use local Microsoft Edge by default:

```text
C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
```

If Edge is installed elsewhere, edit `EDGE` in `tingwu_profile.py` and `tingwu_login_probe.py`.

## First-Time Setup

Copy the template and fill in your own account information:

```powershell
copy tongyi_password.example.txt tongyi_password.txt
notepad tongyi_password.txt
```

Then create/refresh the dedicated browser login state:

```powershell
python tingwu_profile.py auto-login --headed --wait-verification-seconds=60
```

The script explicitly switches to `账号密码登录`, fills the credential file, and saves:

```text
tingwu_profile_state.json
```

If Aliyun asks for extra verification, finish it manually in the opened browser window. After success, rerun:

```powershell
python tingwu_profile.py status
```

## Upload And Start Transcription

Use the default test path in the script:

```powershell
python tingwu_api_upload.py
```

Or pass an explicit file:

```powershell
python tingwu_api_upload.py "C:\path\to\audio.m4a"
```

You can also pass multiple files or folders:

```powershell
python tingwu_api_upload.py "C:\path\a.m4a" "C:\path\b.mp3"
python tingwu_api_upload.py "C:\path\audio_folder" --recursive
```

This matches the web UI behavior for selecting multiple files at once: the UI keeps
one pending upload list, but after clicking `开始转写`, Tingwu still creates one
upload/transcription task per file. The script follows the same model: one command
accepts many files, then uploads them one by one.

Limits enforced before upload:

- single batch: up to 50 files
- single file duration: up to 6 hours
- audio formats: `mp3/wav/m4a/wma/aac/ogg/amr/flac/aiff`
- video formats: `mp4/wmv/m4v/flv/rmvb/dat/mov/mkv/webm/avi/mpeg/3gp/ogg`
- audio file size: up to 500M
- video file size: up to 6G

If more than 50 files are provided, use the default batching behavior:

```powershell
python tingwu_api_upload.py "C:\path\audio_folder" --recursive
```

The script validates all files first, then splits work into batches of 50.
Duration validation uses `ffprobe` when available. If `ffprobe` is not found,
duration checking is skipped unless you pass:

```powershell
python tingwu_api_upload.py "C:\path\audio_folder" --require-duration-check
```

To check files without uploading:

```powershell
python tingwu_api_upload.py "C:\path\audio_folder" --recursive --dry-run
```

The upload script uses this configuration:

- language: `中英文自由说` (`lang=fspk`)
- translation: disabled
- speaker separation: multi-speaker discussion
- source type: local file

After upload it writes a local status file:

```text
tingwu_last_upload_python.json
```

## Export Transcript

Export a specific transcription:

```powershell
python tingwu_export_download.py --trans-id=dej8nbp6odw59pog --out-dir=downloads
```

Or export the latest completed transcription:

```powershell
python tingwu_export_download.py --out-dir=downloads
```

Export options match the tested web UI settings:

- content: original transcript only
- format: `.docx`
- speaker info: enabled
- timestamps: disabled

## List Or Delete Records

List recent records with 48 items per page:

```powershell
python tingwu_delete_record.py --list --page-size=48
```

Delete a specific transcription record:

```powershell
python tingwu_delete_record.py --trans-id=dej8nbp6odw59pog --delete --yes
```

The list API uses `pageNo` and `pageSize`; the main podcast workflow uses `pageSize=48` and keeps turning pages until the target record is found or the list is exhausted. Deletion calls `delTrans`, which moves the record to Tingwu recycle-bin status instead of permanently emptying it.

## API Principle

Upload flow:

```text
generatePutLink -> OSS PUT -> syncPutLink -> getTransStatus
```

Export flow:

```text
exportTrans -> getExportStatus -> download returned OSS URL
```

Delete flow:

```text
getTransList(pageNo/pageSize) -> find transId -> delTrans -> optional getTransStatus check
```

Login flow:

```text
dedicated Edge profile -> account/password login -> storage_state JSON -> API Cookie header
```

The scripts avoid depending on manually copied browser DevTools cookies. Once the dedicated profile has logged in, API scripts read `tingwu_profile_state.json` automatically.

## Security Notes

Never share these runtime files:

- `tongyi_password.txt`
- `tingwu_profile_state.json`
- `tingwu_browser_profile/`
- downloaded transcripts that may contain private content

If you zip this folder for someone else, include only the source scripts, README, examples, and templates.
