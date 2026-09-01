from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictSchemaModel(BaseModel):
    """Base schema that rejects model-invented or client-supplied fields."""

    model_config = ConfigDict(extra="forbid")


class ResearchQuestion(StrictSchemaModel):
    """One focused question in the approved research plan."""

    question: str = Field(min_length=1, max_length=2_000)
    why_it_matters: str = Field(min_length=1, max_length=4_000)
    evidence_needed: list[str] = Field(default_factory=list, max_length=20)
    preferred_sources: list[str] = Field(default_factory=list, max_length=20)


class VisualRequirement(StrictSchemaModel):
    """Evidence-bearing visual requested by the plan."""

    stable_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=2_000)
    intended_takeaway: str = Field(min_length=1, max_length=2_000)
    visual_type: Literal[
        "bar_chart",
        "line_chart",
        "scatter_plot",
        "map",
        "timeline",
        "flow_diagram",
        "web_image",
        "other",
    ]
    data_requirements: list[str] = Field(default_factory=list, max_length=30)
    units_and_denominator: str = Field(default="", max_length=1_000)
    minimum_data_sufficiency: str = Field(default="", max_length=1_000)
    fallback_if_unsupported: str = Field(default="", max_length=1_000)
    alt_text: str = Field(default="", max_length=1_000)
    placement_section: str = Field(default="", max_length=300)
    required: bool = False


class ResearchBrief(StrictSchemaModel):
    """Structured plan produced before evidence collection begins."""

    title: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=4_000)
    audience: str = Field(default="General", max_length=500)
    output_language: str = Field(min_length=1, max_length=100)
    scope_inclusions: list[str] = Field(default_factory=list, max_length=50)
    scope_exclusions: list[str] = Field(default_factory=list, max_length=50)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    definitions: list[str] = Field(default_factory=list, max_length=50)
    research_questions: list[ResearchQuestion] = Field(min_length=1, max_length=30)
    source_hierarchy: list[str] = Field(default_factory=list, max_length=30)
    recency_requirements: list[str] = Field(default_factory=list, max_length=30)
    comparison_dimensions: list[str] = Field(default_factory=list, max_length=30)
    required_tables: list[str] = Field(default_factory=list, max_length=20)
    required_sections: list[str] = Field(default_factory=list, max_length=30)
    decision_criteria: list[str] = Field(default_factory=list, max_length=30)
    requires_calculation: bool = False
    visual_strategy: str = Field(default="", max_length=4_000)
    visual_requirements: list[VisualRequirement] = Field(default_factory=list, max_length=12)
    final_research_instruction: str = Field(min_length=1, max_length=20_000)


class ReviewIssue(StrictSchemaModel):
    """Specific evidence, citation, methodology, or visual defect."""

    severity: Literal["critical", "major", "minor"]
    category: Literal[
        "unsupported_claim",
        "weak_source",
        "freshness",
        "contradiction",
        "missing_context",
        "methodology",
        "citation",
        "visualization",
        "other",
    ]
    claim_or_section: str = Field(min_length=1, max_length=2_000)
    problem: str = Field(min_length=1, max_length=4_000)
    required_fix: str = Field(min_length=1, max_length=4_000)
    verification_sources: list[str] = Field(default_factory=list, max_length=20)


class CoverageAssessment(StrictSchemaModel):
    """Coverage verdict for one approved research question."""

    research_question: str = Field(min_length=1, max_length=2_000)
    status: Literal["answered", "partially_answered", "evidence_unavailable"]
    evidence_summary: str = Field(default="", max_length=4_000)
    remaining_gap: str = Field(default="", max_length=4_000)


class QualityReview(StrictSchemaModel):
    """Independent publication gate output."""

    overall_assessment: str = Field(min_length=1, max_length=8_000)
    ready_to_publish: bool
    strengths: list[str] = Field(default_factory=list, max_length=30)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=100)
    missing_perspectives: list[str] = Field(default_factory=list, max_length=30)
    unresolved_uncertainties: list[str] = Field(default_factory=list, max_length=30)
    coverage: list[CoverageAssessment] = Field(default_factory=list, max_length=30)
    source_quality_observations: list[str] = Field(default_factory=list, max_length=30)
    visual_assessment: list[str] = Field(default_factory=list, max_length=30)
    revision_instructions: list[str] = Field(default_factory=list, max_length=100)


class ArticleEdit(StrictSchemaModel):
    """One exact, inclusive replacement inside an existing Markdown article."""

    start_snippet: str = Field(
        min_length=1,
        max_length=4_000,
        description="Exact unique text where the replacement range begins.",
    )
    end_snippet: str = Field(
        min_length=1,
        max_length=4_000,
        description="Exact text where the inclusive replacement range ends.",
    )
    replacement_markdown: str = Field(
        default="",
        max_length=120_000,
        description="Markdown replacing the complete anchored range.",
    )
    rationale: str = Field(default="", max_length=4_000)

    @field_validator("start_snippet", "end_snippet")
    @classmethod
    def reject_blank_anchor(cls, value: str) -> str:
        """Keep exact whitespace intact while rejecting unusable blank anchors."""

        if not value.strip():
            raise ValueError("Article edit anchors cannot be blank.")
        return value


class ArticleRevision(StrictSchemaModel):
    """A bounded set of anchored edits for an already-written article."""

    summary: str = Field(default="", max_length=8_000)
    edits: list[ArticleEdit] = Field(default_factory=list, max_length=30)
