"""Provider-facing JSON Schema, generated from the Pydantic model.

Single source of truth
----------------------
The Pydantic model *is* the contract: it validates whatever the model answers, so
it is the only description of the output that can be trusted. Keeping a second,
hand-written schema beside it guarantees eventual drift, and drift here is
silent — both documents look plausible while disagreeing about what the model was
asked to produce. (It already happened: the hand-maintained advocate schema and
``AdvocateOutput`` described the same output in 2,734 and 1,939 characters, and
the hand-maintained judge schema demanded seven fields the model treats as
optional.)

This module derives the provider-facing schema from the model instead, so there
is nothing to keep in sync.

Why the schema is dereferenced
------------------------------
``model_json_schema()`` emits ``$defs`` + ``$ref`` for nested models. That is
valid JSON Schema, but provider dialects disagree about how a reference is
spelled — JSON Schema uses ``$ref``/``$defs``, Gemini's native ``Schema`` uses
``ref``/``defs`` — and some dialects reject references outright. Dereferencing
yields a self-contained document every dialect can express, at the cost of
repeating a nested model once per use. These schemas are small and acyclic, so
inlining is exact and cheap.

Note what is *not* done here: nothing is tightened or loosened. If a model does
not forbid extra properties, the generated schema does not claim it does. The
schema reflects the model, which is the whole point — a stricter claim would be
the same kind of second-document drift this module exists to remove.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Keywords that describe the schema *document* rather than the value it
# constrains. A provider has no use for them, and the native subsets do not list
# them.
_DOCUMENT_KEYWORDS = frozenset({"$schema", "$id", "$comment"})

# Guards against a cyclic ``$ref``. These schemas are acyclic; the bound exists so
# a future recursive model degrades to leaving the reference in place rather than
# recursing until the interpreter gives up.
_MAX_INLINE_DEPTH = 12


def provider_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """The provider-facing JSON schema for ``model``, self-contained.

    Uses serialization aliases, because the provider is asked to produce the
    *serialized* form (``claimId``, not ``claim_id``) — the same form the agent's
    parser validates against.
    """
    raw = model.model_json_schema(by_alias=True, mode="serialization")
    defs = raw.get("$defs") or {}
    inlined = _inline(raw, defs, frozenset(), 0)
    document = {
        key: value for key, value in inlined.items() if key not in _DOCUMENT_KEYWORDS
    }
    # Only drop ``$defs`` when nothing still points at it. If a cyclic reference
    # survived, the definitions are the only thing that makes it resolvable.
    if "$defs" not in document and _contains_ref(document):
        document["$defs"] = {name: _inline(node, defs, frozenset(), 0) for name, node in defs.items()}
    return document


def _inline(node: Any, defs: dict[str, Any], active: frozenset[str], depth: int) -> Any:
    if isinstance(node, list):
        return [_inline(item, defs, active, depth) for item in node]
    if not isinstance(node, dict):
        return node

    reference = node.get("$ref")
    if isinstance(reference, str) and depth < _MAX_INLINE_DEPTH:
        name = reference.rsplit("/", 1)[-1]
        target = defs.get(name)
        if isinstance(target, dict) and name not in active:
            merged = dict(target)
            # Sibling keys beside ``$ref`` are legal and narrow the reference, so
            # they win over the target's own values.
            for key, value in node.items():
                if key != "$ref":
                    merged[key] = value
            return _inline(merged, defs, active | {name}, depth + 1)

    return {
        key: _inline(value, defs, active, depth)
        for key, value in node.items()
        if key != "$defs"
    }


def _contains_ref(node: Any) -> bool:
    if isinstance(node, dict):
        return "$ref" in node or any(_contains_ref(value) for value in node.values())
    if isinstance(node, list):
        return any(_contains_ref(item) for item in node)
    return False
