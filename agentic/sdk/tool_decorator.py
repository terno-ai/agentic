"""@tool decorator — convert a plain async function into an agent Tool."""

from __future__ import annotations

import inspect
from typing import Any, Callable, get_type_hints

from agentic.tools.base import Tool, ToolResult


def _py_type_to_json(annotation: Any) -> dict[str, Any]:
    """Map a Python type annotation to a JSON Schema type fragment."""
    origin = getattr(annotation, "__origin__", None)
    if annotation is str or annotation is inspect.Parameter.empty:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is list or origin is list:
        return {"type": "array"}
    if annotation is dict or origin is dict:
        return {"type": "object"}
    return {"type": "string"}


def tool(fn: Callable) -> Tool:
    """Decorator that wraps an async (or sync) function as a Tool.

    The function's name becomes the tool name, its docstring becomes the
    description, and its parameters map to the JSON Schema input_schema.

    Usage::

        @tool
        async def lookup_order(order_id: str) -> str:
            \"\"\"Look up an order by its ID.\"\"\"
            return db.get_order(order_id)

        agent.add_tool(lookup_order)
    """
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        annotation = hints.get(param_name, str)
        prop = _py_type_to_json(annotation)
        prop["description"] = param_name.replace("_", " ")
        properties[param_name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    _name = fn.__name__
    _description = (fn.__doc__ or fn.__name__).strip()
    _schema = {"type": "object", "properties": properties, "required": required}

    class _FunctionTool(Tool):
        name = _name
        description = _description
        input_schema = _schema

        async def execute(self, **kwargs: Any) -> ToolResult:
            try:
                result = fn(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, ToolResult):
                    return result
                return ToolResult.ok(str(result) if result is not None else "")
            except Exception as e:
                return ToolResult.error(f"{_name} error: {e}")

    # Preserve the original function for introspection
    instance = _FunctionTool()
    instance._fn = fn  # type: ignore[attr-defined]
    return instance
