from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping


_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}\.[a-z][a-z0-9_]{0,63}$")
_SKILL_ID_RE = re.compile(r"^skill\.[a-z][a-z0-9_.-]{1,95}$")
_OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_POLICY_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_LONG_ID_RE = re.compile(r"\b[0-9]{15,22}\b")
_ABSOLUTE_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:home|Users|etc|mnt|opt)/)[^\s]+")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"\b(?:api[_-]?key|password|secret|credential|access[_-]?token)\s*[:=]\s*[^\s,;]+",
    flags=re.IGNORECASE,
)
_FORBIDDEN_SCHEMA_FIELD_PARTS = frozenset(
    {
        "api_key",
        "password",
        "secret",
        "credential",
        "access_token",
        "execution_ref",
        "storage_ref",
        "provider_setting",
        "principal",
        "authorization",
        "user_id",
        "agent_id",
        "channel_id",
        "guild_id",
    }
)

_EFFECTS = frozenset(
    {
        "read",
        "local_write",
        "external_write",
        "destructive_local",
        "destructive_external",
        "outbound_communication",
        "privileged",
    }
)
_APPROVAL_RULES = frozenset({"none", "conditional", "always", "denied"})
_APPROVAL_CONDITIONS = frozenset(
    {
        "external_recipients_present",
        "cross_domain_no_store_transfer",
    }
)
_SENSITIVITIES = frozenset(
    {"normal", "private", "financial", "identity", "highly_restricted"}
)
_PERSISTENCE_POLICIES = frozenset({"standard", "redacted", "no_store"})
_IDEMPOTENCY_POLICIES = frozenset({"not_applicable", "required"})
_EFFECT_CARDINALITIES = frozenset({"single", "atomic_batch", "independent_batch"})
_TRANSFER_SCOPES = frozenset({"same_domain", "cross_domain"})
_RUNTIME_DEPENDENCIES = frozenset(
    {"action_approval", "ticket_review", "document_processing", "email_operations"}
)
_EMAIL_UNORDERED_TARGET_TOOLS = frozenset(
    {
        "email.apply_managed_category_label",
        "email.mark_read_complete",
        "email.move_to_spam",
    }
)

_MAX_SCHEMA_BYTES = 32_768
_MAX_DESCRIPTOR_BYTES = 65_536
_MAX_SCHEMA_DEPTH = 8
_MAX_PROPERTIES = 64
_MAX_ARRAY_ITEMS = 256
_MAX_STRING_CHARS = 8_000
_MAX_LEGACY_INTENTS = 32
_MAX_TRANSFER_FIELDS = 64
_MAX_RUNTIME_DEPENDENCIES = 4


class ToolContractError(ValueError):
    """A content-free validation failure for a typed tool contract."""

    def __init__(self, code: str) -> None:
        normalized = str(code or "invalid_tool_contract").strip().casefold()
        super().__init__(normalized)
        self.code = normalized


class ToolArgumentCanonicalizationError(ToolContractError):
    """A bounded domain resolver could not produce stable authorized arguments."""


