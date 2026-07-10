"""Config schema helpers for EvalOps plugin authors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FieldSpec:
    """Describes a single configuration field."""

    name: str
    field_type: str = "string"
    description: str = ""
    default: Any = None
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    enum: list[Any] | None = None


class ConfigSchemaBuilder:
    """Build JSON-Schema-compatible config schemas for plugins."""

    def __init__(self) -> None:
        self._fields: list[FieldSpec] = []
        self._title: str = ""
        self._description: str = ""

    @classmethod
    def create(cls, title: str = "", description: str = "") -> ConfigSchemaBuilder:
        builder = cls()
        builder._title = title
        builder._description = description
        return builder

    def string(
        self,
        name: str,
        *,
        description: str = "",
        default: str | None = None,
        required: bool = False,
        enum: list[str] | None = None,
    ) -> ConfigSchemaBuilder:
        self._fields.append(FieldSpec(
            name=name, field_type="string", description=description,
            default=default, required=required, enum=enum,
        ))
        return self

    def number(
        self,
        name: str,
        *,
        description: str = "",
        default: float | None = None,
        required: bool = False,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> ConfigSchemaBuilder:
        self._fields.append(FieldSpec(
            name=name, field_type="number", description=description,
            default=default, required=required, minimum=minimum, maximum=maximum,
        ))
        return self

    def integer(
        self,
        name: str,
        *,
        description: str = "",
        default: int | None = None,
        required: bool = False,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> ConfigSchemaBuilder:
        self._fields.append(FieldSpec(
            name=name, field_type="integer", description=description,
            default=default, required=required,
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
        ))
        return self

    def boolean(
        self,
        name: str,
        *,
        description: str = "",
        default: bool | None = None,
        required: bool = False,
    ) -> ConfigSchemaBuilder:
        self._fields.append(FieldSpec(
            name=name, field_type="boolean", description=description,
            default=default, required=required,
        ))
        return self

    def array(
        self,
        name: str,
        *,
        description: str = "",
        default: list[Any] | None = None,
        required: bool = False,
    ) -> ConfigSchemaBuilder:
        self._fields.append(FieldSpec(
            name=name, field_type="array", description=description,
            default=default, required=required,
        ))
        return self

    def build(self) -> dict[str, Any]:
        """Return a JSON-Schema-compatible dict."""
        properties: dict[str, Any] = {}
        required_fields: list[str] = []

        for f in self._fields:
            prop: dict[str, Any] = {"type": f.field_type}
            if f.description:
                prop["description"] = f.description
            if f.default is not None:
                prop["default"] = f.default
            if f.minimum is not None:
                prop["minimum"] = f.minimum
            if f.maximum is not None:
                prop["maximum"] = f.maximum
            if f.enum is not None:
                prop["enum"] = f.enum
            properties[f.name] = prop
            if f.required:
                required_fields.append(f.name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required_fields:
            schema["required"] = required_fields
        if self._title:
            schema["title"] = self._title
        if self._description:
            schema["description"] = self._description
        return schema
