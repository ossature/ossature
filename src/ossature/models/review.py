from pydantic import BaseModel


class ReviewIssue(BaseModel):
    file: str
    target: str
    problem: str
    suggestion: str


class ReviewReport(BaseModel):
    passed: bool
    issues: list[ReviewIssue] = []
