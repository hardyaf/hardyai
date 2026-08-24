from app.context.reference_resolver import ReferenceResolver
from app.context.types import EntityRegistry, TrackedEntity


def test_reference_resolver_resolves_deictic_list_and_switch():
    resolver = ReferenceResolver()
    registry = EntityRegistry(
        entities=[
            TrackedEntity(
                domain="lists",
                entity_type="list",
                display_name="groceries",
                aliases=["grocery list"],
                salience=0.9,
            ),
            TrackedEntity(
                domain="home",
                entity_type="switch",
                display_name="kitchen light",
                aliases=["kitchen lamp"],
                salience=0.88,
            ),
        ],
    )

    list_hit = resolver.resolve_reference(
        value="that list",
        registry=registry,
        domain="lists",
        entity_type="list",
        deictic_only=True,
    )
    assert list_hit is not None
    assert list_hit.entity.display_name == "groceries"
    assert list_hit.reason == "deictic_reference"

    switch_hit = resolver.resolve_reference(
        value="that one",
        registry=registry,
        domain="home",
        entity_type="switch",
        deictic_only=True,
    )
    assert switch_hit is not None
    assert switch_hit.entity.display_name == "kitchen light"


def test_reference_resolver_can_match_explicit_alias():
    resolver = ReferenceResolver()
    registry = EntityRegistry(
        entities=[
            TrackedEntity(
                domain="lists",
                entity_type="list",
                display_name="costco",
                aliases=["costco list", "shopping run"],
                salience=0.8,
            ),
        ],
        alias_map={"shopping run": "costco"},
    )
    hit = resolver.resolve_reference(
        value="shopping run",
        registry=registry,
        domain="lists",
        entity_type="list",
        deictic_only=False,
    )
    assert hit is not None
    assert hit.entity.display_name == "costco"

