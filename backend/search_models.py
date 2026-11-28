"""Validate requests before allocating persistent jobs or browser workers."""

from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


Category = Literal["tops", "coats-jackets", "bottoms", "footwear", "accessories"]
Measurement = Annotated[float, Field(ge=0, le=1000)]
RetailSource = Literal["wildrose", "la-penderie", "peoples-thrift"]


class SearchModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class MeasurementTargets(SearchModel):
    first: Measurement | None = None
    second: Measurement | None = None


class MeasurementBounds(SearchModel):
    min: Measurement | None = None
    max: Measurement | None = None


class SizeRange(MeasurementBounds):
    system: str | None = None


class BottomsMeasurements(SearchModel):
    waist: MeasurementBounds | None = None
    inseam: MeasurementBounds | None = None
    rise: MeasurementBounds | None = None
    inseamRise: MeasurementBounds | None = None
    legOpening: MeasurementBounds | None = None


class SearchRequest(SearchModel):
    searchId: str = Field(default="", max_length=128)
    seller: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9._-]*$")
    retailSource: RetailSource | None = None
    category: Category = "tops"
    groups: Category | list[Category] = "tops"
    gender: Literal["male", "female", ""] = "male"
    measurements: MeasurementTargets | None = None
    p2pTolerance: Measurement = 0.5
    lengthTolerance: Measurement = 1.25
    sizeRange: SizeRange | None = None
    bottomsMeasurements: BottomsMeasurements | None = None
    maxPriceCad: Annotated[float, Field(ge=0, le=10_000)] | None = None
    maxItems: int = Field(default=40, ge=1, le=10000)
    maxLinks: int = Field(default=1000, ge=1, le=10000)
    maxScrolls: int = Field(default=8, ge=0, le=200)
    maxAgeDays: float | None = Field(default=None, gt=0, le=36500)
    parseWorkers: int | None = Field(default=None, ge=1, le=6)
    headless: bool = True
    slowmo: int = Field(default=0, ge=0, le=1000)
    forceRefresh: bool = False

    @field_validator("seller", mode="before")
    @classmethod
    def normalize_seller(cls, value):
        if isinstance(value, str):
            return value.strip().lstrip("@").strip("/").lower()
        return value

    @model_validator(mode="after")
    def validate_retail_search(self):
        if self.retailSource and (self.seller or self.category not in {"tops", "bottoms"}):
            raise ValueError("Retailer searches support tops and bottoms without a Depop seller")
        return self


def validate_search_payload(payload) -> dict:
    try:
        return SearchRequest.model_validate(payload).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc
