"""cloakai compiler — one source plugin, many client formats."""

from .clients import AGENT_MANIFEST, NAMESPACE, TARGETS
from .validate import ValidationError

__all__ = ["AGENT_MANIFEST", "NAMESPACE", "TARGETS", "ValidationError"]
