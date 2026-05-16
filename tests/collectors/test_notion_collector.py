from investment_system.collectors.notion.notion_collector import build_notion_text_property
from investment_system.collectors.notion.notion_link_collector import (
    build_failure_properties,
    build_pending_filter,
    build_stored_property,
    escape_table_cell,
)


def test_build_notion_text_property_for_title_column():
    payload = build_notion_text_property("标题", "title", "一篇微信文章")

    assert payload == {
        "标题": {
            "title": [
                {
                    "text": {
                        "content": "一篇微信文章"
                    }
                }
            ]
        }
    }


def test_build_notion_text_property_ignores_unsupported_type():
    assert build_notion_text_property("标题", "select", "一篇微信文章") == {}


def test_build_stored_property_supports_select_and_checkbox():
    assert build_stored_property("是否已存到本地", "select") == {
        "是否已存到本地": {
            "select": {
                "name": "已存到本地"
            }
        }
    }
    assert build_stored_property("是否已存到本地", "checkbox") == {
        "是否已存到本地": {
            "checkbox": True
        }
    }


def test_build_pending_filter_for_link_collection_select():
    assert build_pending_filter("是否已存到本地", "select") == {
        "and": [
            {
                "property": "是否已存到本地",
                "select": {
                    "does_not_equal": "已存到本地"
                }
            },
            {
                "property": "是否已存到本地",
                "select": {
                    "does_not_equal": "3次存到本地失败"
                }
            }
        ]
    }


def test_build_failure_properties_marks_third_failure():
    assert build_failure_properties("是否已存到本地", "select", "存本地失败次数", 3) == {
        "存本地失败次数": {
            "number": 3
        },
        "是否已存到本地": {
            "select": {
                "name": "3次存到本地失败"
            }
        }
    }


def test_escape_table_cell_keeps_obsidian_table_valid():
    assert escape_table_cell("a|b\nc") == "a\\|b<br>c"