class FrozenDict(dict[str, Any]):
    """A JSON-serializable dict whose mutation methods fail closed."""

    __slots__ = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FrozenDict:
        instance = cls()
        for key, item in value.items():
            dict.__setitem__(instance, key, _freeze_json(item))
        return instance

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("frozen JSON objects are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable

    def __copy__(self) -> FrozenDict:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> FrozenDict:
        return self


def _freeze_json(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ToolContractError("json_object_key_invalid")
            frozen[key] = _freeze_json(item)
        return FrozenDict.from_mapping(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolContractError("json_number_non_finite")
        return value
    raise ToolContractError("json_type_unsupported")


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw_json(item) for item in value]
    return value


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ToolContractError("json_object_key_invalid")
            normalized[unicodedata.normalize("NFC", key)] = _canonical_json_value(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolContractError("json_number_non_finite")
        return value
    raise ToolContractError("json_type_unsupported")


def canonical_json(value: Any) -> str:
    normalized = _canonical_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _closed_schema(schema: Any, *, path: str = "$", depth: int = 0) -> FrozenDict:
    if depth > _MAX_SCHEMA_DEPTH:
        raise ToolContractError("schema_depth_exceeded")
    if not isinstance(schema, Mapping):
        raise ToolContractError("schema_not_object")
    if any(not isinstance(key, str) for key in schema):
        raise ToolContractError("schema_key_invalid")

    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise ToolContractError("schema_type_invalid")
    common_keys = {"type", "description", "enum", "const"}
    type_keys: dict[str, set[str]] = {
        "object": {"properties", "required", "additionalProperties", "minProperties", "maxProperties"},
        "array": {"items", "minItems", "maxItems", "uniqueItems"},
        "string": {"minLength", "maxLength", "format"},
        "integer": {"minimum", "maximum"},
        "number": {"minimum", "maximum"},
        "boolean": set(),
        "null": set(),
    }
    if set(schema) - common_keys - type_keys[str(schema_type)]:
        raise ToolContractError("schema_keyword_unsupported")

    description = schema.get("description")
    if description is not None and (
        not isinstance(description, str) or not description.strip() or len(description) > 500
    ):
        raise ToolContractError("schema_description_invalid")

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum or len(enum) > 64:
            raise ToolContractError("schema_enum_invalid")
        serialized = {canonical_json(item) for item in enum}
        if len(serialized) != len(enum):
            raise ToolContractError("schema_enum_duplicate")

    normalized: dict[str, Any] = dict(schema)
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, Mapping) or len(properties) > _MAX_PROPERTIES:
            raise ToolContractError("schema_properties_invalid")
        if schema.get("additionalProperties") is not False:
            raise ToolContractError("schema_not_closed")
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ToolContractError("schema_required_invalid")
        if len(required) != len(set(required)) or not set(required).issubset(properties):
            raise ToolContractError("schema_required_invalid")
        min_properties = schema.get("minProperties", 0)
        max_properties = schema.get("maxProperties", len(properties))
        if (
            not isinstance(min_properties, int)
            or isinstance(min_properties, bool)
            or not isinstance(max_properties, int)
            or isinstance(max_properties, bool)
            or min_properties < 0
            or max_properties < min_properties
            or max_properties > len(properties)
        ):
            raise ToolContractError("schema_property_bounds_invalid")
        normalized["properties"] = {
            str(name): _closed_schema(child, path=f"{path}/{name}", depth=depth + 1)
            for name, child in properties.items()
            if isinstance(name, str) and name
        }
        if len(normalized["properties"]) != len(properties):
            raise ToolContractError("schema_property_name_invalid")
        for property_name in normalized["properties"]:
            lowered_name = property_name.casefold()
            if any(part in lowered_name for part in _FORBIDDEN_SCHEMA_FIELD_PARTS):
                raise ToolContractError("schema_protected_field_invalid")
        normalized["required"] = list(required)
    elif schema_type == "array":
        if "items" not in schema:
            raise ToolContractError("schema_array_items_missing")
        minimum = schema.get("minItems", 0)
        maximum = schema.get("maxItems")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or minimum < 0
            or maximum < max(1, minimum)
            or maximum > _MAX_ARRAY_ITEMS
        ):
            raise ToolContractError("schema_array_bounds_invalid")
        if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
            raise ToolContractError("schema_unique_items_invalid")
        normalized["items"] = _closed_schema(schema["items"], path=f"{path}/*", depth=depth + 1)
    elif schema_type == "string":
        minimum = schema.get("minLength", 0)
        maximum = schema.get("maxLength")
        if enum is not None and maximum is None:
            maximum = max(len(str(item)) for item in enum)
            normalized["maxLength"] = maximum
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or minimum < 0
            or maximum < max(1, minimum)
            or maximum > _MAX_STRING_CHARS
        ):
            raise ToolContractError("schema_string_bounds_invalid")
        if schema.get("format") not in {None, "date-time", "date", "uri"}:
            raise ToolContractError("schema_string_format_invalid")
    elif schema_type in {"integer", "number"}:
        if enum is None and ("minimum" not in schema or "maximum" not in schema):
            raise ToolContractError("schema_number_bounds_missing")
        if "minimum" in schema and "maximum" in schema:
            minimum = schema["minimum"]
            maximum = schema["maximum"]
            if (
                isinstance(minimum, bool)
                or isinstance(maximum, bool)
                or not isinstance(minimum, (int, float))
                or not isinstance(maximum, (int, float))
                or not math.isfinite(float(minimum))
                or not math.isfinite(float(maximum))
                or minimum > maximum
            ):
                raise ToolContractError("schema_number_bounds_invalid")

    frozen = FrozenDict.from_mapping(normalized)
    if len(canonical_json(frozen).encode("utf-8")) > _MAX_SCHEMA_BYTES:
        raise ToolContractError("schema_size_exceeded")
    return frozen


