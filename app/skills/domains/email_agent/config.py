from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EmailSourceRoute:
    route_key: str
    source_mailbox: str
    destination_alias: str


@dataclass(frozen=True)
class EmailCategory:
    key: str
    display_name: str
    audience: str = "shared"
    gmail_label_name: str | None = None


@dataclass(frozen=True)
class EmailAccessGrant:
    user_id: str
    discord_channel_id: str
    external_user_id: str | None
    agent_ids: tuple[str, ...]
    audiences: tuple[str, ...]
    enabled: bool


@dataclass(frozen=True)
class EmailClassificationRule:
    category_key: str
    source_route_keys: tuple[str, ...] = ()
    sender_emails: tuple[str, ...] = ()
    sender_domains: tuple[str, ...] = ()
    subject_contains: tuple[str, ...] = ()
    content_contains: tuple[str, ...] = ()
    list_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmailAgentPermissions:
    gmail_profile: str
    google_account_key: str
    taxonomy_version: str
    source_routes: tuple[EmailSourceRoute, ...]
    categories: tuple[EmailCategory, ...]
    access_grants: tuple[EmailAccessGrant, ...]
    classification_rules: tuple[EmailClassificationRule, ...]

    @classmethod
    def load(cls, path_value: str) -> "EmailAgentPermissions":
        path_text = str(path_value or "").strip()
        if not path_text:
            raise ValueError("Email-agent permissions path is required.")
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"Email-agent permissions file not found: {path_value}")
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("PyYAML is required for email-agent permissions.") from exc
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("Email-agent permissions YAML must be a mapping.")
        return cls.from_mapping(loaded)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "EmailAgentPermissions":
        if int(raw.get("version") or 0) != 1:
            raise ValueError("Email-agent permissions version must be 1.")
        gmail_profile = _email(raw.get("gmail_profile"), field="gmail_profile")
        google_account_key = str(raw.get("google_account_key") or "").strip()
        taxonomy_version = str(raw.get("taxonomy_version") or "").strip()
        if not google_account_key:
            raise ValueError("google_account_key is required.")
        if not taxonomy_version:
            raise ValueError("taxonomy_version is required.")

        route_rows = raw.get("source_routes")
        if not isinstance(route_rows, list) or not route_rows:
            raise ValueError("At least one source route is required.")
        routes: list[EmailSourceRoute] = []
        route_keys: set[str] = set()
        aliases: set[str] = set()
        source_mailboxes: set[str] = set()
        for row in route_rows:
            if not isinstance(row, dict):
                raise ValueError("Every source route must be a mapping.")
            route_key = _key(row.get("route_key"), field="route_key")
            source_mailbox = _email(row.get("source_mailbox"), field="source_mailbox")
            destination_alias = _email(row.get("destination_alias"), field="destination_alias")
            if route_key in route_keys or destination_alias in aliases or source_mailbox in source_mailboxes:
                raise ValueError("Duplicate email route key, source mailbox, or destination alias.")
            if destination_alias.split("@", 1)[1] != gmail_profile.split("@", 1)[1]:
                raise ValueError("Destination aliases must use the configured Gmail profile domain.")
            route_keys.add(route_key)
            aliases.add(destination_alias)
            source_mailboxes.add(source_mailbox)
            routes.append(EmailSourceRoute(route_key, source_mailbox, destination_alias))

        category_rows = raw.get("categories")
        if not isinstance(category_rows, list) or not category_rows:
            raise ValueError("At least one shared email category is required.")
        categories: list[EmailCategory] = []
        category_keys: set[str] = set()
        gmail_label_names: set[str] = set()
        for row in category_rows:
            if not isinstance(row, dict):
                raise ValueError("Every category must be a mapping.")
            key = _key(row.get("key"), field="category key")
            display_name = str(row.get("display_name") or "").strip()
            audience = str(row.get("audience") or "shared").strip().casefold()
            if not display_name:
                raise ValueError("Every email category requires a display_name.")
            if audience != "shared":
                raise ValueError("All initial email categories must use audience=shared.")
            raw_label_name = row.get("gmail_label_name")
            gmail_label_name = (
                str(raw_label_name).strip()
                if raw_label_name is not None
                else (None if key == "spam" else f"Jarvis/{display_name}")
            )
            if gmail_label_name:
                if (
                    not gmail_label_name.casefold().startswith("jarvis/")
                    or len(gmail_label_name) > 225
                    or any(ord(char) < 32 for char in gmail_label_name)
                ):
                    raise ValueError("Managed Gmail labels must use the Jarvis/ namespace and contain no controls.")
                folded_label = gmail_label_name.casefold()
                if folded_label in gmail_label_names:
                    raise ValueError(f"Duplicate managed Gmail label: {gmail_label_name}")
                gmail_label_names.add(folded_label)
            if key in category_keys:
                raise ValueError(f"Duplicate email category: {key}")
            category_keys.add(key)
            categories.append(
                EmailCategory(
                    key=key,
                    display_name=display_name,
                    audience=audience,
                    gmail_label_name=gmail_label_name,
                )
            )
        if "needs_review" not in category_keys:
            raise ValueError("The shared needs_review category is required.")

        access_rows = raw.get("access")
        if not isinstance(access_rows, list) or not access_rows:
            raise ValueError("At least one email access grant is required.")
        grants: list[EmailAccessGrant] = []
        access_pairs: set[tuple[str, str]] = set()
        for row in access_rows:
            if not isinstance(row, dict):
                raise ValueError("Every email access grant must be a mapping.")
            user_id = str(row.get("user_id") or "").strip().casefold()
            channel_id = str(row.get("discord_channel_id") or "").strip()
            enabled = _bool(row.get("enabled"), True)
            if not user_id:
                raise ValueError("Every email access grant requires user_id.")
            if enabled and (not channel_id or not channel_id.isdigit()):
                raise ValueError("Enabled email access grants require a numeric Discord channel ID.")
            external_user_id = str(row.get("external_user_id") or "").strip() or None
            if external_user_id is not None and not external_user_id.isdigit():
                raise ValueError("external_user_id must be a numeric Discord ID when supplied.")
            agent_ids = tuple(
                dict.fromkeys(
                    str(item).strip().casefold()
                    for item in row.get("agent_ids") or ["jarvis"]
                    if str(item).strip()
                )
            )
            audiences = tuple(
                dict.fromkeys(
                    str(item).strip().casefold()
                    for item in row.get("audiences") or ["shared"]
                    if str(item).strip()
                )
            )
            if audiences != ("shared",):
                raise ValueError("Initial email access grants may authorize only the shared audience.")
            pair = (user_id, channel_id)
            if enabled and pair in access_pairs:
                raise ValueError("Duplicate enabled email user/channel grant.")
            if enabled:
                access_pairs.add(pair)
            grants.append(
                EmailAccessGrant(
                    user_id=user_id,
                    discord_channel_id=channel_id,
                    external_user_id=external_user_id,
                    agent_ids=agent_ids or ("jarvis",),
                    audiences=audiences,
                    enabled=enabled,
                )
            )

        rules: list[EmailClassificationRule] = []
        rule_rows = raw.get("classification_rules") or []
        if not isinstance(rule_rows, list):
            raise ValueError("classification_rules must be a list.")
        for row in rule_rows:
            if not isinstance(row, dict):
                raise ValueError("Every classification rule must be a mapping.")
            category_key = _key(row.get("category_key"), field="rule category_key")
            if category_key not in category_keys:
                raise ValueError(f"Classification rule references unknown category: {category_key}")
            source_keys = _tuple_keys(row.get("source_route_keys"))
            if any(item not in route_keys for item in source_keys):
                raise ValueError("Classification rule references an unknown source route.")
            rules.append(
                EmailClassificationRule(
                    category_key=category_key,
                    source_route_keys=source_keys,
                    sender_emails=tuple(_email(item, field="sender email") for item in row.get("sender_emails") or []),
                    sender_domains=tuple(
                        str(item).strip().casefold().lstrip("@")
                        for item in row.get("sender_domains") or []
                        if str(item).strip()
                    ),
                    subject_contains=tuple(
                        str(item).strip().casefold()
                        for item in row.get("subject_contains") or []
                        if str(item).strip()
                    ),
                    content_contains=tuple(
                        str(item).strip().casefold()
                        for item in row.get("content_contains") or []
                        if str(item).strip()
                    ),
                    list_ids=tuple(
                        str(item).strip().casefold()
                        for item in row.get("list_ids") or []
                        if str(item).strip()
                    ),
                )
            )
        return cls(
            gmail_profile=gmail_profile,
            google_account_key=google_account_key,
            taxonomy_version=taxonomy_version,
            source_routes=tuple(routes),
            categories=tuple(categories),
            access_grants=tuple(grants),
            classification_rules=tuple(rules),
        )

    @property
    def category_keys(self) -> frozenset[str]:
        return frozenset(item.key for item in self.categories)

    @property
    def managed_gmail_labels(self) -> dict[str, str]:
        return {
            item.key: item.gmail_label_name
            for item in self.categories
            if item.gmail_label_name
        }

    @property
    def destination_aliases(self) -> tuple[str, ...]:
        return tuple(item.destination_alias for item in self.source_routes)

    def route_for_delivery_addresses(self, addresses: tuple[str, ...]) -> EmailSourceRoute | None:
        matched = {
            route.route_key: route
            for route in self.source_routes
            if route.destination_alias in {str(item).strip().casefold() for item in addresses}
        }
        if len(matched) != 1:
            return None
        return next(iter(matched.values()))

    def authorize(self, context: dict[str, Any]) -> EmailAccessGrant | None:
        if str(context.get("source_interface") or "").strip().casefold() != "discord":
            return None
        if context.get("identity_bound") is not True:
            return None
        user_id = str(context.get("requested_by_user_id") or "").strip().casefold()
        channel_id = str(context.get("discord_channel_id") or "").strip()
        external_user_id = str(context.get("external_user_id") or "").strip()
        agent_id = str(context.get("agent_id") or "jarvis").strip().casefold()
        for grant in self.access_grants:
            if not grant.enabled or grant.user_id != user_id or grant.discord_channel_id != channel_id:
                continue
            if grant.external_user_id and grant.external_user_id != external_user_id:
                continue
            if agent_id not in grant.agent_ids and "all" not in grant.agent_ids:
                continue
            if "shared" not in grant.audiences:
                continue
            return grant
        return None


def _key(value: Any, *, field: str) -> str:
    import re

    result = str(value or "").strip().casefold()
    if not result or not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,63}", result):
        raise ValueError(f"Invalid {field}; use lowercase letters, digits, and underscores.")
    return result


def _email(value: Any, *, field: str) -> str:
    result = str(value or "").strip().casefold()
    if not result or "@" not in result or result.startswith("@") or result.endswith("@"):
        raise ValueError(f"A valid {field} email address is required.")
    return result


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return default


def _tuple_keys(value: Any) -> tuple[str, ...]:
    return tuple(_key(item, field="route key") for item in value or [])
