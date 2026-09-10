"""Standardized observation envelope for FirstLight lake and ingestion pipelines."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from pydantic import BaseModel, Field, field_validator


class ObservationEnvelope(BaseModel):
    """Immutable envelope enclosing any raw or preprocessed geospatial observation."""
    geometry: dict[str, Any]
    observed_at: datetime
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str
    source_version: str
    provenance: str = "live"  # "live", "cache:<date>", or "fixture"
    quality: float = Field(default=1.0, ge=0.0, le=1.0)
    payload_ref: dict[str, Any] = Field(default_factory=dict)
    lineage_id: str = ""

    @field_validator("observed_at", "ingested_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    def model_post_init(self, __context: Any) -> None:
        if not self.lineage_id:
            coords = self.geometry.get("coordinates", [])
            obs_iso = self.observed_at.astimezone(timezone.utc).isoformat()
            raw_sig = f"{self.source}|{self.source_version}|{obs_iso}|{coords}|{json.dumps(self.payload_ref, sort_keys=True)}"
            self.lineage_id = hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()

    @property
    def latitude(self) -> float:
        geom_type = self.geometry.get("type", "")
        coords = self.geometry.get("coordinates", [])
        if geom_type == "Point" and len(coords) >= 2:
            return float(coords[1])
        return 0.0

    @property
    def longitude(self) -> float:
        geom_type = self.geometry.get("type", "")
        coords = self.geometry.get("coordinates", [])
        if geom_type == "Point" and len(coords) >= 2:
            return float(coords[0])
        return 0.0

    def to_flat_dict(self) -> dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "source": self.source,
            "source_version": self.source_version,
            "observed_at": self.observed_at,
            "ingested_at": self.ingested_at,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "geometry_type": self.geometry.get("type", "Point"),
            "geometry_json": json.dumps(self.geometry),
            "quality": float(self.quality),
            "payload_json": json.dumps(self.payload_ref),
        }
