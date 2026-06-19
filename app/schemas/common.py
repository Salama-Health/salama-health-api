"""Shared schema base and generic response models."""
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """
    Base model: serialises to camelCase (matching the Flutter app) while still
    accepting snake_case or camelCase on input.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class Message(CamelModel):
    message: str


class HealthStatus(CamelModel):
    status: str
    models: dict | None = None
