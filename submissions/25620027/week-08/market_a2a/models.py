"""Typed contracts shared by the model loop, catalog and A2A adapters."""

from __future__ import annotations

from typing import ClassVar, Protocol, override

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


class FrozenModel(BaseModel):
    """Parse an external boundary once and keep its value immutable."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Product(FrozenModel):
    """A generated catalog item with explicit searchable attributes."""

    sku: str
    name: str
    description: str
    kind: str
    material: str = ""
    color: str = ""
    height_cm: int = 0
    capacity: int = 0
    reserve: int = Field(ge=1)


class ToolSpec(FrozenModel):
    """One local tool advertised through the OpenAI function interface."""

    name: str
    description: str
    parameters: dict[str, JsonValue]


class ToolResult(FrozenModel):
    """Tool feedback; terminal means one authorized action was committed."""

    data: JsonValue
    terminal: bool = False


class ToolDispatch(Protocol):
    """The adapter owns side effects and guards; the model chooses the tool."""

    async def __call__(self, name: str, arguments: str) -> ToolResult:
        """Parse and perform one tool call or return actionable error feedback."""
        ...


class SafetyObservation(FrozenModel):
    """Local observations of a narrow operator-directive attack pattern."""

    operator_directive_observed: bool = False
    response_count: int = Field(default=0, ge=0)
    evidence_sha256: tuple[str, ...] = ()


class Purchase(FrozenModel):
    """A single buyer's observable outcome and receipt identity."""

    completed: bool
    item: str
    sku: str = ""
    price: int | None = None
    task_id: str = ""
    shop: str = ""
    failure_observation: str = ""
    receipt: dict[str, JsonValue] | None = None
    safety: SafetyObservation = Field(default_factory=SafetyObservation)
    initial_ask: int | None = Field(default=None, strict=True, ge=1)
    offer_count: int | None = Field(default=None, strict=True, ge=0)


class ShopCard(FrozenModel):
    """Public discovery information, distinct from private product reserves."""

    url: str
    name: str
    skills: tuple[str, ...]


class ShoppingItem(FrozenModel):
    """One requested purchase; its id prevents duplicate concurrent checkout."""

    request_id: str
    description: str


class HarnessError(RuntimeError):
    """Typed runtime error whose traceback remains mutable for contextlib."""

    def __init__(self, detail: str) -> None:
        """Store the typed reason while retaining Python exception semantics."""
        self.detail: str = detail
        super().__init__(detail)

    @override
    def __str__(self) -> str:
        """Expose the reason without credentials."""
        return self.detail


def json_value(model: BaseModel) -> JsonValue:
    """Convert typed model output to the JSON value accepted by tool feedback."""
    return TypeAdapter(JsonValue).validate_json(model.model_dump_json())
