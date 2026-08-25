"""Pydantic request/response models for the API."""

from pydantic import BaseModel


class IsbnScanRequest(BaseModel):
    isbn: str


class ManualVolumeRequest(BaseModel):
    """For books with no barcode, a damaged one, or ones Google Books
    doesn't have a record for — bypasses the lookup entirely."""

    series_title: str
    volume_number: int | None = None
    author: str | None = None
    isbn: str | None = None


class VolumeUpdateRequest(BaseModel):
    volume_number: int | None = None
    series_title: str | None = None


class VolumeResponse(BaseModel):
    id: int
    series_title: str
    volume_number: int | None
    isbn: str | None
    already_owned: bool
    has_cover: bool = False