def _validate_scalar_enum(schema: Mapping[str, Any], value: Any) -> None:
    if "const" in schema and canonical_json(value) != canonical_json(schema["const"]):
        raise ToolContractError("arguments_const_mismatch")
    if "enum" in schema:
        allowed = {canonical_json(item) for item in schema["enum"]}
        if canonical_json(value) not in allowed:
            raise ToolContractError("arguments_enum_mismatch")


def _normalize_datetime(value: str) -> str:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ToolContractError("arguments_datetime_invalid") from exc
    if parsed.tzinfo is None:
        raise ToolContractError("arguments_datetime_timezone_missing")
    normalized = parsed.astimezone(UTC).isoformat(timespec="seconds")
    return f"{normalized[:-6]}Z" if normalized.endswith("+00:00") else normalized


def _normalize_value(schema: Mapping[str, Any], value: Any, *, path: str = "$") -> Any:
    schema_type = schema["type"]
    if schema_type == "object":
        if not isinstance(value, Mapping):
            raise ToolContractError("arguments_type_mismatch")
        properties = schema["properties"]
        if any(not isinstance(key, str) for key in value):
            raise ToolContractError("arguments_object_key_invalid")
        extras = set(value) - set(properties)
        if extras:
            raise ToolContractError("arguments_unknown_field")
        missing = set(schema["required"]) - set(value)
        if missing:
            raise ToolContractError("arguments_required_field_missing")
        if len(value) < int(schema.get("minProperties", 0)) or len(value) > int(
            schema.get("maxProperties", len(properties))
        ):
            raise ToolContractError("arguments_property_count_invalid")
        result = {
            key: _normalize_value(properties[key], item, path=f"{path}/{key}")
            for key, item in value.items()
        }
        _validate_scalar_enum(schema, result)
        return FrozenDict.from_mapping(result)
    if schema_type == "array":
        if not isinstance(value, (list, tuple)):
            raise ToolContractError("arguments_type_mismatch")
        if len(value) < int(schema.get("minItems", 0)) or len(value) > int(schema["maxItems"]):
            raise ToolContractError("arguments_array_size_invalid")
        result = tuple(
            _normalize_value(schema["items"], item, path=f"{path}/{index}")
            for index, item in enumerate(value)
        )
        if schema.get("uniqueItems") and len({canonical_json(item) for item in result}) != len(result):
            raise ToolContractError("arguments_array_duplicate")
        _validate_scalar_enum(schema, result)
        return result
    if schema_type == "string":
        if not isinstance(value, str):
            raise ToolContractError("arguments_type_mismatch")
        normalized = unicodedata.normalize("NFC", value)
        if schema.get("format") == "date-time":
            normalized = _normalize_datetime(normalized)
        elif schema.get("format") == "date":
            try:
                normalized = date.fromisoformat(normalized).isoformat()
            except ValueError as exc:
                raise ToolContractError("arguments_date_invalid") from exc
        if len(normalized) < int(schema.get("minLength", 0)) or len(normalized) > int(
            schema["maxLength"]
        ):
            raise ToolContractError("arguments_string_size_invalid")
        _validate_scalar_enum(schema, normalized)
        return normalized
    if schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolContractError("arguments_type_mismatch")
    elif schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise ToolContractError("arguments_type_mismatch")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise ToolContractError("arguments_type_mismatch")
    elif schema_type == "null":
        if value is not None:
            raise ToolContractError("arguments_type_mismatch")
    if schema_type in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolContractError("arguments_number_out_of_range")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolContractError("arguments_number_out_of_range")
    _validate_scalar_enum(schema, value)
    return value


