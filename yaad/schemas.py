from typing import Literal
from pydantic import BaseModel, Field

class FolderRequest(BaseModel):
    path: str
    cloud_consent: bool = False

class FolderPickerRequest(BaseModel):
    cloud_consent: bool = False

class FolderConsentRequest(BaseModel):
    cloud_consent: bool

class IndexRequest(BaseModel):
    folder_id: int | None = None

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    folder_id: int | None = None

class AskResponse(BaseModel):
    status: Literal["answered", "conflict", "ambiguous", "partial", "not_found", "unverified", "indexing", "cloud_unavailable", "local_unavailable", "error"]
    answer: str
    values: list[dict] = []
    evidence: list[dict] = []
    candidates: list[dict] = []
    disclosure: list[dict] = []

class MemoryExtractRequest(BaseModel):
    folder_id: int

class MemoryFlagRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
