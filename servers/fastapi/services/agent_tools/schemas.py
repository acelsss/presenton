import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OpenSession(ProtocolModel):
    label: str = Field(min_length=1, max_length=80)


class CreateDocument(ProtocolModel):
    operation_id: uuid.UUID = Field(alias="operationId")
    title: str = Field(min_length=1, max_length=300)
    language: str = Field(default="Chinese", min_length=1, max_length=80)


class ToolRequest(ProtocolModel):
    tool: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class Mutation(ToolRequest):
    operation_id: uuid.UUID = Field(alias="operationId")
    expected_revision: int = Field(alias="expectedRevision", ge=0, strict=True)
    # Accept cached legacy payloads, but never expose or enforce writer epochs.
    writer_epoch: SkipJsonSchema[int | None] = Field(default=None, alias="writerEpoch")


class ReadBatch(ProtocolModel):
    operations: list[ToolRequest] = Field(
        min_length=1, max_length=20,
        description="Array of native tool objects. Only saveSlide.arguments.content needs JSON string encoding.",
    )

    @field_validator("operations", mode="before")
    @classmethod
    def decode_operations(cls, value):
        # Some model clients JSON-encode the array along with nested slide content.
        # Decode once, then apply the same bounded list and native input schemas.
        return json.loads(value) if isinstance(value, str) else value


class BatchMutation(ReadBatch):
    operation_id: uuid.UUID = Field(alias="operationId")
    expected_revision: int = Field(alias="expectedRevision", ge=0, strict=True)
    writer_epoch: SkipJsonSchema[int | None] = Field(default=None, alias="writerEpoch")


class SelectTemplate(ProtocolModel):
    template_id: str = Field(alias="templateId", min_length=1, max_length=200)


class ConfirmOutline(ProtocolModel):
    confirmed: Literal[True]
