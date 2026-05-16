# 实用微程序

这里放和主信息工作流相互独立的小工具。它们可以通过主程序首页的“实用微程序”菜单启动，也可以单独用 Python 运行。

## 有道云文档链接转 Markdown

```powershell
python micro_programs\youdao_note_to_md.py "有道云分享链接"
python micro_programs\youdao_note_to_md.py "有道云分享链接1" "有道云分享链接2"
```

默认保存到会议纪要 Inbox：`C会议纪要\0-Inbox`。

这个工具会优先调用有道云分享接口获取真实笔记正文，而不是只抓分享页外壳。笔记里的远程图片会下载到 Obsidian 统一附件目录，并把 Markdown 中的图片引用改成本地文件名。
