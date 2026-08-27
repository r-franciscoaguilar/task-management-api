"""Offset/limit envelope for growing collections.

`total` lets a client render "showing 20 of 143" without a second request.
Cursor pagination would be better at scale but cannot answer "how many"
cheaply -- see the README.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel

ItemT = TypeVar("ItemT")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Page(BaseModel, Generic[ItemT]):
    items: list[ItemT]
    total: int
    limit: int
    offset: int
