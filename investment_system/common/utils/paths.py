import os
from investment_system.common.config.config_loader import (
    get_obsidian_base_dir as _get_obsidian_base_dir,
    get_memo_base_dir as _get_memo_base_dir,
    get_wechat_article_base_dir as _get_wechat_article_base_dir,
    get_attachment_dir as _get_attachment_dir,
    get as _cfg,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

OBSIDIAN_BASE_DIR = _get_obsidian_base_dir()

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CREDENTIALS_DIR = os.path.join(DATA_DIR, "credentials")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
DATA_LOGS_DIR = os.path.join(DATA_DIR, "logs")
DATA_CONFIG_DIR = os.path.join(DATA_DIR, "config")
RUNTIME_DIR = os.path.join(DATA_DIR, "runtime")

TOKEN_FILE = os.path.join(CREDENTIALS_DIR, "alphapai_tokens.json")
ALPHAPAI_INFO_FILE = os.path.join(CREDENTIALS_DIR, "alphapai_info.txt")
AI_API_KEYS_FILE = os.path.join(CREDENTIALS_DIR, "AI_api_keys.txt")
NOTION_CONFIG_FILE = os.path.join(CREDENTIALS_DIR, "notion_token_and_databases_id.txt")

MEMO_HISTORY_FILE = os.path.join(HISTORY_DIR, "memo_download_history.json")
WECHAT_HISTORY_FILE = os.path.join(HISTORY_DIR, "wechat_download_history.json")
NOTION_WECHAT_HISTORY_FILE = os.path.join(HISTORY_DIR, "notion_wechat_history.json")
NOTION_LINK_HISTORY_FILE = os.path.join(HISTORY_DIR, "notion_link_history.json")
COMPANY_MEMO_HISTORY_FILE = os.path.join(HISTORY_DIR, "company_memo_download_history.json")
PODCAST_HISTORY_FILE = os.path.join(HISTORY_DIR, "podcast_download_history.json")
LAST_DOWNLOAD_MARKDOWNS_FILE = os.path.join(HISTORY_DIR, "last_downloaded_markdowns.json")
ALPHA_WECHAT_FAILED_ARTICLES_FILE = os.path.join(HISTORY_DIR, "alpha_wechat_failed_articles.json")
NOTION_WECHAT_FAILED_ARTICLES_FILE = os.path.join(HISTORY_DIR, "notion_wechat_failed_articles.json")
NOTION_LINK_FAILED_ARTICLES_FILE = os.path.join(HISTORY_DIR, "notion_link_failed_articles.json")

WORKFLOW_PROGRESS_FILE = os.path.join(DATA_LOGS_DIR, "workflow_progress.json")
WORKFLOW_ERROR_LOG_FILE = os.path.join(DATA_LOGS_DIR, "workflow_errors.log")
TINGWU_RUNTIME_DIR = os.path.join(RUNTIME_DIR, "tingwu")
TINGWU_PROFILE_DIR = os.path.join(TINGWU_RUNTIME_DIR, "tingwu_browser_profile")
TINGWU_STORAGE_STATE_FILE = os.path.join(CREDENTIALS_DIR, "tingwu_profile_state.json")
TINGWU_PROFILE_STATUS_FILE = os.path.join(TINGWU_RUNTIME_DIR, "tingwu_profile_status.json")
TINGWU_LAST_UPLOAD_FILE = os.path.join(TINGWU_RUNTIME_DIR, "tingwu_last_upload_python.json")

SET_CONFIG_FILE = os.path.join(DATA_CONFIG_DIR, "set_config.xlsx")

MEMO_BASE_DIR = _get_memo_base_dir()
WECHAT_ARTICLE_BASE_DIR = _get_wechat_article_base_dir()
ATTACHMENT_DIR = _get_attachment_dir()

PODCAST_BASE_DIR = os.path.join(OBSIDIAN_BASE_DIR, _cfg('podcast.base_dir', 'C小宇宙'))


def get_script_dir(file_path):
    return os.path.dirname(os.path.abspath(file_path))


def get_project_root():
    return PROJECT_ROOT


def get_obsidian_base_dir():
    return OBSIDIAN_BASE_DIR

