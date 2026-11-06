"""Validated requests for read-only agent assistance."""

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .schemas import PoiID, PositiveID


_MENTION = re.compile(r"^@TravelSync(?=\s|$)", re.IGNORECASE)


class AgentRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    trigger: Literal["MENTION", "QUICK_ACTION"]

    message: Annotated[str, Field(min_length=1, max_length=2000)]

    idempotency_key: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9_-]+$",
        ),
    ]

    run_id: PositiveID | None = None
    poi_ids: list[PoiID] = Field(default_factory=list, max_length=20)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("poi_ids")
    @classmethod
    def validate_poi_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("poi_ids must not contain blank IDs")
        if len(values) != len(set(values)):
            raise ValueError("poi_ids must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_explicit_trigger(self) -> "AgentRequestInput":
        if self.trigger == "MENTION":
            text = self.message.lstrip()
            match = _MENTION.match(text)

            if match is None:
                raise ValueError(
                    "MENTION requests must start with @TravelSync "
                    "followed by whitespace"
                )

            if not text[match.end():].strip():
                raise ValueError("a request must follow @TravelSync")

        return self
    
    