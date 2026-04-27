from enum import Enum

from pydantic import BaseModel


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Manifest(BaseModel):
    sources: dict[str, str] = {}
    brief_inputs: dict[str, str] = {}
    project_brief_input: str = ""

    def diff(self, other: Manifest) -> list[str]:
        mismatched = []

        all_sources = set(self.sources.keys()) | set(other.sources.keys())

        for source in all_sources:
            checksum1 = self.sources.get(source)
            checksum2 = other.sources.get(source)

            if checksum1 != checksum2:
                mismatched.append(source)

        return mismatched


class Brief(BaseModel):
    brief: str


class AuditFinding(BaseModel):
    severity: Severity
    location: str
    issue: str
    suggestion: str


class SpecAuditReport(BaseModel):
    findings: list[AuditFinding]


class CrossSpecFinding(BaseModel):
    severity: Severity
    specs: list[str]
    issue: str
    suggestion: str


class CrossSpecAuditReport(BaseModel):
    findings: list[CrossSpecFinding] = []