def _decode_pointer(pattern: str) -> tuple[str, ...]:
    if not isinstance(pattern, str) or not pattern.startswith("/") or pattern in {"", "/"}:
        raise ToolContractError("transfer_pattern_invalid")
    if pattern.startswith("#") or pattern.endswith("/-") or "**" in pattern:
        raise ToolContractError("transfer_pattern_invalid")
    decoded: list[str] = []
    for raw in pattern[1:].split("/"):
        index = 0
        output: list[str] = []
        while index < len(raw):
            if raw[index] != "~":
                output.append(raw[index])
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                raise ToolContractError("transfer_pattern_escape_invalid")
            output.append("~" if raw[index + 1] == "0" else "/")
            index += 2
        segment = "".join(output)
        if segment in {"", "-", "**"}:
            raise ToolContractError("transfer_pattern_segment_invalid")
        decoded.append(segment)
    return tuple(decoded)


def _schema_at_pointer(schema: Mapping[str, Any], segments: tuple[str, ...]) -> Mapping[str, Any]:
    current = schema
    for segment in segments:
        schema_type = current.get("type")
        if schema_type == "object":
            if segment == "*" or segment not in current["properties"]:
                raise ToolContractError("transfer_pattern_schema_mismatch")
            current = current["properties"][segment]
        elif schema_type == "array":
            if segment == "*":
                if int(current["maxItems"]) < 1:
                    raise ToolContractError("transfer_pattern_zero_match")
            elif not segment.isdigit() or int(segment) >= int(current["maxItems"]):
                raise ToolContractError("transfer_pattern_array_index_invalid")
            current = current["items"]
        else:
            raise ToolContractError("transfer_pattern_schema_mismatch")
    return current


def _segments_compatible(left: str, right: str) -> bool:
    return left == right or (left == "*" and right.isdigit()) or (right == "*" and left.isdigit())


def _patterns_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    for left_segment, right_segment in zip(left, right, strict=False):
        if not _segments_compatible(left_segment, right_segment):
            return False
    return True


