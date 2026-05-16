from investment_system.common.runtime.obsidian_entity_maintenance import (
    ENTITY_COMPANY,
    ENTITY_INDUSTRY,
    ENTITY_STRATEGY,
    create_info_collector_entity,
    create_investment_notes_entity,
    info_collector_shortcut_specs,
    investment_notes_shortcut_specs,
    list_investment_category_dirs,
    scan_entity,
)


def _make_vaults(tmp_path):
    info_root = tmp_path / "信息收集器"
    investment_root = tmp_path / "投资笔记"
    (info_root / "B公司" / "阿里巴巴").mkdir(parents=True)
    (info_root / "B行业" / "餐饮").mkdir(parents=True)
    for name in ["A1-方法策略", "A2-宏观经济", "B0-社服整体", "B1-电商", "C1-零售业态"]:
        (investment_root / name).mkdir(parents=True)
    (investment_root / "D0-其他行业").mkdir()
    (investment_root / "_overall").mkdir()
    (investment_root / "B1-电商" / "{公司-曹操出行}").mkdir()
    (investment_root / "B0-社服整体" / "{行业-社服整体}").mkdir()
    (investment_root / "A1-方法策略" / "{策略-投资策略}").mkdir()
    return info_root, investment_root


def test_scan_entity_matches_exact_info_collector_and_investment_notes(tmp_path):
    info_root, investment_root = _make_vaults(tmp_path)

    company = scan_entity("阿里巴巴", info_root, investment_root)
    assert [(item.kind, item.path.name) for item in company.info_collector_matches] == [
        (ENTITY_COMPANY, "阿里巴巴")
    ]
    assert company.investment_notes_matches == ()

    industry = scan_entity("餐饮", info_root, investment_root)
    assert [(item.kind, item.path.name) for item in industry.info_collector_matches] == [
        (ENTITY_INDUSTRY, "餐饮")
    ]

    investment_company = scan_entity("曹操出行", info_root, investment_root)
    assert [(item.kind, item.path.name) for item in investment_company.investment_notes_matches] == [
        (ENTITY_COMPANY, "{公司-曹操出行}")
    ]

    investment_industry = scan_entity("社服整体", info_root, investment_root)
    assert [(item.kind, item.path.name) for item in investment_industry.investment_notes_matches] == [
        (ENTITY_INDUSTRY, "{行业-社服整体}")
    ]

    strategy = scan_entity("投资策略", info_root, investment_root)
    assert [(item.kind, item.path.name) for item in strategy.investment_notes_matches] == [
        (ENTITY_STRATEGY, "{策略-投资策略}")
    ]


def test_scan_entity_does_not_treat_partial_name_as_match(tmp_path):
    info_root, investment_root = _make_vaults(tmp_path)

    result = scan_entity("阿里", info_root, investment_root)

    assert result.info_collector_matches == ()
    assert result.investment_notes_matches == ()


def test_list_investment_category_dirs_only_returns_abc_prefixes(tmp_path):
    _, investment_root = _make_vaults(tmp_path)

    names = [path.name for path in list_investment_category_dirs(investment_root)]

    assert names == ["A1-方法策略", "A2-宏观经济", "B0-社服整体", "B1-电商", "C1-零售业态"]


def test_create_info_collector_company_and_industry_templates(tmp_path):
    info_root = tmp_path / "信息收集器"

    company = create_info_collector_entity("美团", ENTITY_COMPANY, info_root)
    assert company.root == info_root / "B公司" / "美团"
    assert (company.root / "公告").is_dir()
    assert (company.root / "纪要").is_dir()
    assert (company.root / "模型").is_dir()
    assert (company.root / "其他").is_dir()
    assert (company.root / "研报").is_dir()
    assert (company.root / "【美团】.md").read_text(encoding="utf-8") == ""

    industry = create_info_collector_entity("跨境电商", ENTITY_INDUSTRY, info_root)
    assert industry.root == info_root / "B行业" / "跨境电商"
    assert not (industry.root / "公告").exists()
    assert (industry.root / "纪要").is_dir()
    assert (industry.root / "模型").is_dir()
    assert (industry.root / "其他").is_dir()
    assert (industry.root / "研报").is_dir()
    assert (industry.root / "【跨境电商】.md").read_text(encoding="utf-8") == ""


def test_create_investment_notes_templates_and_preserves_existing_files(tmp_path):
    category_dir = tmp_path / "投资笔记" / "B1-电商"
    entity_dir = category_dir / "{公司-美团}"
    entity_dir.mkdir(parents=True)
    existing = entity_dir / "美团{投资逻辑页}.md"
    existing.write_text("keep me", encoding="utf-8")

    result = create_investment_notes_entity("美团", ENTITY_COMPANY, category_dir)

    assert result.root == entity_dir
    assert existing.read_text(encoding="utf-8") == "keep me"
    assert (entity_dir / "美团{手写报告页}.md").read_text(encoding="utf-8") == ""
    assert "# 公司基本信息" in (entity_dir / "美团{信息整理页}.md").read_text(encoding="utf-8")
    assert existing in result.skipped_existing

    industry_result = create_investment_notes_entity("互联网电商", ENTITY_INDUSTRY, category_dir)
    industry_dir = category_dir / "{行业-互联网电商}"
    assert industry_result.root == industry_dir
    assert "# 行业宏观分析" in (industry_dir / "互联网电商{信息整理页}.md").read_text(encoding="utf-8")


def test_shortcut_specs_match_existing_obsidian_link_shape(tmp_path):
    shortcut_dir = tmp_path / "新增待添加到utools"
    exe = r"D:\softwares\Obsidian\Obsidian.exe"

    info_specs = info_collector_shortcut_specs("阿里巴巴", ENTITY_COMPANY, shortcut_dir, exe)
    assert len(info_specs) == 1
    assert info_specs[0].path == shortcut_dir / "阿里巴巴【信息搜集】.lnk"
    assert info_specs[0].target_path == exe
    assert info_specs[0].arguments == "obsidian://open?vault=信息收集器&file=B公司\\阿里巴巴\\【阿里巴巴】.md"

    category_dir = tmp_path / "投资笔记" / "B1-电商"
    investment_specs = investment_notes_shortcut_specs("阿里巴巴", ENTITY_COMPANY, category_dir, shortcut_dir, exe)
    assert [spec.path.name for spec in investment_specs] == [
        "阿里巴巴{信息整理页}.lnk",
        "阿里巴巴{手写报告页}.lnk",
        "阿里巴巴{投资逻辑页}.lnk",
    ]
    assert [spec.arguments for spec in investment_specs] == [
        "obsidian://open?vault=投资笔记&file=B1-电商/{公司-阿里巴巴}/阿里巴巴{信息整理页}.md",
        "obsidian://open?vault=投资笔记&file=B1-电商/{公司-阿里巴巴}/阿里巴巴{手写报告页}.md",
        "obsidian://open?vault=投资笔记&file=B1-电商/{公司-阿里巴巴}/阿里巴巴{投资逻辑页}.md",
    ]

