from app.core.conversation_routing import ConversationLanePolicy
from app.core.types import Intent


def test_unknown_information_questions_route_to_conversation():
    policy = ConversationLanePolicy()

    for text in (
        "can you tell me a recipe for pancakes",
        "What is the best lord of the rings movie",
        "How do I stop beetles from eating my fruit tree?",
        "Search the web for the official SearXNG search API documentation",
    ):
        decision = policy.decide(text=text, intent=Intent.UNKNOWN)
        assert decision.route_to_conversation is True


def test_unknown_direct_action_still_routes_to_semantic_repair():
    policy = ConversationLanePolicy()

    decision = policy.decide(
        text="set the house heat to 68 degrees",
        intent=Intent.UNKNOWN,
    )

    assert decision.route_to_conversation is False


def test_unknown_pronoun_action_still_routes_to_semantic_repair():
    policy = ConversationLanePolicy()

    decision = policy.decide(
        text="Can you turn it off",
        intent=Intent.UNKNOWN,
    )

    assert decision.route_to_conversation is False


def test_resolved_contextual_followup_routes_to_conversation():
    policy = ConversationLanePolicy()

    decision = policy.decide(
        text="do things get stuck in it during a fight",
        intent=Intent.UNKNOWN,
        contextual_followup={"resolved": True, "active_topic": "male lion mane"},
    )

    assert decision.route_to_conversation is True
