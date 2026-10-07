"""Actual bounded component reuse; the registry is data, not an Agent loop."""

import json
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import Field, model_validator

from unfold.contracts import Contract


class SearchParameters(Contract):
    values: Annotated[list[Annotated[int, Field(ge=-1000000, le=1000000)]], Field(max_length=32)]
    target: Annotated[int, Field(ge=-1000000, le=1000000)]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.values != sorted(self.values):
            raise ValueError("binary search component requires an ascending array")
        return self


def registry() -> list[dict[str, Any]]:
    return [
        {
            "id": "binary-search.v1",
            "title": "二分查找单步组件",
            "version": "component.v1",
            "capabilities": ["step", "reset", "keyboard", "hit", "miss", "empty_array"],
            "parameters_schema": SearchParameters.model_json_schema(),
            "source": "program-authored; bounded arrays; checked independently",
        }
    ]


def search_component(parameters: SearchParameters) -> str:
    data = json.dumps(parameters.model_dump(mode="json"))
    return (
        Path(__file__)
        .with_name("templates")
        .joinpath("binary-search.html")
        .read_text()
        .replace("__UNFOLD_PARAMETERS__", data)
    )