@dataclass(frozen=True, slots=True)
class TransferableObservationField:
    pattern: str
    scope: str

    @classmethod
    def from_value(
        cls,
        value: Mapping[str, Any],
        *,
        observation_schema: Mapping[str, Any],
    ) -> TransferableObservationField:
        if not isinstance(value, Mapping) or set(value) != {"pattern", "scope"}:
            raise ToolContractError("transfer_field_shape_invalid")
        pattern = str(value.get("pattern") or "")
        scope = str(value.get("scope") or "").strip().casefold()
        if scope not in _TRANSFER_SCOPES:
            raise ToolContractError("transfer_scope_invalid")
        segments = _decode_pointer(pattern)
        _schema_at_pointer(observation_schema, segments)
        return cls(pattern=pattern, scope=scope)

    def to_dict(self) -> dict[str, str]:
        return {"pattern": self.pattern, "scope": self.scope}


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_id: str
    skill_id: str
    contract_version: int
    purpose: str
    input_schema: FrozenDict
    observation_schema: FrozenDict
    effect: str
    approval_rule: str
    approval_conditions: tuple[str, ...]
    sensitivity: str
    persistence: str
    idempotency: str
    effect_cardinality: str
    transferable_observation_fields: tuple[TransferableObservationField, ...]
    runtime_dependencies: tuple[str, ...]
    timeout_seconds: int
    max_result_items: int
    max_observation_chars: int
    legacy_intents: tuple[str, ...]
    interactive: bool

    def __post_init__(self) -> None:
        tool_id = str(self.tool_id or "").strip().casefold()
        skill_id = str(self.skill_id or "").strip().casefold()
        if not _TOOL_ID_RE.fullmatch(tool_id):
            raise ToolContractError("tool_id_invalid")
        if not _SKILL_ID_RE.fullmatch(skill_id):
            raise ToolContractError("skill_id_invalid")
        if not isinstance(self.contract_version, int) or isinstance(self.contract_version, bool) or not (
            1 <= self.contract_version <= 65_535
        ):
            raise ToolContractError("contract_version_invalid")
        purpose = re.sub(r"\s+", " ", str(self.purpose or "").strip())
        if not purpose or len(purpose) > 500:
            raise ToolContractError("purpose_invalid")
        if self.effect not in _EFFECTS:
            raise ToolContractError("effect_invalid")
        if self.approval_rule not in _APPROVAL_RULES:
            raise ToolContractError("approval_rule_invalid")
        conditions = tuple(str(item or "").strip().casefold() for item in self.approval_conditions)
        if len(conditions) != len(set(conditions)) or any(
            not _POLICY_ID_RE.fullmatch(item) or item not in _APPROVAL_CONDITIONS for item in conditions
        ):
            raise ToolContractError("approval_condition_invalid")
        if (self.approval_rule == "conditional") != bool(conditions):
            raise ToolContractError("approval_condition_mismatch")
        if self.sensitivity not in _SENSITIVITIES:
            raise ToolContractError("sensitivity_invalid")
        if self.persistence not in _PERSISTENCE_POLICIES:
            raise ToolContractError("persistence_invalid")
        if self.idempotency not in _IDEMPOTENCY_POLICIES:
            raise ToolContractError("idempotency_invalid")
        if self.effect_cardinality not in _EFFECT_CARDINALITIES:
            raise ToolContractError("effect_cardinality_invalid")
        if not isinstance(self.interactive, bool):
            raise ToolContractError("interactive_invalid")

        input_schema = _closed_schema(thaw_json(self.input_schema))
        observation_schema = _closed_schema(thaw_json(self.observation_schema))
        if input_schema.get("type") != "object" or observation_schema.get("type") != "object":
            raise ToolContractError("schema_root_not_object")

        raw_transfer_fields: Iterable[Any] = self.transferable_observation_fields
        transfer_fields: list[TransferableObservationField] = []
        transfer_segments: list[tuple[str, ...]] = []
        for raw in raw_transfer_fields:
            field = (
                raw
                if isinstance(raw, TransferableObservationField)
                else TransferableObservationField.from_value(
                    raw,
                    observation_schema=observation_schema,
                )
            )
            segments = _decode_pointer(field.pattern)
            _schema_at_pointer(observation_schema, segments)
            if any(_patterns_overlap(segments, previous) for previous in transfer_segments):
                raise ToolContractError("transfer_pattern_overlap")
            transfer_fields.append(field)
            transfer_segments.append(segments)
        if len(transfer_fields) > _MAX_TRANSFER_FIELDS:
            raise ToolContractError("transfer_field_count_exceeded")

        dependencies = tuple(str(item or "").strip().casefold() for item in self.runtime_dependencies)
        if (
            len(dependencies) > _MAX_RUNTIME_DEPENDENCIES
            or len(dependencies) != len(set(dependencies))
            or any(item not in _RUNTIME_DEPENDENCIES for item in dependencies)
        ):
            raise ToolContractError("runtime_dependency_invalid")
        legacy_intents = tuple(str(item or "").strip().casefold() for item in self.legacy_intents)
        if (
            len(legacy_intents) > _MAX_LEGACY_INTENTS
            or len(legacy_intents) != len(set(legacy_intents))
            or any(not _TOOL_ID_RE.fullmatch(item) for item in legacy_intents)
        ):
            raise ToolContractError("legacy_intent_invalid")
        for name, value, maximum in (
            ("timeout_seconds", self.timeout_seconds, 120),
            ("max_result_items", self.max_result_items, _MAX_ARRAY_ITEMS),
            ("max_observation_chars", self.max_observation_chars, _MAX_STRING_CHARS),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise ToolContractError(f"{name}_invalid")

        object.__setattr__(self, "tool_id", tool_id)
        object.__setattr__(self, "skill_id", skill_id)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "input_schema", input_schema)
        object.__setattr__(self, "observation_schema", observation_schema)
        object.__setattr__(self, "approval_conditions", conditions)
        object.__setattr__(self, "transferable_observation_fields", tuple(transfer_fields))
        object.__setattr__(self, "runtime_dependencies", dependencies)
        object.__setattr__(self, "legacy_intents", legacy_intents)
        if len(canonical_json(self.to_storage_dict()).encode("utf-8")) > _MAX_DESCRIPTOR_BYTES:
            raise ToolContractError("descriptor_size_exceeded")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, skill_id: str | None = None) -> ToolDescriptor:
        required = {
            "tool_id",
            "skill_id",
            "contract_version",
            "purpose",
            "input_schema",
            "observation_schema",
            "effect",
            "approval_rule",
            "approval_conditions",
            "sensitivity",
            "persistence",
            "idempotency",
            "effect_cardinality",
            "transferable_observation_fields",
            "runtime_dependencies",
            "timeout_seconds",
            "max_result_items",
            "max_observation_chars",
            "legacy_intents",
            "interactive",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ToolContractError("descriptor_shape_invalid")
        declared_skill_id = str(value.get("skill_id") or "").strip().casefold()
        if skill_id is not None and declared_skill_id != str(skill_id).strip().casefold():
            raise ToolContractError("descriptor_skill_owner_mismatch")
        for tuple_field in (
            "approval_conditions",
            "transferable_observation_fields",
            "runtime_dependencies",
            "legacy_intents",
        ):
            if not isinstance(value.get(tuple_field), (list, tuple)):
                raise ToolContractError(f"descriptor_{tuple_field}_invalid")
        return cls(
            tool_id=str(value["tool_id"]),
            skill_id=declared_skill_id,
            contract_version=value["contract_version"],
            purpose=str(value["purpose"]),
            input_schema=_freeze_json(value["input_schema"]),
            observation_schema=_freeze_json(value["observation_schema"]),
            effect=str(value["effect"]).strip().casefold(),
            approval_rule=str(value["approval_rule"]).strip().casefold(),
            approval_conditions=tuple(value["approval_conditions"]),
            sensitivity=str(value["sensitivity"]).strip().casefold(),
            persistence=str(value["persistence"]).strip().casefold(),
            idempotency=str(value["idempotency"]).strip().casefold(),
            effect_cardinality=str(value["effect_cardinality"]).strip().casefold(),
            transferable_observation_fields=tuple(value["transferable_observation_fields"]),
            runtime_dependencies=tuple(value["runtime_dependencies"]),
            timeout_seconds=value["timeout_seconds"],
            max_result_items=value["max_result_items"],
            max_observation_chars=value["max_observation_chars"],
            legacy_intents=tuple(value["legacy_intents"]),
            interactive=value["interactive"],
        )

    def validate_arguments(self, arguments: Mapping[str, Any]) -> FrozenDict:
        normalized = _normalize_value(self.input_schema, arguments)
        if not isinstance(normalized, FrozenDict):
            raise ToolContractError("arguments_root_not_object")
        return normalized

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "skill_id": self.skill_id,
            "contract_version": self.contract_version,
            "purpose": self.purpose,
            "input_schema": thaw_json(self.input_schema),
            "observation_schema": thaw_json(self.observation_schema),
            "effect": self.effect,
            "approval_rule": self.approval_rule,
            "approval_conditions": list(self.approval_conditions),
            "sensitivity": self.sensitivity,
            "persistence": self.persistence,
            "idempotency": self.idempotency,
            "effect_cardinality": self.effect_cardinality,
            "transferable_observation_fields": [
                item.to_dict() for item in self.transferable_observation_fields
            ],
            "runtime_dependencies": list(self.runtime_dependencies),
            "timeout_seconds": self.timeout_seconds,
            "max_result_items": self.max_result_items,
            "max_observation_chars": self.max_observation_chars,
            "legacy_intents": list(self.legacy_intents),
            "interactive": self.interactive,
        }

    def to_model_projection(self, *, availability_note: str) -> dict[str, Any]:
        note = sanitize_model_text(availability_note, max_chars=240)
        return {
            "tool_id": self.tool_id,
            "purpose": sanitize_model_text(self.purpose, max_chars=500),
            "input_schema": _model_safe_schema(self.input_schema),
            "output_shape": _model_safe_schema(self.observation_schema),
            "effect": self.effect,
            "approval": {
                "rule": self.approval_rule,
                "required": self.approval_rule in {"always", "conditional"},
            },
            "availability": note or "Available in the current request context.",
            "transferable_observation_fields": [
                item.to_dict() for item in self.transferable_observation_fields
            ],
            "limits": {
                "timeout_seconds": self.timeout_seconds,
                "max_result_items": self.max_result_items,
                "max_observation_chars": self.max_observation_chars,
            },
        }


