import json

from investment_system.common.runtime import last_downloads


def test_record_markdown_overwrites_new_manifest_and_deduplicates(tmp_path, monkeypatch):
    manifest_path = tmp_path / "last_downloaded_markdowns.json"
    monkeypatch.setattr(last_downloads, "LAST_DOWNLOAD_MARKDOWNS_FILE", manifest_path)

    md_path = tmp_path / "article.md"
    md_path.write_text("hello", encoding="utf-8")

    last_downloads.start_new_manifest()
    last_downloads.record_markdown(last_downloads.CATEGORY_WECHAT, md_path, title="Article")
    last_downloads.record_markdown(last_downloads.CATEGORY_WECHAT, md_path, title="Article")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(data["categories"][last_downloads.CATEGORY_WECHAT]) == 1
    assert data["categories"][last_downloads.CATEGORY_WECHAT][0]["path"] == str(md_path)


def test_delete_last_wechat_download_removes_md_and_referenced_images(tmp_path, monkeypatch):
    from investment_system.launcher import run_workflow

    monkeypatch.setenv("INFO_COLLECTOR_RECYCLE_BIN_DIR", str(tmp_path / "recycle"))
    attachment_dir = tmp_path / "attachments"
    attachment_dir.mkdir()
    image_path = attachment_dir / "pic.png"
    image_path.write_bytes(b"fake image")

    md_path = tmp_path / "wechat.md"
    md_path.write_text("content\n![[pic.png]]\n", encoding="utf-8")

    saved = {}
    monkeypatch.setattr(run_workflow, "ATTACHMENT_DIR", str(attachment_dir))
    monkeypatch.setattr(run_workflow, "save_last_download_manifest", lambda manifest: saved.update(manifest))

    manifest = {
        "categories": {
            run_workflow.CATEGORY_ALPHA_MEMO: [],
            run_workflow.CATEGORY_WECHAT: [{"path": str(md_path), "title": "Wechat"}],
            run_workflow.CATEGORY_PODCAST: [],
        }
    }

    run_workflow.execute_delete_last_downloaded_content([run_workflow.CATEGORY_WECHAT], manifest)

    assert not md_path.exists()
    assert not image_path.exists()
    assert list((tmp_path / "recycle").rglob("wechat.md"))
    assert list((tmp_path / "recycle").rglob("pic.png"))
    assert saved["categories"][run_workflow.CATEGORY_WECHAT] == []


def test_delete_last_download_skips_moved_markdown(tmp_path, monkeypatch):
    from investment_system.launcher import run_workflow

    saved = {}
    missing_path = tmp_path / "moved.md"
    monkeypatch.setattr(run_workflow, "save_last_download_manifest", lambda manifest: saved.update(manifest))

    manifest = {
        "categories": {
            run_workflow.CATEGORY_ALPHA_MEMO: [{"path": str(missing_path), "title": "Moved"}],
            run_workflow.CATEGORY_WECHAT: [],
            run_workflow.CATEGORY_PODCAST: [],
        }
    }

    run_workflow.execute_delete_last_downloaded_content([run_workflow.CATEGORY_ALPHA_MEMO], manifest)

    assert saved["categories"][run_workflow.CATEGORY_ALPHA_MEMO] == []


def test_collect_markdown_files_under_root_is_recursive_and_scoped(tmp_path):
    from investment_system.launcher import run_workflow

    root = tmp_path / "memo"
    nested = root / "已读" / "0-Inbox"
    nested.mkdir(parents=True)
    md_path = nested / "memo.md"
    md_path.write_text("memo", encoding="utf-8")
    txt_path = nested / "memo.txt"
    txt_path.write_text("not markdown", encoding="utf-8")

    files = run_workflow._collect_markdown_files_under_root(root)

    assert files == [md_path.resolve()]


def test_collect_markdown_files_can_exclude_read_folder(tmp_path):
    from investment_system.launcher import run_workflow

    root = tmp_path / "wechat"
    unread = root / "0-Inbox"
    read = root / "已读" / "6-其他"
    unread.mkdir(parents=True)
    read.mkdir(parents=True)

    unread_md = unread / "unread.md"
    read_md = read / "read.md"
    unread_md.write_text("unread", encoding="utf-8")
    read_md.write_text("read", encoding="utf-8")

    files = run_workflow._collect_markdown_files_under_root(
        root,
        include_read=False,
        category=run_workflow.CATEGORY_WECHAT,
    )

    assert files == [unread_md.resolve()]


def test_delete_local_wechat_markdowns_removes_referenced_images(tmp_path, monkeypatch):
    from investment_system.launcher import run_workflow

    monkeypatch.setenv("INFO_COLLECTOR_RECYCLE_BIN_DIR", str(tmp_path / "recycle"))
    wechat_root = tmp_path / "wechat"
    wechat_root.mkdir()
    attachment_dir = tmp_path / "attachments"
    attachment_dir.mkdir()

    image_path = attachment_dir / "pic.png"
    image_path.write_bytes(b"fake image")
    md_path = wechat_root / "article.md"
    md_path.write_text("content\n![[pic.png]]\n", encoding="utf-8")

    monkeypatch.setattr(run_workflow, "WECHAT_ARTICLE_BASE_DIR", str(wechat_root))
    monkeypatch.setattr(run_workflow, "ATTACHMENT_DIR", str(attachment_dir))

    stats = run_workflow.execute_delete_local_markdowns_by_type({
        run_workflow.CATEGORY_WECHAT: [md_path],
    })

    assert stats["deleted_md"] == 1
    assert stats["deleted_images"] == 1
    assert not md_path.exists()
    assert not image_path.exists()
    assert stats["recycled_md"] == 1
    assert stats["recycled_images"] == 1
    assert list((tmp_path / "recycle").rglob("article.md"))
    assert list((tmp_path / "recycle").rglob("pic.png"))


def test_delete_local_markdowns_skips_paths_outside_category_root(tmp_path, monkeypatch):
    from investment_system.launcher import run_workflow

    monkeypatch.setenv("INFO_COLLECTOR_RECYCLE_BIN_DIR", str(tmp_path / "recycle"))
    memo_root = tmp_path / "memo"
    memo_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("keep", encoding="utf-8")

    monkeypatch.setattr(run_workflow, "MEMO_BASE_DIR", str(memo_root))

    stats = run_workflow.execute_delete_local_markdowns_by_type({
        run_workflow.CATEGORY_ALPHA_MEMO: [outside],
    })

    assert stats["deleted_md"] == 0
    assert stats["skipped_missing"] == 1
    assert outside.exists()

