"""Memory type definitions."""

from __future__ import annotations

from enum import Enum


class MemoryType(str, Enum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


MEMORY_TYPE_DESCRIPTIONS = {
    MemoryType.USER: "Information about the user's role, preferences, expertise",
    MemoryType.FEEDBACK: "Guidance on behavior — corrections and confirmations",
    MemoryType.PROJECT: "Ongoing work, goals, initiatives, bugs, deadlines",
    MemoryType.REFERENCE: "Pointers to external systems and resources",
}