def compile_tool_descriptors(
    *,
    skill_id: str,
    contract_version: Any,
    declarations: Any,
) -> tuple[tuple[ToolDescriptor, ...], tuple[dict[str, str], ...]]:
    if declarations is None:
        return (), ()
    if not isinstance(contract_version, int) or isinstance(contract_version, bool) or contract_version != 1:
        return (), ({"code": "main_tools_contract_version_invalid", "tool_id": ""},)
    if not isinstance(declarations, list):
        return (), ({"code": "main_tools_not_list", "tool_id": ""},)
    descriptors: list[ToolDescriptor] = []
    diagnostics: list[dict[str, str]] = []
    seen: set[str] = set()
    for declaration in declarations[:128]:
        tool_id = ""
        if isinstance(declaration, Mapping):
            tool_id = str(declaration.get("tool_id") or "").strip().casefold()
            declaration = {**dict(declaration), "skill_id": str(skill_id).strip().casefold()}
        try:
            descriptor = ToolDescriptor.from_mapping(declaration, skill_id=skill_id)
            if descriptor.tool_id in seen:
                raise ToolContractError("duplicate_tool_id")
        except ToolContractError as exc:
            diagnostics.append(
                {
                    "code": exc.code,
                    "tool_id": tool_id if _TOOL_ID_RE.fullmatch(tool_id) else "",
                }
            )
            continue
        descriptors.append(descriptor)
        seen.add(descriptor.tool_id)
    if len(declarations) > 128:
        diagnostics.append({"code": "main_tools_count_exceeded", "tool_id": ""})
    return tuple(descriptors), tuple(diagnostics)


