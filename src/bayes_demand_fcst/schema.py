"""Versioned structural schemas shared across pipeline packages."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldSchema:
    """Describe one persisted dataset field without binding to a storage engine."""

    name: str
    logical_type: str
    nullable: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Field names cannot be empty")
        if not self.logical_type:
            raise ValueError("Field logical types cannot be empty")


@dataclass(frozen=True, slots=True)
class DatasetSchema:
    """A versioned column and key contract for a persisted dataset."""

    name: str
    version: int
    fields: tuple[FieldSchema, ...]
    primary_key: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Dataset names cannot be empty")
        if self.version < 1:
            raise ValueError("Dataset schema versions must be positive")
        if not self.fields:
            raise ValueError("Dataset schemas require at least one field")

        field_names = tuple(field.name for field in self.fields)
        if len(set(field_names)) != len(field_names):
            raise ValueError(f"Dataset schema {self.name} contains duplicate fields")

        unknown_keys = set(self.primary_key) - set(field_names)
        if unknown_keys:
            names = ", ".join(sorted(unknown_keys))
            raise ValueError(f"Dataset schema {self.name} has unknown primary-key fields: {names}")

    @property
    def required_columns(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)

    def validate_columns(self, columns: Iterable[str]) -> None:
        available = set(columns)
        missing = tuple(column for column in self.required_columns if column not in available)
        if missing:
            raise ValueError(
                f"Dataset schema {self.name} v{self.version} is missing columns: {', '.join(missing)}"
            )
