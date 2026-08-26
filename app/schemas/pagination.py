"""The envelope for growing collections.

The brief asks for "a practical way to browse lists as the number of items
grows". Offset/limit is the simplest thing that delivers that, and it carries
`total` so a client can render "showing 20 of 143" without a second request.

Cursor pagination would be the better choice at scale -- it does not drift when
rows are inserted mid-browse, and it does not get slower as the offset grows --
but it cannot answer "how many are there" cheaply and is more machinery than
this dataset justifies. Noted in the README as an evolution point.
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
