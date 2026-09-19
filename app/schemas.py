# -*- coding: utf-8 -*-
"""
The Unified Request Envelope and API contracts.

The envelope is the platform's interoperability surface. Any channel -- web
form, voice, WhatsApp, SMS gateway, a state government's existing grievance
system -- produces this one shape, and everything downstream consumes it. It is
deliberately small, free of country-specific fields, and carries the AI's own
uncertainty alongside its conclusions so that consumers can decide how much to
trust any single record.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field

Channel = Literal["web", "voice", "whatsapp", "sms", "ivr", "api", "field_worker"]
Status = Literal["new", "review", "verified", "rejected"]
Urgency = Literal["critical", "high", "medium", "low"]


class RequestIn(BaseModel):
    """What a citizen (or an integrating system) submits."""
    text: str = Field(min_length=1, max_length=4000,
                      description="The request in the citizen's own language")
    country: str = Field(default="IN", description="ISO-3166-1 alpha-2 country code")
    district_code: str = Field(description="District code from the country pack")
    channel: Channel = "web"
    language: Optional[str] = Field(default=None,
                                    description="ISO-639-1. Omit to auto-detect.")
    contact: Optional[str] = Field(default=None, max_length=120,
                                   description="Optional. Stored redacted; never displayed.")


class RequestOut(BaseModel):
    """What the platform returns. The AI's reasoning travels with the record --
    a classification without its rationale is not auditable."""
    id: str
    created_at: str
    country: str
    region_code: str
    region_name: str
    district_code: str
    district_name: str
    channel: Channel
    language: str
    language_confidence: float
    text_original: str
    text_redacted: str
    text_en: str
    sector: str
    sector_confidence: float
    urgency: Urgency
    urgency_score: float
    affected_population: int
    ai_confidence: float
    ai_engine: str
    ai_rationale: str
    pii_types: list[str]
    entities: list[str]
    status: Status
    reviewer_note: Optional[str] = None


class ReviewIn(BaseModel):
    """A human correcting or confirming the AI. Every field is optional except
    the decision, so a reviewer can accept a classification without restating it."""
    status: Status
    sector: Optional[str] = None
    urgency: Optional[Urgency] = None
    affected_population: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=1000)
    reviewer: str = Field(default="anonymous", max_length=80)


class WeightsIn(BaseModel):
    """Prioritisation weights. Exposed so the policy is tunable and visible
    rather than buried in the code."""
    demand: float = Field(default=0.30, ge=0, le=1)
    gap: float = Field(default=0.25, ge=0, le=1)
    people: float = Field(default=0.15, ge=0, le=1)
    severity: float = Field(default=0.15, ge=0, le=1)
    vulnerability: float = Field(default=0.15, ge=0, le=1)
    discount_lambda: float = Field(default=0.60, ge=0, le=1,
                                   description="How strongly committed investment "
                                               "suppresses priority")


class TranscribeIn(BaseModel):
    """Audio submitted for server-side transcription.

    Base64 in a JSON body rather than a multipart upload, so the project keeps
    its three-package dependency list — clips are seconds long.
    """
    audio_base64: str = Field(description="Base64-encoded audio (webm/ogg/mp4/wav/m4a)")
    filename: str = Field(default="audio.webm",
                          description="Used only for the content-type the STT service sees")
    language: Optional[str] = Field(default=None,
                                    description="ISO-639-1 hint. Omit to let the model detect.")


class LoginIn(BaseModel):
    """Staff sign-in. Citizens never authenticate — intake is anonymous."""
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
