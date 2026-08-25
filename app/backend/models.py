"""Pydantic request/response models for the API."""
from typing import Optional

from pydantic import BaseModel


class IsbnScanRequest(BaseModel):
    isbn: str


class ManualVolumeRequest(BaseModel):
    """For books with no barcode, a damaged one, or ones Google Books
    doesn't have a record for — bypasses the lookup entirely."""
    series_title: str
    volume_number: Optional[int] = None
    author: Optional[str] = None
    isbn: Optional[str] = None


class VolumeUpdateRequest(BaseModel):
    volume_number: Optional[int] = None
    series_title: Optional[str] = None


class VolumeResponse(BaseModel):
    id: int
    series_title: str
    volume_number: Optional[int]
    isbn: Optional[str]
    already_owned: bool
    has_cover: bool = False