def sanitize_model_text(value: Any, *, max_chars: int) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip())
    normalized = _SENSITIVE_ASSIGNMENT_RE.sub("[protected-credential]", normalized)
    normalized = _LONG_ID_RE.sub("[protected-id]", normalized)
    normalized = _ABSOLUTE_PATH_RE.sub("[protected-path]", normalized)
    return normalized[: max(0, int(max_chars))]


def _model_safe_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "description":
            continue
        if key == "properties" and isinstance(value, Mapping):
            safe[key] = {
                str(name): _model_safe_schema(child)
                for name, child in value.items()
                if isinstance(child, Mapping)
            }
        elif key == "items" and isinstance(value, Mapping):
            safe[key] = _model_safe_schema(value)
        else:
            safe[key] = thaw_json(value)
    return safe


def normalize_identity_arguments(tool_id: str, arguments: Mapping[str, Any]) -> FrozenDict:
    normalized = thaw_json(_freeze_json(arguments))
    normalized_tool_id = str(tool_id or "").strip().casefold()
    if normalized_tool_id in _EMAIL_UNORDERED_TARGET_TOOLS:
        targets = normalized.get("message_refs")
        if not isinstance(targets, list) or not targets:
            raise ToolContractError("email_message_refs_invalid")
        normalized_targets = [str(item or "").strip() for item in targets]
        if any(not item or len(item) > 255 for item in normalized_targets):
            raise ToolContractError("email_message_ref_invalid")
        if len(normalized_targets) != len(set(normalized_targets)):
            raise ToolContractError("email_message_ref_duplicate")
        normalized["message_refs"] = sorted(normalized_targets)
    return _freeze_json(normalized)


def canonical_arguments_hash(arguments: Mapping[str, Any], *, tool_id: str) -> str:
    normalized = normalize_identity_arguments(tool_id, arguments)
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def tool_operation_id(
    *,
    root_request_id: str,
    tool_id: str,
    contract_version: int,
    call_ordinal: int,
    arguments: Mapping[str, Any],
) -> tuple[str, str, FrozenDict]:
    root = str(root_request_id or "").strip()
    normalized_tool_id = str(tool_id or "").strip().casefold()
    if not root or len(root) > 255 or "\n" in root:
        raise ToolContractError("root_request_id_invalid")
    if not _TOOL_ID_RE.fullmatch(normalized_tool_id):
        raise ToolContractError("tool_id_invalid")
    if not isinstance(contract_version, int) or isinstance(contract_version, bool) or contract_version < 1:
        raise ToolContractError("contract_version_invalid")
    if not isinstance(call_ordinal, int) or isinstance(call_ordinal, bool) or call_ordinal < 1:
        raise ToolContractError("call_ordinal_invalid")
    normalized = normalize_identity_arguments(normalized_tool_id, arguments)
    arguments_hash = hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()
    material = (
        f"{root}\n{normalized_tool_id}\n{contract_version}\n{call_ordinal}\n{arguments_hash}"
    )
    operation_id = "toolop_v1_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    return operation_id, arguments_hash, normalized


