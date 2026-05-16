import time


def test_clean_old_files_uses_custom_timestamp_getter(tmp_path):
    from investment_system.collectors.podcast import podcast_workflow

    old_audio = tmp_path / "old.m4a"
    fresh_audio = tmp_path / "fresh.m4a"
    old_note = tmp_path / "old.txt"
    old_audio.write_bytes(b"audio")
    fresh_audio.write_bytes(b"audio")
    old_note.write_text("not audio", encoding="utf-8")

    now = time.time()
    timestamps = {
        old_audio: now - 11 * 24 * 3600,
        fresh_audio: now - 2 * 24 * 3600,
        old_note: now - 11 * 24 * 3600,
    }

    deleted = podcast_workflow.clean_old_files(
        tmp_path,
        10,
        (".m4a",),
        timestamp_getter=lambda path: timestamps[path],
    )

    assert deleted == 1
    assert not old_audio.exists()
    assert fresh_audio.exists()
    assert old_note.exists()


def test_process_completed_transcripts_respects_max_items(monkeypatch):
    from investment_system.collectors.podcast import podcast_workflow

    completed = [{"transId": f"tid-{idx}"} for idx in range(8)]
    processed: list[str] = []

    monkeypatch.setattr(
        podcast_workflow,
        "get_completed_transcripts",
        lambda cookie, max_pages=None: completed,
    )

    def fake_export(cookie, target, history):
        processed.append(target["transId"])
        return True

    monkeypatch.setattr(podcast_workflow, "export_and_process_transcript", fake_export)

    count = podcast_workflow.process_completed_transcripts("cookie", {"processed_transcripts": []}, max_items=3)

    assert count == 3
    assert processed == ["tid-0", "tid-1", "tid-2"]


def test_process_completed_transcripts_zero_max_items_means_unlimited(monkeypatch):
    from investment_system.collectors.podcast import podcast_workflow

    completed = [{"transId": f"tid-{idx}"} for idx in range(3)]
    processed: list[str] = []

    monkeypatch.setattr(
        podcast_workflow,
        "get_completed_transcripts",
        lambda cookie, max_pages=None: completed,
    )
    monkeypatch.setattr(
        podcast_workflow,
        "export_and_process_transcript",
        lambda cookie, target, history: processed.append(target["transId"]) or True,
    )

    count = podcast_workflow.process_completed_transcripts("cookie", {}, max_items=None)

    assert count == 3
    assert processed == ["tid-0", "tid-1", "tid-2"]


def test_mark_completed_transcripts_processed_adds_ids_without_export(monkeypatch):
    from investment_system.collectors.podcast import podcast_workflow

    monkeypatch.setattr(
        podcast_workflow,
        "get_completed_transcripts",
        lambda cookie, max_pages=None: [{"transId": "a"}, {"transIdStr": "b"}, {"transId": "a"}],
    )
    saved: list[dict] = []
    monkeypatch.setattr(podcast_workflow, "save_history", lambda history: saved.append(dict(history)))

    history = {"processed_transcripts": ["old"]}
    added = podcast_workflow.mark_completed_transcripts_processed("cookie", history)

    assert added == 2
    assert history["processed_transcripts"][:2] == ["b", "a"]
    assert "old" in history["processed_transcripts"]
    assert saved

