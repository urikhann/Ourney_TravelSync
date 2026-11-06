"""Validated inputs for the read-only agent tools."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TripID = Annotated[str, Field(min_length=1, max_length=36)]
PoiID = Annotated[str, Field(min_length=1, max_length=64)]
PositiveID = Annotated[int, Field(gt=0)]


class ToolInput(BaseModel):
    """Reject unexpected fields and automatic type conversions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    trip_id: TripID

    @field_validator("trip_id")
    @classmethod
    def reject_blank_trip_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trip_id must not be blank")
        return value


class TripContextInput(ToolInput):
    """Read the trip and, optionally, one saved recommendation Run."""

    run_id: PositiveID | None = None


class PoiInformationInput(ToolInput):
    """Read a bounded set of POIs from the appropriate dataset."""

    poi_ids: list[PoiID] = Field(min_length=1, max_length=20)
    run_id: PositiveID | None = None

    @field_validator("poi_ids")
    @classmethod
    def validate_poi_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("poi_ids must not contain blank IDs")
        if len(values) != len(set(values)):
            raise ValueError("poi_ids must not contain duplicates")
        return values


class RecommendationEvidenceInput(PoiInformationInput):
    """Recommendation evidence must identify its saved Run."""

    run_id: PositiveID
    
    
class RunSummaryOutput(BaseModel):
    """Public metadata about a saved recommendation Run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: PositiveID
    created_at: datetime
    model_version: str
    method: str
    dataset_version: str
    feature_schema_version: str


class TripContextOutput(BaseModel):
    """Bounded trip context without member-private inputs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["agent.v1"] = "agent.v1"
    read_only: Literal[True] = True

    trip_id: TripID
    name: str
    city: str
    start_date: date
    end_date: date
    member_count: Annotated[int, Field(ge=1)]

    recommendation: RunSummaryOutput | None


EvidenceStatus = Literal[
    "UNKNOWN",
    "REPORTED",
    "ESTIMATED",
    "UNVERIFIED",
]

class EvidenceBase(BaseModel):
    """How much support the application has for a stored value."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: EvidenceStatus
    note: str
    verified_at: datetime | None = None


class BooleanEvidence(EvidenceBase):
    value: bool | None
    

class IntegerEvidence(EvidenceBase):
    value: int | None

class PoiInformationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    poi_id: PoiID
    name: str
    category: str

    record_source: str
    record_updated_at: datetime

    indoor: BooleanEvidence
    cost_krw: IntegerEvidence
    duration_min: IntegerEvidence


class PoiInformationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["agent.v1"] = "agent.v1"
    read_only: Literal[True] = True

    trip_id: TripID
    run_id: PositiveID | None
    dataset_version: str
    dataset_basis: Literal["RECOMMENDATION_RUN", "ACTIVE_DATASET"]
    currency: Literal["KRW"] = "KRW"

    places: list[PoiInformationItem]
    missing_poi_ids: list[PoiID]


FiniteScore = Annotated[float, Field(allow_inf_nan=False)]

ExclusionCode = Literal[
    "MEMBER_EXCLUSION",
    "MISERY_THRESHOLD",
    "NO_FEATURES",
    "OTHER_RECORDED_EXCLUSION",
]


class RecommendationEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    poi_id: PoiID
    state: Literal["SELECTED", "EXCLUDED", "NOT_RECORDED"]
    source_section: Literal["group", "removed"] | None = None

    group_rank: PositiveID | None = None
    mean_score: FiniteScore | None = None
    selection_gain: FiniteScore | None = None
    group_utility: Annotated[int, Field(ge=0)] | None = None

    exclusion_codes: list[ExclusionCode] = Field(default_factory=list)


class RecommendationEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["agent.v1"] = "agent.v1"
    read_only: Literal[True] = True

    trip_id: TripID
    run: RunSummaryOutput
    score_scale: Literal["0-1", "1-5"] | None
    individual_method: str | None
    scorer_version: str | None

    scope: Literal["RECOMMENDATION_CANDIDATES"] = "RECOMMENDATION_CANDIDATES"
    causal_attribution_available: Literal[False] = False

    items: list[RecommendationEvidenceItem]
    

class AgentContextQuery(BaseModel):
    """HTTP query parameters arrive as text and need parsing."""

    model_config = ConfigDict(extra="forbid")

    run_id: PositiveID | None = None
    poi_ids: list[PoiID] = Field(default_factory=list, max_length=20)

    @field_validator("poi_ids")
    @classmethod
    def validate_poi_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("poi_ids must not contain blank IDs")
        if len(values) != len(set(values)):
            raise ValueError("poi_ids must not contain duplicates")
        return values


class AgentContextOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["agent.v1"] = "agent.v1"
    read_only: Literal[True] = True

    trip_context: TripContextOutput
    poi_information: PoiInformationOutput | None = None
    recommendation_evidence: RecommendationEvidenceOutput | None = None
    
