"""Strict structured-output schema compatibility (Meta ``json_schema`` strict mode).

The Meta API rejects (HTTP 400, observed live) any schema whose
``required`` array omits a key present in ``properties``. Pydantic's
``model_json_schema`` does exactly that for defaulted fields, and also
emits ``$defs``/``$ref`` references, ``anyOf`` null-unions, and
constraining keywords outside the known-good subset used by the
hand-written schemas.

This module converts Pydantic models into strict-safe wire schemas and
validates any production schema offline:

Wire contract (every node, recursively):

- object nodes: ``required`` lists every key of ``properties``;
  ``additionalProperties`` is ``False``.
- no ``$ref``/``$defs``/``anyOf``/``oneOf``/``allOf``/``not``/
  ``pattern``/``format``; nested models are inlined and fixed
  vocabularies use ``enum``.
- ``type`` is a scalar (object, string, integer, number, boolean) or a
  ``["<scalar>", "null"]`` nullable pair.
- keyword allowlist: type, enum, properties, required,
  additionalProperties, items.

Range/length keywords (minimum, maxLength, ...) are intentionally
absent from the wire: the Pydantic models remain the enforcing
validator for every response, so validation is not weakened.
"""
from __future__ import annotations

import copy

_WIRE_KEYWORDS = frozenset({
    "type", "enum", "properties", "required", "additionalProperties", "items",
})
_WIRE_TYPES = frozenset({"object", "string", "integer", "number", "boolean", "array"})
_SCALAR_TYPES = frozenset({"object", "string", "integer", "number", "boolean"})
_REF_PREFIX = "#/$defs/"


def _resolve_ref(node: dict, defs: dict, seen: frozenset) -> tuple[dict, frozenset]:
    """Inline ``$ref`` nodes against ``defs`` (cycle-safe).

    Returns (node, seen) with every resolved name added to ``seen`` so
    callers can thread it through property/item recursion: a model that
    (transitively) contains itself raises the documented ValueError
    instead of recursing forever."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith(_REF_PREFIX):
            raise ValueError(f"unsupported $ref target: {ref!r}")
        name = ref[len(_REF_PREFIX):]
        if name in seen:
            raise ValueError(f"cyclic $ref: {ref!r}")
        if name not in defs:
            raise ValueError(f"dangling $ref: {ref!r}")
        seen = seen | {name}
        node = copy.deepcopy(defs[name])
    return node, seen


def _nullable(node: dict, defs: dict, seen: frozenset) -> dict | None:
    """``anyOf: [T, null]`` as a ``[t, "null"]`` type pair, else None."""
    options = node.get("anyOf")
    if not isinstance(options, list) or len(options) != 2:
        return None
    types = []
    scalar_option: dict | None = None
    for option in options:
        if not isinstance(option, dict):
            return None
        option, _seen = _resolve_ref(option, defs, seen)
        if option.get("type") not in _WIRE_TYPES | {"null"}:
            return None
        types.append(option["type"])
        if option.get("type") != "null":
            scalar_option = option
    if "null" not in types:
        return None
    scalar = types[0] if types[1] == "null" else types[1]
    if scalar == "null":
        return None
    merged: dict = {"type": [scalar, "null"]}
    # Preserve the scalar branch's constraining keys (enum most
    # importantly): dropping them would let any string through the
    # wire schema and weaken the strict contract.
    if scalar_option is not None:
        for key in ("enum", "properties", "items"):
            if key in scalar_option:
                merged[key] = _convert(scalar_option[key], defs, seen) \
                    if key != "enum" else list(scalar_option[key])
        if "properties" in merged:
            merged["required"] = sorted(merged["properties"])
            merged["additionalProperties"] = False
    return merged


def _convert(node, defs: dict, seen: frozenset):
    if isinstance(node, list):
        return [_convert(item, defs, seen) for item in node]
    if not isinstance(node, dict):
        return node
    node, seen = _resolve_ref(node, defs, seen)
    if "anyOf" in node:
        merged = _nullable(node, defs, seen)
        if merged is None:
            raise ValueError(f"unsupported anyOf (only [T, null] unions): {node!r}")
        return merged
    converted: dict = {}
    node_type = node.get("type")
    if isinstance(node_type, list):
        if len(node_type) != 2 or node_type[1] != "null" \
                or node_type[0] not in _SCALAR_TYPES:
            raise ValueError(f"unsupported type array: {node_type!r}")
        converted["type"] = list(node_type)
    elif node_type is not None:
        if node_type not in _WIRE_TYPES:
            raise ValueError(f"unsupported type: {node_type!r}")
        converted["type"] = node_type
    if "enum" in node:
        values = node["enum"]
        if not isinstance(values, list) or not values \
                or any(not isinstance(v, (str, int, float, bool)) for v in values):
            raise ValueError(f"unsupported enum: {values!r}")
        converted["enum"] = list(values)
    if "properties" in node:
        props = node["properties"]
        if not isinstance(props, dict):
            raise ValueError("properties must be an object")
        converted["properties"] = {
            key: _convert(value, defs, seen) for key, value in props.items()
        }
        converted["required"] = sorted(converted["properties"])
        converted["additionalProperties"] = False
    if "items" in node:
        converted["items"] = _convert(node["items"], defs, seen)
    return converted


def to_strict_schema(model) -> dict:
    """Strict-safe wire schema for a Pydantic model class.

    Raises ValueError for constructs with no strict representation
    (non-null unions, dangling/cyclic references, exotic types), so an
    incompatible model fails at import time, never as a live HTTP 400.
    The returned schema always passes ``assert_strict_schema``.
    """
    raw = copy.deepcopy(model.model_json_schema())
    defs = raw.pop("$defs", {})
    strict = _convert(raw, defs, frozenset())
    assert_strict_schema(strict, getattr(model, "__name__", "schema"))
    return strict


def strict_violations(schema, path: str = "root") -> list[str]:
    """Every strict-mode violation in ``schema`` (empty means compliant)."""
    problems: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: schema node must be an object"]
    unknown = [key for key in schema if key not in _WIRE_KEYWORDS]
    if unknown:
        problems.append(f"{path}: unsupported keywords: {sorted(unknown)}")
    node_type = schema.get("type")
    if isinstance(node_type, list):
        if len(node_type) != 2 or node_type[1] != "null" \
                or node_type[0] not in _SCALAR_TYPES:
            problems.append(f"{path}: bad nullable type: {node_type!r}")
    elif node_type is not None and node_type not in _WIRE_TYPES:
        problems.append(f"{path}: bad type: {node_type!r}")
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            problems.append(f"{path}: bad enum: {values!r}")
    if "properties" in schema:
        props = schema["properties"]
        if not isinstance(props, dict):
            problems.append(f"{path}: properties must be an object")
        else:
            required = schema.get("required")
            if not isinstance(required, list) or set(required) != set(props):
                missing = sorted(set(props) - set(required or []))
                problems.append(
                    f"{path}: required must list every property key; missing: {missing}")
            if schema.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            for key, value in props.items():
                problems.extend(strict_violations(value, f"{path}.{key}"))
    if "items" in schema:
        problems.extend(strict_violations(schema["items"], f"{path}[]"))
    return problems


def assert_strict_schema(schema, name: str = "schema") -> None:
    """Raise ValueError listing every strict-mode violation found."""
    problems = strict_violations(schema)
    if problems:
        raise ValueError(f"strict-mode violations in {name}: {'; '.join(problems)}")
