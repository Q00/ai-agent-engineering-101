"""Parse preregistered experiment inputs at the filesystem boundary."""

from pathlib import Path

from pydantic import Field, TypeAdapter

from market.models import FrozenModel, Scenario

MIN_SCENARIOS = 4


class ExperimentConfig(FrozenModel):
    """Model, retry, and episode controls frozen before live calls."""

    provider: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int = Field(gt=0)
    request_timeout_s: float = Field(gt=0)
    max_turns: int = Field(gt=0)
    repetitions: int = Field(gt=0)
    retry_delays_s: tuple[float, ...]
    prompt_version: str


def load_config(path: Path) -> ExperimentConfig:
    """Parse one immutable JSON configuration."""
    return ExperimentConfig.model_validate_json(path.read_bytes())


def load_scenarios(path: Path) -> tuple[Scenario, ...]:
    """Parse the preregistered scenario list."""
    adapter = TypeAdapter(tuple[Scenario, ...])
    scenarios = adapter.validate_json(path.read_bytes())
    if len(scenarios) < MIN_SCENARIOS:
        message = "at least four scenarios are required"
        raise ValueError(message)
    return scenarios
