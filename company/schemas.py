from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Brief(Strict):
    markdown: str = Field(min_length=40, max_length=50000)


class Endpoint(Strict):
    path: str = Field(pattern=r"^/[A-Za-z0-9_/{}/-]*$")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    summary: str
    request_schema: dict | None
    response_schema: dict
    success_status: int = Field(ge=200, le=299)


class Contract(Strict):
    title: str
    endpoints: list[Endpoint] = Field(min_length=1, max_length=30)
    data_models: dict
    frontend_orders: list[str] = Field(min_length=1)
    backend_orders: list[str] = Field(min_length=1)
    acceptance_tests: list[str] = Field(min_length=1)


class SourceFile(Strict):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=100000)


class CodeBundle(Strict):
    files: list[SourceFile] = Field(min_length=1, max_length=40)


class Issue(Strict):
    owner: Literal["frontend", "backend", "both"]
    severity: Literal["critical", "high", "medium"]
    description: str = Field(min_length=5, max_length=2000)


class Review(Strict):
    issues: list[Issue] = Field(max_length=30)
    lessons: list[str] = Field(max_length=10)


class Decision(Strict):
    action: Literal["approve", "revise", "reject"]
    revision: int
    feedback: str = Field(default="", max_length=4000)


class State(TypedDict, total=False):
    project_id: str
    idea: str
    research: str
    prd: str
    prd_note_hash: str
    prd_note: str
    revision: int
    feedback: str
    decision: str
    contract: dict
    frontend_files: list[str]
    backend_files: list[str]
    issues: list[dict]
    lessons: list[str]
    retries: dict[str, int]
    qa_passed: bool
    status: str