def tool_child_operation_id(
    *,
    operation_id: str,
    child_index: int,
    canonical_target_ref: str,
    child_arguments: Mapping[str, Any],
) -> tuple[str, str]:
    parent = str(operation_id or "").strip()
    target = str(canonical_target_ref or "").strip()
    if not re.fullmatch(r"toolop_v1_[0-9a-f]{64}", parent):
        raise ToolContractError("parent_operation_id_invalid")
    if not isinstance(child_index, int) or isinstance(child_index, bool) or child_index < 1:
        raise ToolContractError("child_index_invalid")
    if not _OPAQUE_REF_RE.fullmatch(target) or "\n" in target:
        raise ToolContractError("canonical_target_ref_invalid")
    child_arguments_hash = hashlib.sha256(
        canonical_json(_freeze_json(child_arguments)).encode("utf-8")
    ).hexdigest()
    material = f"{parent}\n{child_index}\n{target}\n{child_arguments_hash}"
    child_id = "toolchild_v1_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    return child_id, child_arguments_hash


@dataclass(frozen=True, slots=True)
class ToolCallEnvelope:
    root_request_id: str
    operation_id: str
    call_ordinal: int
    session_id: str
    principal_kind: str
    principal_subject: str
    user_id: str
    agent_id: str
    source_interface: str
    channel_scope: str
    skill_id: str
    tool_id: str
    contract_version: int
    authorization_snapshot_ref: str
    arguments_hash: str
    arguments: FrozenDict

    def __post_init__(self) -> None:
        for name in (
            "root_request_id",
            "session_id",
            "principal_kind",
            "principal_subject",
            "user_id",
            "agent_id",
            "source_interface",
            "channel_scope",
            "authorization_snapshot_ref",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value or len(value) > 255 or "\n" in value:
                raise ToolContractError(f"envelope_{name}_invalid")
            object.__setattr__(self, name, value)
        if not _SKILL_ID_RE.fullmatch(str(self.skill_id or "").strip().casefold()):
            raise ToolContractError("skill_id_invalid")
        object.__setattr__(self, "skill_id", str(self.skill_id).strip().casefold())
        operation_id, arguments_hash, normalized = tool_operation_id(
            root_request_id=self.root_request_id,
            tool_id=self.tool_id,
            contract_version=self.contract_version,
            call_ordinal=self.call_ordinal,
            arguments=self.arguments,
        )
        if self.operation_id != operation_id or self.arguments_hash != arguments_hash:
            raise ToolContractError("envelope_operation_identity_mismatch")
        object.__setattr__(self, "tool_id", str(self.tool_id).strip().casefold())
        object.__setattr__(self, "arguments", normalized)

    @classmethod
    def create(
        cls,
        *,
        root_request_id: str,
        call_ordinal: int,
        session_id: str,
        principal_kind: str,
        principal_subject: str,
        user_id: str,
        agent_id: str,
        source_interface: str,
        channel_scope: str,
        skill_id: str,
        descriptor: ToolDescriptor,
        authorization_snapshot_ref: str,
        validated_arguments: Mapping[str, Any],
    ) -> ToolCallEnvelope:
        operation_id, arguments_hash, normalized = tool_operation_id(
            root_request_id=root_request_id,
            tool_id=descriptor.tool_id,
            contract_version=descriptor.contract_version,
            call_ordinal=call_ordinal,
            arguments=validated_arguments,
        )
        return cls(
            root_request_id=root_request_id,
            operation_id=operation_id,
            call_ordinal=call_ordinal,
            session_id=session_id,
            principal_kind=principal_kind,
            principal_subject=principal_subject,
            user_id=user_id,
            agent_id=agent_id,
            source_interface=source_interface,
            channel_scope=channel_scope,
            skill_id=skill_id,
            tool_id=descriptor.tool_id,
            contract_version=descriptor.contract_version,
            authorization_snapshot_ref=authorization_snapshot_ref,
            arguments_hash=arguments_hash,
            arguments=normalized,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_request_id": self.root_request_id,
            "operation_id": self.operation_id,
            "call_ordinal": self.call_ordinal,
            "session_id": self.session_id,
            "principal_kind": self.principal_kind,
            "principal_subject": self.principal_subject,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "source_interface": self.source_interface,
            "channel_scope": self.channel_scope,
            "skill_id": self.skill_id,
            "tool_id": self.tool_id,
            "contract_version": self.contract_version,
            "authorization_snapshot_ref": self.authorization_snapshot_ref,
            "arguments_hash": self.arguments_hash,
            "arguments": thaw_json(self.arguments),
        }
