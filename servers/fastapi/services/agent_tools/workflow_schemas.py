"""Business inputs: structured content and opaque references, no lock protocol."""
import uuid
from typing import Any, Literal
from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema
from constants.presentation import MAX_NUMBER_OF_SLIDES, MAX_OUTLINE_CONTENT_WORDS
from services.agent_tools.schemas import ProtocolModel, ToolRequest
from utils.outline_limits import count_outline_words


class Prepare(ProtocolModel):
    title: str = Field(min_length=1, max_length=300)
    language: str = Field(default="Chinese", min_length=1, max_length=80)
    outline: list[str] = Field(min_length=1, max_length=MAX_NUMBER_OF_SLIDES)
    confirmed: Literal[True]
    style: str = Field(default="", max_length=300)
    asset_refs: list[str] = Field(default_factory=list, alias="assetRefs", max_length=100)
    template_offset: int = Field(default=0, alias="templateOffset", ge=0)

    @field_validator("outline")
    @classmethod
    def valid_outline(cls, values):
        if any(not v.strip() or len(v) > 20000 or count_outline_words(v) > MAX_OUTLINE_CONTENT_WORDS for v in values):
            raise ValueError(f"Each outline must be nonempty and <= {MAX_OUTLINE_CONTENT_WORDS} words")
        return values


class Start(ProtocolModel):
    preparation_ref: str = Field(alias="preparationRef", min_length=20, max_length=100)
    template_ref: str = Field(alias="templateRef", min_length=1, max_length=100)
    layout_plan: list[str] = Field(alias="layoutPlan", min_length=1, max_length=MAX_NUMBER_OF_SLIDES)


class Page(ProtocolModel):
    slot_id: str = Field(alias="slotId", min_length=1, max_length=100)
    expected_page_revision: int = Field(default=0, alias="expectedPageRevision", ge=0, strict=True,
                                        description="Copy pageRevision for this slot; new slots start at 0.")
    content: dict[str, Any]
    speaker_note: str = Field(default="", alias="speakerNote", max_length=20000)


class Submit(ProtocolModel):
    model_config = ConfigDict(json_schema_extra={"required": ["taskRef"]})
    task_ref: str | None = Field(default=None, alias="taskRef", min_length=20, max_length=100,
                                description="Stable taskRef from ppt_start; reuse for corrections before completion.")
    edit_ref: SkipJsonSchema[str | None] = Field(default=None, alias="editRef", min_length=20, max_length=100)
    pages: list[Page] = Field(default_factory=list, max_length=MAX_NUMBER_OF_SLIDES)
    finish: bool = True
    export_formats: list[Literal["pptx", "pdf"]] = Field(default_factory=list, alias="exportFormats", max_length=2)

    @model_validator(mode="after")
    def target(self):
        if bool(self.task_ref) == bool(self.edit_ref):
            raise ValueError("Supply taskRef (legacy editRef is accepted only as an alternative)")
        return self


class Task(ProtocolModel):
    task_ref: str = Field(alias="taskRef", min_length=20, max_length=100)
    action: Literal["status", "resume", "cancel"] = "status"
    wait_seconds: int = Field(default=0, alias="waitSeconds", ge=0, le=30)


class Read(ProtocolModel):
    document_id: uuid.UUID = Field(alias="documentId")
    slide_ids: list[uuid.UUID] | None = Field(default=None, alias="slideIds", min_length=1, max_length=MAX_NUMBER_OF_SLIDES,
                                             description="Read only assigned slides when working as a subagent.")


class RevisionTarget(ProtocolModel):
    document_id: uuid.UUID | None = Field(default=None, alias="documentId")
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=0, strict=True,
                                          description="Copy revision from the latest ppt_read or successful ppt_edit.")
    edit_ref: SkipJsonSchema[str | None] = Field(default=None, alias="editRef", min_length=20, max_length=100)

    @model_validator(mode="after")
    def target(self):
        direct = self.document_id is not None and self.expected_revision is not None
        legacy = self.edit_ref is not None and self.document_id is None and self.expected_revision is None
        if not legacy and (not direct or self.edit_ref is not None):
            raise ValueError("Supply documentId and expectedRevision from ppt_read")
        return self


class PageEdit(ProtocolModel):
    slide_id: uuid.UUID = Field(alias="slideId")
    expected_page_revision: int = Field(alias="expectedPageRevision", ge=0, strict=True)
    operations: list[ToolRequest] = Field(min_length=1, max_length=20,
        description="Native edits confined to this slide. Omit index; the server resolves the stable slideId.")


class Edit(RevisionTarget):
    model_config = ConfigDict(json_schema_extra={"required": ["documentId"], "anyOf": [
        {"required": ["pageEdits"]}, {"required": ["expectedRevision", "operations"]}]})
    page_edits: list[PageEdit] = Field(default_factory=list, alias="pageEdits", max_length=20,
        description="Preferred: independent per-page versions let multiple agents edit different slides concurrently.")
    operations: list[ToolRequest] = Field(default_factory=list, max_length=20,
        description="Whole-document/structural operations require expectedRevision. Use pageEdits for page content.")

    @model_validator(mode="after")
    def target(self):
        if self.page_edits:
            if self.document_id is None or self.expected_revision is not None or self.edit_ref or self.operations:
                raise ValueError("pageEdits requires documentId only; do not mix whole-document operations or revisions")
            if len({page.slide_id for page in self.page_edits}) != len(self.page_edits):
                raise ValueError("Each slideId must appear once")
            if sum(len(page.operations) for page in self.page_edits) > 20:
                raise ValueError("At most 20 native operations per atomic edit")
            return self
        if not self.operations:
            raise ValueError("Supply pageEdits, or structural operations and expectedRevision")
        return super().target()


class Export(RevisionTarget):
    model_config = ConfigDict(json_schema_extra={"required": ["documentId", "expectedRevision"]})
    formats: list[Literal["pptx", "pdf"]] = Field(default=["pptx"], min_length=1, max_length=2)
