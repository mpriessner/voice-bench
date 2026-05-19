"""Shared helpers for Gemini adapters (live and text)."""

from google.genai import types


def schema_from_dict(d: dict) -> types.Schema:
    """Recursively convert a JSON Schema dict to a types.Schema object."""
    type_map = {
        "boolean": "BOOLEAN", "string": "STRING", "number": "NUMBER",
        "integer": "INTEGER", "object": "OBJECT", "array": "ARRAY",
    }
    kwargs: dict = {}
    if "type" in d:
        kwargs["type"] = type_map.get(d["type"].lower(), d["type"].upper())
    if "description" in d:
        kwargs["description"] = d["description"]
    if "enum" in d:
        kwargs["enum"] = [str(v) for v in d["enum"]]
    if "properties" in d:
        kwargs["properties"] = {
            k: schema_from_dict(v)
            for k, v in d["properties"].items()
        }
    if "required" in d:
        kwargs["required"] = d["required"]
    if "items" in d:
        kwargs["items"] = schema_from_dict(d["items"])
    return types.Schema(**kwargs)
