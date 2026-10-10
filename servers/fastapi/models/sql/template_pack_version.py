from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class TemplatePackVersion(SQLModel, table=True):
    __tablename__ = "template_pack_versions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    template_id: str = Field(sa_column=Column(ForeignKey("template_v2.id", ondelete="CASCADE"), nullable=False, index=True))
    owner_id: uuid.UUID = Field(sa_column=Column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True), exclude=True)
    version: str
    source_hash: str = Field(index=True)
    source: dict = Field(sa_column=Column(JSON, nullable=False))
    validation_report: dict = Field(sa_column=Column(JSON, nullable=False))
    status: str = "draft"
    asset_directory: str = Field(exclude=True)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, default=get_current_utc_datetime))
