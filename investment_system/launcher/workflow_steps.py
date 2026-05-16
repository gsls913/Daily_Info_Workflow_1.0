from __future__ import annotations

import os


TRACKED_MARKDOWN_STEP_INDICES = {1, 4, 5}


def build_workflow_steps(project_root: str) -> list[dict]:
    return [
        {
            "name": "AlphaPai会议纪要下载",
            "script": os.path.join(project_root, "investment_system", "collectors", "alpha_memo", "alphapai_download.py"),
            "args": ["--auto"],
            "timeout_seconds": 7200,
            "description": "下载AlphaPai会议纪要到Obsidian",
            "dependencies": ["playwright", "requests"],
            "common_errors": {
                "token": "AlphaPai登录token过期或无效",
                "network": "网络连接失败，无法访问AlphaPai服务器",
                "playwright": "Playwright浏览器未正确安装",
            },
        },
        {
            "name": "Notion微信文章收集",
            "script": os.path.join(project_root, "investment_system", "collectors", "notion", "notion_collector.py"),
            "args": [],
            "timeout_seconds": 3600,
            "description": "从Notion数据库下载微信文章",
            "dependencies": ["notion-client", "requests", "beautifulsoup4"],
            "common_errors": {
                "notion_token": "Notion API token无效或过期",
                "database": "Notion数据库ID配置错误",
                "network": "网络连接失败，无法访问Notion API",
            },
        },
        {
            "name": "Notion链接收藏收集",
            "script": os.path.join(project_root, "investment_system", "collectors", "notion", "notion_link_collector.py"),
            "args": [],
            "timeout_seconds": 1800,
            "description": "从Notion数据库保存网页/微信文章链接到Obsidian合集",
            "dependencies": ["notion-client", "requests", "beautifulsoup4"],
            "common_errors": {
                "notion_token": "Notion API token无效或过期",
                "database": "Notion数据库ID配置错误",
                "network": "网络连接失败，无法访问Notion API或网页标题抓取失败",
            },
        },
        {
            "name": "Alpha派微信公众号文章下载",
            "script": os.path.join(project_root, "investment_system", "collectors", "alpha_wechat", "fetch_wechat_articles.py"),
            "args": ["--auto"],
            "timeout_seconds": 7200,
            "description": "下载订阅的微信公众号文章",
            "dependencies": ["playwright", "requests", "beautifulsoup4", "Pillow"],
            "common_errors": {
                "token": "AlphaPai登录token过期或无效",
                "network": "网络连接失败，无法访问AlphaPai服务器",
                "excel": "Excel配置文件不存在或格式错误",
            },
        },
        {
            "name": "小宇宙播客下载与AI要点整理",
            "script": os.path.join(project_root, "investment_system", "collectors", "podcast", "podcast_workflow.py"),
            "args": [],
            "timeout_seconds": 7200,
            "description": "归档已读播客笔记，处理通义听悟已完成转录，并上传新小宇宙节目",
            "dependencies": ["requests", "playwright", "beautifulsoup4"],
            "common_errors": {
                "tingwu": "通义听悟登录状态失效或转录任务未完成",
                "xiaoyuzhou": "小宇宙页面解析失败或音频链接失效",
                "ai": "AI模型调用失败或API Key配置错误",
            },
        },
    ]

