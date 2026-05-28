"""Shared fixtures for the guardrail test suite.

`make_payload` builds a JSON string with the three required keys
(`answer`, `citations`, `confidence_score`). Defaults form a passing
payload; pass overrides to construct blocking inputs.
"""
import json

import pytest


_UNSET = object()


def _build(answer="Pancho Villa led the Division del Norte.",
           citations=_UNSET,
           confidence_score=0.95,
           **extra):
    payload = {
        "answer": answer,
        "citations": [] if citations is _UNSET else citations,
        "confidence_score": confidence_score,
    }
    payload.update(extra)
    return json.dumps(payload)


@pytest.fixture
def make_payload():
    return _build
