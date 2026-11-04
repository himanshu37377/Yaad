from typing import Literal
from pydantic import BaseModel, Field

class FolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    cloud_consent: bool = False

class FolderPickerRequest(BaseModel):
    cloud_consent: bool = False

class FolderConsentRequest(BaseModel):
    cloud_consent: bool

class IndexRequest(BaseModel):
    folder_id: int | None = Field(default=None, gt=0)

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    folder_id: int | None = Field(default=None, gt=0)

class AskResponse(BaseModel):
    status: Literal["answered", "conflict", "ambiguous", "partial", "not_found", "unverified", "indexing", "cloud_unavailable", "local_unavailable", "error"]
    answer: str
    values: list[dict] = []
    evidence: list[dict] = []
    candidates: list[dict] = []
    disclosure: list[dict] = []

class MemoryExtractRequest(BaseModel):
    folder_id: int = Field(gt=0)

class MemoryFlagRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

class FeedbackRequest(BaseModel):
    evidence_id: str | None = Field(default=None, max_length=128)
    helpful: bool = False
