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

