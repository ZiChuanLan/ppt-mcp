"""Profile catalog for hosted remote MCP."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProfileRecord:
    """Server-side profile definition."""

    profile_id: str
    kind: str
    title: str
    summary: str
    default_pipeline_ids: tuple[str, ...]
    capabilities: tuple[str, ...]
    job_defaults: dict[str, Any]

    def to_public_dict(self) -> dict[str, Any]:
        """Return the MCP-safe public representation."""
        return {
            "profile_id": self.profile_id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "default_pipeline_ids": list(self.default_pipeline_ids),
            "capabilities": list(self.capabilities),
        }

    def resolve_job_defaults(self) -> dict[str, Any]:
        """Resolve env-backed secret references into actual job fields."""
        resolved: dict[str, Any] = {}
        for key, value in self.job_defaults.items():
            if key.endswith("_env"):
                target_key = key[: -len("_env")]
                env_name = str(value).strip()
                resolved[target_key] = os.getenv(env_name, "").strip()
                continue
            resolved[key] = value
        return resolved


class ProfileStore:
    """Load profiles from a JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._profiles = self._load_profiles(path)

    def list_profiles(self) -> list[ProfileRecord]:
        return list(self._profiles.values())

    def get_profile(self, profile_id: str) -> ProfileRecord | None:
        return self._profiles.get(profile_id)

    def _load_profiles(self, path: Path) -> dict[str, ProfileRecord]:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text())
        items = raw.get("profiles", [])
        profiles: dict[str, ProfileRecord] = {}
        for item in items:
            record = ProfileRecord(
                profile_id=str(item["profile_id"]),
                kind=str(item["kind"]),
                title=str(item.get("title") or item["profile_id"]),
                summary=str(item.get("summary") or ""),
                default_pipeline_ids=tuple(item.get("default_pipeline_ids", []) or ()),
                capabilities=tuple(item.get("capabilities", []) or ()),
                job_defaults=dict(item.get("job_defaults", {}) or {}),
            )
            profiles[record.profile_id] = record
        return profiles
