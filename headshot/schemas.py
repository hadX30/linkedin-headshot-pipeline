"""
Typed shapes for what the Claude stages return.

Live mode sends these as structured-output schemas, so the model's reply is
validated JSON; mock mode builds the same objects from fixtures.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── Stage 1: photo analysis ───────────────────────────────────────────────────

class FacialHair(BaseModel):
    present: bool
    type: Literal["none", "full_beard", "goatee", "mustache", "stubble", "clean_shaven"]
    description: str = Field(description="Precise English description, e.g. 'short trimmed full beard, dark brown, grey at chin'")
    description_ar: str = Field(description="Same, in at most 8 Arabic words; empty string if none")


class Glasses(BaseModel):
    present: bool
    type: Literal["none", "prescription", "sunglasses", "reading"]
    description: str
    description_ar: str


class Subject(BaseModel):
    apparent_gender: Literal["male", "female", "unclear"]
    apparent_age_range: Literal["child", "teen", "young_adult", "adult", "senior"]
    skin_tone: str
    facial_hair: FacialHair
    expression: Literal["smiling", "neutral", "slight_smile", "serious"]
    glasses: Glasses
    accessories: list[str]


class Headwear(BaseModel):
    type: Literal["none", "hijab", "niqab", "shemagh_with_iqal", "shemagh_without_iqal", "other"]
    color: str
    description: str = Field(description="Exact pattern/colour/draping in English")
    description_ar: str = Field(description="Same, in at most 8 Arabic words; empty string if none")


class Clothing(BaseModel):
    type: Literal["thobe", "suit", "abaya", "casual", "uniform", "other"]
    is_professional: bool
    color: str
    description: str
    description_ar: str


class Attire(BaseModel):
    headwear: Headwear
    clothing: Clothing
    needs_wardrobe_change: bool
    recommended_outfit: str


class PhotoQuality(BaseModel):
    resolution_sufficient: bool
    face_visible: bool
    face_angle: Literal["frontal", "three_quarter", "profile", "extreme"]
    lighting_quality: Literal["studio", "natural_good", "natural_poor", "harsh", "backlit"]
    has_beauty_filters: bool
    multiple_faces: bool
    background_type: Literal["solid", "indoor", "outdoor", "studio"]


class PipelineAction(BaseModel):
    proceed: bool
    mode: Literal["full_edit", "reduced_edit", "reject"]
    reject_reason: Literal["none", "multiple_faces", "no_face", "low_resolution", "minor_detected"]
    notes: str


class Analysis(BaseModel):
    subject: Subject
    attire: Attire
    photo_quality: PhotoQuality
    pipeline_action: PipelineAction


# ── Stage 4: quality validation ───────────────────────────────────────────────

class ScoredCheck(BaseModel):
    score: int = Field(description="0-100")
    issues: list[str]
    issues_ar: list[str] = Field(description="The same issues as short Arabic phrases")


class CulturalCheck(BaseModel):
    passed: bool
    issues: list[str]
    issues_ar: list[str]


class ValidationReport(BaseModel):
    face_match: ScoredCheck
    attire_accuracy: ScoredCheck
    background: ScoredCheck
    realism: ScoredCheck
    cultural_sensitivity: CulturalCheck
    retry_guidance: str = Field(description="Concrete prompt-wording changes for the next attempt; empty if none")


SCORED_CHECKS = ("face_match", "attire_accuracy", "background", "realism")
