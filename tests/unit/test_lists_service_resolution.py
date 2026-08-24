from app.tools.lists_service import ListsService


def test_resolves_my_grocery_list_to_existing_groceries():
    service = ListsService()
    service.create_list("groceries")
    first = service.add_item("groceries", "milk")
    assert first["list_name"] == "groceries"

    second = service.add_item("my grocery list", "tofu")
    assert second["list_name"] == "groceries"
    assert second["matched_existing"] is True
    assert second["count"] == 2


def test_add_item_does_not_create_unknown_list_implicitly():
    service = ListsService()
    result = service.add_item("the grocery list", "tofu")

    assert result["status"] == "unknown_list"
    assert result["resolved_list_name"] == "groceries"
    assert result["matched_existing"] is False


def test_get_items_resolves_alias_to_existing_list():
    service = ListsService()
    service.create_list("groceries")
    service.add_item("groceries", "tofu")

    result = service.get_items("my grocery list")
    assert result["status"] == "ok"
    assert result["list_name"] == "groceries"
    assert result["items"] == ["tofu"]


def test_create_list_requires_explicit_name_not_pronoun():
    service = ListsService()
    result = service.create_list("it")

    assert result["status"] == "needs_input"
    assert result["missing_fields"] == ["list_name"]


def test_get_items_resolves_compact_and_spaced_variants_of_same_list_name():
    service = ListsService()
    service.create_list("easterprep")
    service.add_item("easterprep", "buy eggs")

    result = service.get_items("easter prep list")
    assert result["status"] == "ok"
    assert result["list_name"] == "easterprep"
    assert result["items"] == ["buy eggs"]


def test_add_item_returns_unknown_list_with_high_confidence_suggestion_for_partial_name():
    service = ListsService()
    service.create_list("easterprep")

    result = service.add_item("easter list", "pick up dog poop")

    assert result["status"] == "unknown_list"
    assert result["matched_existing"] is False
    assert result["suggested_list"] == "easterprep"
    assert result["suggestions"][0] == "easterprep"
    assert float(result["suggestion_confidence"]) >= 0.6


def test_remove_item_uses_fuzzy_item_matching():
    service = ListsService()
    service.create_list("costco")
    service.add_item("costco", "apples")
    service.add_item("costco", "tofu")

    result = service.remove_item("costco", "apple")

    assert result["status"] == "ok"
    assert result["item_text"] == "apples"
    assert result["items"] == ["tofu"]


def test_mark_item_done_requires_completion_mode_then_marks_done():
    service = ListsService()
    service.create_list("costco")
    service.add_item("costco", "granola")

    first = service.mark_item_done(list_name="costco", item_text="granola", completion_mode=None)
    assert first["status"] == "needs_input"
    assert first["missing_fields"] == ["completion_mode"]

    second = service.mark_item_done(list_name="costco", item_text="granola", completion_mode="done")
    assert second["status"] == "ok"
    assert second["completion_mode"] == "done"
    entries = second.get("item_entries")
    assert isinstance(entries, list)
    assert any(bool(entry.get("checked")) for entry in entries)


def test_delete_list_returns_unknown_with_fuzzy_suggestion_when_name_is_partial():
    service = ListsService()
    service.create_list("easterprep")

    result = service.delete_list("easter list")

    assert result["status"] == "unknown_list"
    assert result["suggested_list"] == "easterprep"
