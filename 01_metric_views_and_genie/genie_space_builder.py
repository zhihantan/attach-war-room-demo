#!/usr/bin/env python3
"""
Genie Space Builder

A small helper for creating and round-tripping Databricks Genie
`serialized_space` payloads.

The helper intentionally models only the stable, high-value collections
needed for authoring workflows. If a fetched space includes additional
fields, load it with `from_json()` and this builder will preserve the
unknown fields while you patch the known ones.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Iterable, Optional, Sequence
from uuid import uuid4


class GenieSpaceBuilder:
    """Builder class for common Genie `serialized_space` authoring tasks."""

    TABLES_PATH = ("data_sources", "tables")
    SAMPLE_QUESTIONS_PATH = ("config", "sample_questions")
    TEXT_INSTRUCTIONS_PATH = ("instructions", "text_instructions")
    EXAMPLE_SQL_PATH = ("instructions", "example_question_sqls")
    SQL_FUNCTIONS_PATH = ("instructions", "sql_functions")
    JOIN_SPECS_PATH = ("instructions", "join_specs")
    SNIPPET_FILTERS_PATH = ("instructions", "sql_snippets", "filters")
    SNIPPET_EXPRESSIONS_PATH = ("instructions", "sql_snippets", "expressions")
    SNIPPET_MEASURES_PATH = ("instructions", "sql_snippets", "measures")
    BENCHMARKS_PATH = ("benchmarks", "questions")

    ID_LIST_PATHS = (
        SAMPLE_QUESTIONS_PATH,
        TEXT_INSTRUCTIONS_PATH,
        EXAMPLE_SQL_PATH,
        SQL_FUNCTIONS_PATH,
        JOIN_SPECS_PATH,
        SNIPPET_FILTERS_PATH,
        SNIPPET_EXPRESSIONS_PATH,
        SNIPPET_MEASURES_PATH,
        BENCHMARKS_PATH,
    )

    def __init__(
        self,
        title: str = "New Genie Space",
        description: str = "",
        warehouse_id: Optional[str] = None,
        space: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.title = title
        self.description = description
        self.warehouse_id = warehouse_id or ""
        self._space: Dict[str, Any] = copy.deepcopy(space) if space is not None else {}
        self._space.setdefault("version", 2)

    @classmethod
    def from_json(
        cls,
        serialized_space: Any,
        title: Optional[str] = None,
        description: Optional[str] = None,
        warehouse_id: Optional[str] = None,
    ) -> "GenieSpaceBuilder":
        """Load a builder from a serialized space string or API response dict."""
        raw_title = title
        raw_description = description
        raw_warehouse_id = warehouse_id

        if isinstance(serialized_space, dict):
            if "serialized_space" in serialized_space:
                raw_title = raw_title or serialized_space.get("title")
                raw_description = raw_description or serialized_space.get("description")
                raw_warehouse_id = raw_warehouse_id or serialized_space.get("warehouse_id")
                serialized_space = serialized_space["serialized_space"]
            else:
                data = copy.deepcopy(serialized_space)
                return cls(
                    title=raw_title or "New Genie Space",
                    description=raw_description or "",
                    warehouse_id=raw_warehouse_id,
                    space=data,
                )

        if isinstance(serialized_space, str):
            data = json.loads(serialized_space)
        else:
            raise TypeError("serialized_space must be a JSON string or dict")

        return cls(
            title=raw_title or "New Genie Space",
            description=raw_description or "",
            warehouse_id=raw_warehouse_id,
            space=data,
        )

    def set_title(self, title: str) -> "GenieSpaceBuilder":
        self.title = title
        return self

    def set_description(self, description: str) -> "GenieSpaceBuilder":
        self.description = description
        return self

    def set_instructions(
        self,
        instructions: str,
        item_id: Optional[Any] = None,
        replace: bool = True,
        **extra: Any,
    ) -> str:
        """Replace or append a text instruction entry."""
        entry = {"id": self._resolve_id(item_id), "content": self._as_text_list(instructions)}
        entry.update(extra)

        if replace:
            self._set_list(self.TEXT_INSTRUCTIONS_PATH, [entry])
        else:
            self._ensure_list(self.TEXT_INSTRUCTIONS_PATH).append(entry)
        return entry["id"]

    def set_warehouse(self, warehouse_id: str) -> str:
        """Set the outer API warehouse_id used when creating or updating the space."""
        self.warehouse_id = warehouse_id
        return self.warehouse_id

    def add_table(
        self,
        table_identifier: str,
        **extra: Any,
    ) -> str:
        return self._add_relation(table_identifier, **extra)

    def add_view(
        self,
        view_identifier: str,
        **extra: Any,
    ) -> str:
        """Add a view as a curated relation."""
        return self._add_relation(view_identifier, **extra)

    def add_metric_view(
        self,
        metric_view_identifier: str,
        **extra: Any,
    ) -> str:
        """Add a metric view as a curated relation."""
        return self._add_relation(metric_view_identifier, **extra)

    def add_join_spec(
        self,
        join_spec: Dict[str, Any],
        item_id: Optional[Any] = None,
        **extra: Any,
    ) -> str:
        """Add a raw join spec while preserving the surrounding exported shape."""
        entry = copy.deepcopy(join_spec)
        if "id" in entry:
            entry["id"] = self._resolve_id(entry["id"])
            if item_id is not None and entry["id"] != self._resolve_id(item_id):
                raise ValueError("item_id does not match join_spec['id']")
        else:
            entry["id"] = self._resolve_id(item_id)

        entry.update(extra)
        self._ensure_list(self.JOIN_SPECS_PATH).append(entry)
        return entry["id"]

    def add_function(
        self,
        function_identifier: str,
        item_id: Optional[Any] = None,
        **extra: Any,
    ) -> str:
        entry = {
            "id": self._resolve_id(item_id),
            "identifier": function_identifier,
        }
        entry.update(extra)
        self._ensure_list(self.SQL_FUNCTIONS_PATH).append(entry)
        return entry["id"]

    def add_example_sql(
        self,
        title: str,
        sql: str,
        description: str = "",
        item_id: Optional[Any] = None,
        **extra: Any,
    ) -> str:
        """Add an example question + SQL pair."""
        entry = {
            "id": self._resolve_id(item_id),
            "question": self._as_text_list(title),
            "sql": self._as_line_list(sql),
        }
        if description:
            entry["usage_guidance"] = self._as_text_list(description)
        entry.update(extra)
        self._ensure_list(self.EXAMPLE_SQL_PATH).append(entry)
        return entry["id"]

    def add_benchmark(
        self,
        name: str,
        expected_response: str = "",
        item_id: Optional[Any] = None,
        response_format: str = "TEXT",
        **extra: Any,
    ) -> str:
        """Add a benchmark question entry."""
        entry = {"id": self._resolve_id(item_id), "question": self._as_text_list(name)}
        if expected_response:
            entry["answer"] = [
                {
                    "format": response_format,
                    "content": self._as_line_list(expected_response),
                }
            ]
        entry.update(extra)
        self._ensure_list(self.BENCHMARKS_PATH).append(entry)
        return entry["id"]

    def to_dict(self) -> Dict[str, Any]:
        """Return a normalized deep copy of the serialized space."""
        normalized = copy.deepcopy(self._space)
        normalized.setdefault("version", 2)

        tables = self._get_path(normalized, self.TABLES_PATH)
        if isinstance(tables, list):
            tables.sort(key=lambda item: item.get("identifier", "") if isinstance(item, dict) else "")

        return normalized

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def validate(self) -> bool:
        """Validate the modeled payload against common Genie invariants."""
        errors = []
        normalized = self.to_dict()

        if not isinstance(self.title, str) or not self.title.strip():
            errors.append("title must be a non-empty string")

        if not isinstance(self.warehouse_id, str) or not self.warehouse_id.strip():
            errors.append("warehouse_id must be a non-empty string")

        if normalized.get("version") != 2:
            errors.append("serialized_space version must be 2")

        tables = self._get_path(normalized, self.TABLES_PATH)
        if tables is None:
            errors.append("serialized_space must include data_sources.tables")
        elif not isinstance(tables, list):
            errors.append("data_sources.tables must be a list")
        else:
            identifiers = []
            for index, item in enumerate(tables):
                if not isinstance(item, dict):
                    errors.append(f"data_sources.tables[{index}] must be an object")
                    continue
                identifier = item.get("identifier")
                if not isinstance(identifier, str) or not identifier.strip():
                    errors.append(f"data_sources.tables[{index}] must include a non-empty identifier")
                    continue
                identifiers.append(identifier)

            if not identifiers:
                errors.append("serialized_space must include at least one curated object in data_sources.tables")
            elif identifiers != sorted(identifiers):
                errors.append("data_sources.tables must be sorted by identifier")
            elif len(identifiers) != len(set(identifiers)):
                errors.append("data_sources.tables contains duplicate identifier values")

        sql_snippets = self._get_path(normalized, ("instructions", "sql_snippets"))
        if sql_snippets is not None and not isinstance(sql_snippets, dict):
            errors.append("instructions.sql_snippets must be an object")

        for path in self.ID_LIST_PATHS:
            items = self._get_path(normalized, path)
            if items is None:
                continue
            if not isinstance(items, list):
                errors.append(f"{self._path_name(path)} must be a list")
                continue

            ids = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    errors.append(f"{self._path_name(path)}[{index}] must be an object")
                    continue
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id.strip():
                    errors.append(f"{self._path_name(path)}[{index}] must have a non-empty string id")
                    continue
                ids.append(item_id)

            if len(ids) != len(set(ids)):
                errors.append(f"{self._path_name(path)} ids must be unique")

        functions = self._get_path(normalized, self.SQL_FUNCTIONS_PATH)
        if isinstance(functions, list):
            self._validate_unique_identifier(
                functions,
                "identifier",
                "instructions.sql_functions",
                errors,
            )

        if errors:
            raise ValueError("Invalid Genie space:\n- " + "\n- ".join(errors))

        return True

    def _add_relation(self, identifier: str, **extra: Any) -> str:
        entry = {"identifier": identifier}
        entry.update(extra)
        tables = self._ensure_list(self.TABLES_PATH)
        tables.append(entry)
        tables.sort(key=lambda item: item.get("identifier", "") if isinstance(item, dict) else "")
        return identifier

    def _ensure_dict(self, path: Sequence[str]) -> Dict[str, Any]:
        current = self._space
        for index, key in enumerate(path):
            existing = current.get(key)
            if existing is None:
                current[key] = {}
                existing = current[key]
            elif not isinstance(existing, dict):
                raise ValueError(f"{self._path_name(path[: index + 1])} must be an object")
            current = existing
        return current

    def _ensure_list(self, path: Sequence[str]) -> list[Dict[str, Any]]:
        parent = self._ensure_dict(path[:-1])
        existing = parent.get(path[-1])
        if existing is None:
            parent[path[-1]] = []
            existing = parent[path[-1]]
        elif not isinstance(existing, list):
            raise ValueError(f"{self._path_name(path)} must be a list")
        return existing

    def _set_list(self, path: Sequence[str], items: list[Dict[str, Any]]) -> None:
        parent = self._ensure_dict(path[:-1])
        parent[path[-1]] = items

    @staticmethod
    def _get_path(data: Dict[str, Any], path: Sequence[str]) -> Any:
        current: Any = data
        for key in path:
            if not isinstance(current, dict):
                return None
            if key not in current:
                return None
            current = current[key]
        return current

    @staticmethod
    def _as_text_list(value: Any) -> list[str]:
        if isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise TypeError("text collections must contain only strings")
            return copy.deepcopy(value)
        if isinstance(value, str):
            return [value]
        raise TypeError("text collections must be a string or list of strings")

    @staticmethod
    def _as_line_list(value: Any) -> list[str]:
        if isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise TypeError("line collections must contain only strings")
            return copy.deepcopy(value)
        if isinstance(value, str):
            return value.splitlines(keepends=True) or [value]
        raise TypeError("line collections must be a string or list of strings")

    @staticmethod
    def _resolve_id(item_id: Optional[Any]) -> str:
        if item_id is None:
            return uuid4().hex
        if isinstance(item_id, int):
            if item_id == 0:
                raise ValueError("item_id must be non-zero")
            return str(item_id)
        if isinstance(item_id, str) and item_id.strip():
            return item_id
        raise ValueError("item_id must be a non-empty string, non-zero integer, or None")

    @staticmethod
    def _path_name(path: Iterable[str]) -> str:
        return ".".join(path)

    @staticmethod
    def _validate_unique_identifier(
        items: Iterable[Dict[str, Any]],
        key: str,
        collection: str,
        errors: list[str],
    ) -> None:
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                errors.append(f"{collection} entries must be objects")
                continue
            identifier = item.get(key)
            if not isinstance(identifier, str) or not identifier.strip():
                errors.append(f"{collection} entries must include {key}")
                continue
            if identifier in seen:
                errors.append(f"{collection} contains duplicate {key}: {identifier}")
                continue
            seen.add(identifier)
