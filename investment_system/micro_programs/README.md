# 实用微程序

这里放和主信息工作流相互独立的小工具。它们可以通过主程序首页的“实用微程序”菜单启动，也可以单独用 Python 运行。

## 有道云文档链接转 Markdown

```powershell
python micro_programs\youdao_note_to_md.py "有道云分享链接"
python micro_programs\youdao_note_to_md.py "有道云分享链接1" "有道云分享链接2"
```

默认保存到会议纪要 Inbox：`C会议纪要\0-Inbox`。

这个工具会优先调用有道云分享接口获取真实笔记正文，而不是只抓分享页外壳。笔记里的远程图片会下载到 Obsidian 统一附件目录，并把 Markdown 中的图片引用改成本地文件名。

## 本地文档转 Markdown

```powershell
python micro_programs\word_doc_to_md.py "D:\path\to\meeting.docx"
python micro_programs\word_doc_to_md.py "D:\path\to\meeting.pdf" "D:\path\to\meeting.txt"
```

默认保存到会议纪要 Inbox：`C会议纪要\0-Inbox`。

这个工具会读取本地 `.docx`、`.doc`、`.pdf`、`.txt` 文件，并套用会议纪要 Inbox 的基本信息模板。`.docx/.doc` 会尽量保留标题、列表缩进、加粗、斜体、删除线、文字颜色和表格；`.doc` 需要本机安装 LibreOffice/OpenOffice 以便先转换为 `.docx`；`.pdf/.txt` 会提取文字并保存为同一模板的 Markdown。
