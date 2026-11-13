"""JSON Schema helpers for tool definitions exposed to models.

Pydantic emits nested models as ``$ref`` pointers into ``$defs``. Some
Anthropic-compatible providers resolve these unreliably (a DeepSeek run
submitted a diagnosis without its nested ``diagnosis`` object), so tool
schemas are sent with every local reference inlined.
"""

from __future__ import annotations

from typing import Any

_DEFS_PREFIX = "#/$defs/"


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Return ``schema`` with local ``#/$defs/...`` references inlined.

    Keywords next to a ``$ref`` (e.g. ``description``) are kept and win over
    the referenced definition. Recursive definitions are rejected because they
    cannot be flattened.
    """
    defs: dict[str, Any] = schema.get("$defs", {})

    def resolve(node: Any, seen: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [resolve(item, seen) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith(_DEFS_PREFIX) or ref[len(_DEFS_PREFIX) :] not in defs:
                raise ValueError(f"unsupported schema reference: {ref}")
            name = ref[len(_DEFS_PREFIX) :]
            if name in seen:
                raise ValueError(f"recursive schema reference: {ref}")
            target = resolve(defs[name], (*seen, name))
            siblings = {k: resolve(v, seen) for k, v in node.items() if k != "$ref"}
            return {**target, **siblings}
        return {k: resolve(v, seen) for k, v in node.items() if k != "$defs"}

    flat: dict[str, Any] = resolve(schema, ())
    return flat
