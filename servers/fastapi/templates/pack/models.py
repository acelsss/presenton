from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(StrictModel):
    x: float = 0
    y: float = 0


class Size(StrictModel):
    width: float = Field(gt=0, le=10000)
    height: float = Field(gt=0, le=10000)


class Theme(StrictModel):
    id: str
    name: str
    tokens: dict[str, dict[str, Any]]


class ElementStyle(StrictModel):
    id: str
    type: str
    name: str
    extends: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class Node(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    type: Literal["text", "vector", "image", "table", "group", "instance"]
    position: Point = Field(default_factory=Point)
    size: Size | None = None
    styleRef: str | None = None
    compositionRef: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    props: dict[str, Any] = Field(default_factory=dict)
    children: list["Node"] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)


class Binding(StrictModel):
    nodeId: str
    property: Literal["content.text", "content.src", "props.rows"]
    valueType: Literal["string", "array"] = "string"
    required: bool = True


class Composition(StrictModel):
    id: str
    name: str
    root: Node


class Layout(Composition):
    bindings: dict[str, Binding] = Field(default_factory=dict)
    sourceSlide: int | None = None


class PagePattern(StrictModel):
    id: str
    name: str
    compositionRef: str


class Asset(StrictModel):
    id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mimeType: str
    filename: str
    size: int = Field(ge=0)
    role: Literal["image", "media", "source"]


class TemplatePack(StrictModel):
    schemaVersion: Literal["1.0"] = "1.0"
    id: str
    version: str
    name: str
    supportedModes: list[Literal["template", "composition"]]
    canvas: Size
    defaultThemeRef: str
    themes: list[Theme]
    elementStyles: list[ElementStyle]
    compositions: list[Composition]
    pagePatterns: list[PagePattern]
    layouts: list[Layout]
    assets: list[Asset]
    provenance: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
