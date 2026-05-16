from investment_system.common.article.article_manager import archive_read_articles_from_folders, clean_old_read_articles


def test_clean_old_read_articles_moves_markdown_and_images_to_recycle_bin(tmp_path, monkeypatch):
    monkeypatch.setenv("INFO_COLLECTOR_RECYCLE_BIN_DIR", str(tmp_path / "recycle"))

    base = tmp_path / "articles"
    read_subfolder = base / "已读" / "0-Inbox"
    attachments = tmp_path / "attachments"
    read_subfolder.mkdir(parents=True)
    attachments.mkdir()

    image = attachments / "pic.png"
    image.write_bytes(b"image")
    md = read_subfolder / "old.md"
    md.write_text("- **日期**: 2000-01-01\n![[pic.png]]\n", encoding="utf-8")

    deleted_articles, deleted_images = clean_old_read_articles(
        str(base),
        ["0-Inbox"],
        days_threshold=1,
        attachment_dir=str(attachments),
    )

    assert deleted_articles == 1
    assert deleted_images == 1
    assert not md.exists()
    assert not image.exists()
    assert list((tmp_path / "recycle").rglob("old.md"))
    assert list((tmp_path / "recycle").rglob("pic.png"))


def test_archive_read_articles_renames_when_destination_exists(tmp_path):
    base = tmp_path / "articles"
    inbox = base / "0-Inbox"
    read_inbox = base / "已读" / "0-Inbox"
    inbox.mkdir(parents=True)
    read_inbox.mkdir(parents=True)
    source = inbox / "same.md"
    source.write_text("- [x] **是否已读**\nnew", encoding="utf-8")
    existing = read_inbox / "same.md"
    existing.write_text("old", encoding="utf-8")

    moved = archive_read_articles_from_folders(str(base), ["0-Inbox"])

    assert moved == 1
    assert existing.read_text(encoding="utf-8") == "old"
    assert (read_inbox / "same_1.md").read_text(encoding="utf-8") == "- [x] **是否已读**\nnew"

