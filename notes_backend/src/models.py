from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class ApiMessage(BaseModel):
    message: str = Field(..., description="Human-readable message")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", description="Token type; always 'bearer'")


class UserMeResponse(BaseModel):
    id: int = Field(..., description="User ID")
    email: EmailStr = Field(..., description="User email")
    created_at: datetime = Field(..., description="Account creation time")
    updated_at: datetime = Field(..., description="Account last update time")


class SignupRequest(BaseModel):
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=8, max_length=256, description="Password (min 8 chars)")


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=1, max_length=256, description="Password")


class TagResponse(BaseModel):
    id: int = Field(..., description="Tag ID")
    name: str = Field(..., description="Tag name")


class TagCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="Tag name")


class NoteResponse(BaseModel):
    id: int = Field(..., description="Note ID")
    title: str = Field(..., description="Note title")
    content: str = Field(..., description="Note body content")
    is_archived: bool = Field(..., description="Archived flag")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")
    tags: List[TagResponse] = Field(default_factory=list, description="Tags assigned to this note")


class NoteCreateRequest(BaseModel):
    title: str = Field("", max_length=500, description="Note title")
    content: str = Field("", max_length=200000, description="Note content")
    tag_names: List[str] = Field(default_factory=list, description="List of tag names to assign")


class NoteUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=500, description="New title (optional)")
    content: Optional[str] = Field(None, max_length=200000, description="New content (optional)")
    is_archived: Optional[bool] = Field(None, description="Archive toggle (optional)")
    tag_names: Optional[List[str]] = Field(None, description="Replace tags with these names (optional)")


class NotesListResponse(BaseModel):
    items: List[NoteResponse] = Field(..., description="Notes list")
    total: int = Field(..., description="Total notes matching the query")
