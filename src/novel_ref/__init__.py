"""NovelRef Skill — realtime style reference fetcher (memory-only)."""

from novel_ref.models import ReferenceParagraph, ReferenceResult
from novel_ref.skill import get_reference, list_tags

__all__ = [
    "ReferenceParagraph",
    "ReferenceResult",
    "get_reference",
    "list_tags",
]
