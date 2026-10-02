from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    title: str = ""
    source: str = ""

    @property
    def embedding_text(self) -> str:
        return f"{self.title}. {self.text}" if self.title else self.text


@dataclass(frozen=True)
class TestCase:
    __test__ = False  # stop pytest collecting this as a test class

    id: str
    text: str
    title: str = ""
    source: str = ""

    @property
    def embedding_text(self) -> str:
        return f"{self.title}. {self.text}" if self.title and self.text else self.title or self.text


@dataclass(frozen=True)
class Link:
    requirement_id: str
    test_id: str
    score: float
