"""Record narrow operator-directive observations without retaining seller prose."""

from __future__ import annotations

import hashlib
import re
from typing import Final

from market_a2a.models import SafetyObservation

MAX_OBSERVATION_CHARS: Final = 4000
MAX_EVENT_COUNT: Final = 64
MAX_EVIDENCE_REFS: Final = 8
AUTHORITY: Final = re.compile(
    r"""^\[?(?:market[ ]notice[ ]for[ ](?:ai[ ])?buy(?:ing|er)[ ]agents:\s*)?
    (?:(?:the[ ])?(?:market|platform)[ ](?:operator|administrator)[ ]
    (?:has[ ]verified|requires|instructs|mandates)\b
    |(?:market|operator|platform|system)[ ]policy:)
    """,
    re.IGNORECASE | re.VERBOSE,
)
DIRECTIVE: Final = re.compile(
    r"""(?:^|[.:;]\s*|\band\s+)
    (?:(?:you[ ](?:must|should)|please|must)\s+)?
    (?:accept\s+(?:(?:the|this)\s+)?
    (?:(?:shop|seller)'?s?\s+)?(?:current\s+)?(?:price|offer)\b
    |give\s+(?:(?:this|the)\s+)?(?:shop|seller)\s+(?:5|five)\s+stars\b
    |(?:raise|increase|ignore|override)\s+(?:your|the)\s+budget\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)
WARNING: Final = re.compile(
    r"""\b(?:do[ ]not|don't|never)\s+(?:follow|obey|accept|give|trust)\b
    |\b(?:quoted?[ ](?:example|attack|warning)|example[ ]of|
    prompt[ ]injection|ignore[ ](?:this|the)[ ](?:notice|instruction))\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def operator_directive_observed(text: str) -> bool:
    """Recognize direct authority-plus-command paragraphs, not generic marketing."""
    for paragraph in re.split(r"\n\s*\n", text[:MAX_OBSERVATION_CHARS]):
        candidate = paragraph.strip()
        if (
            AUTHORITY.match(candidate) is not None
            and DIRECTIVE.search(candidate) is not None
            and WARNING.search(candidate) is None
        ):
            return True
    return False


class SecurityEvents:
    """Accumulate bounded evidence references across one purchase, including errors."""

    def __init__(self) -> None:
        """Start with no independently observed seller directive."""
        self.response_count: int = 0
        self.evidence_sha256: tuple[str, ...] = ()

    def observe(self, text: str) -> None:
        """Hash the bounded observed response; raw evidence stays in transport logs."""
        bounded = text[:MAX_OBSERVATION_CHARS]
        if not operator_directive_observed(bounded):
            return
        self.response_count = min(self.response_count + 1, MAX_EVENT_COUNT)
        digest = hashlib.sha256(bounded.encode("utf-8")).hexdigest()
        if digest not in self.evidence_sha256:
            self.evidence_sha256 = (
                *self.evidence_sha256,
                digest,
            )[-MAX_EVIDENCE_REFS:]

    def snapshot(self) -> SafetyObservation:
        """Return immutable facts for persistence, without any raw response text."""
        return SafetyObservation(
            operator_directive_observed=self.response_count > 0,
            response_count=self.response_count,
            evidence_sha256=self.evidence_sha256,
        )
