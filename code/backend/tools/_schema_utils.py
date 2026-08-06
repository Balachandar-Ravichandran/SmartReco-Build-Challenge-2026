"""Shared structured-output normalization used by mesh_client.py."""
from pydantic import BaseModel


def coerce_null_lists(parsed: dict, schema: type[BaseModel]) -> dict:
    """Some models emit `null` instead of `[]` for a list-typed field with
    nothing to put in it, even under strict JSON-schema-constrained decoding
    (observed with Gemini 3.5 Flash Lite via Mesh on SolverOutput.courseTiles,
    and separately on OptionTile.includes NESTED inside pathTiles/courseTiles
    items — a real observed failure: `courseTiles.0.includes` came back null
    and failed validation, sending a perfectly good retrieval result to
    fallback for no reason but this cosmetic null/empty-list ambiguity).
    Normalize null -> [] for any field the schema types as a list, recursing
    into list-of-model fields so nested list fields get the same treatment —
    this only relaxes a null/empty-list ambiguity, never fabricates content."""
    for name, field in schema.model_fields.items():
        if name not in parsed:
            continue
        annotation = field.annotation
        origin = getattr(annotation, "__origin__", None)
        args = getattr(annotation, "__args__", ())

        if parsed[name] is None and (origin is list or annotation is list):
            parsed[name] = []
            continue

        if origin is list and args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            item_schema = args[0]
            if isinstance(parsed[name], list):
                parsed[name] = [
                    coerce_null_lists(item, item_schema) if isinstance(item, dict) else item
                    for item in parsed[name]
                ]
    return parsed
