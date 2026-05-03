import os
import yaml

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_FILE = os.path.join(_PROJECT_ROOT, "config", "config.yaml")

_config_cache = None


def _load_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if not os.path.exists(_CONFIG_FILE):
        _config_cache = {}
        return _config_cache

    try:
        with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
            _config_cache = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Warning: Failed to load config.yaml: {e}")
        _config_cache = {}

    return _config_cache


def reload_config():
    global _config_cache
    _config_cache = None
    return _load_config()


def get(key_path, default=None):
    config = _load_config()

    keys = key_path.split('.')
    value = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default

    return default if value is None else value


def get_path(key_path, default=None):
    value = get(key_path, default)
    if value is None:
        return default
    return str(value)


def get_obsidian_base_dir():
    return get('paths.obsidian_base_dir', r"D:\path\to\Obsidian\Vault")


def get_memo_base_dir():
    base = get_obsidian_base_dir()
    subdir = get('paths.memo_base_dir', 'B会议纪要')
    return os.path.join(base, subdir)


def get_wechat_article_base_dir():
    base = get_obsidian_base_dir()
    subdir = get('paths.wechat_article_base_dir', 'B微信文章')
    return os.path.join(base, subdir)


def get_attachment_dir():
    base = get_obsidian_base_dir()
    subdir = get('paths.attachment_dir', '_overall\\_attachment')
    return os.path.join(base, subdir)


def get_alphapai_base_url():
    return get('alphapai.base_url', 'https://alphapai-web.rabyte.cn')


def get_alphapai_api(endpoint_key):
    base_url = get_alphapai_base_url()
    path = get(f'alphapai.api.{endpoint_key}', '')
    if path:
        return f"{base_url}{path}"
    return base_url


def get_retention_days(category):
    defaults = {
        'log': 30,
        'memo_read_article': 90,
        'wechat_read_article': 30,
        'history': 90,
    }
    return get(f'retention.{category}_days', defaults.get(category, 30))


def get_wechat_category_mapping():
    return get('wechat.category_mapping', {
        "投资": "1-投资",
        "宏观": "2-宏观",
        "商业": "3-商业",
        "科技": "4-科技",
        "工作": "5-工作",
        "其他": "6-其他",
    })


def get_wechat_read_subfolders():
    return get('wechat.read_subfolders', [
        "1-投资", "2-宏观", "3-商业", "4-科技", "5-工作", "6-其他"
    ])


def get_memo_read_subfolders():
    return get('memo.read_subfolders', [
        "0-Inbox", "1-转记", "2-预约会议", "3-宏观", "4-策略", "5-社会服务", "6-商贸零售"
    ])


def get_memo_tag_configs():
    return get('memo.tag_configs', [])


def get_ai_config(key_path, default=None):
    return get(f'ai.{key_path}', default)


def get_podcast_accounts():
    return get('podcast.accounts', [])
