"""Pydantic models shared across modules."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Claim(BaseModel):
    """One statement in the personalization text and the stored evidence behind it."""

    text: str
    source_id: int | None = None          # sources.id for statements about the recipient's work
    profile_fact_id: str | None = None    # approved profile fact for statements about the student

    @model_validator(mode="after")
    def exactly_one_origin(self) -> Claim:
        if (self.source_id is None) == (self.profile_fact_id is None):
            raise ValueError("a claim needs exactly one of source_id or profile_fact_id")
        return self


class DraftSlots(BaseModel):
    """What a drafting call may produce. Code renders the locked skeleton around these slots."""

    subject_formula: Literal["S1", "S2", "S3", "S4"]
    subject_topic: str = Field(max_length=80)
    subject_year: int | None = None
    p1: str = Field(max_length=400)
    p2: str = Field(max_length=400)
    credential_fact_ids: list[str] = Field(min_length=2, max_length=3)
    task: str
    claims: list[Claim]
    confidence: float = Field(ge=0, le=1)


class Fact(BaseModel):
    id: str
    category: str
    resume_line: str                     # where on the resume this comes from (audit)
    email_phrase: str                    # the ONLY wording emails may use for this fact
    tags: list[str] = []
    numbers: list[str] = []              # numeric strings this fact legitimately contains
    status: Literal["approved_pending", "needs_confirmation"] = "approved_pending"
    note: str | None = None


class StudentProfile(BaseModel):
    version: int
    name: str
    school: str
    city: str
    grade: str
    gmail: str
    facts: list[Fact]
    banned_claims: list[str] = []        # words the student's resume does not support

    def fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def usable(self, fact_id: str) -> Fact | None:
        f = self.fact(fact_id)
        return f if f and f.status != "needs_confirmation" else None


class LintIssue(BaseModel):
    rule: str
    detail: str


class LintReport(BaseModel):
    ok: bool
    word_count: int
    issues: list[LintIssue] = []

    def rules(self) -> set[str]:
        return {i.rule for i in self.issues}


class VerifyResult(BaseModel):
    ok: bool
    failures: list[str] = []
