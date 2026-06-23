"""Hermes integration bridge for the Attractor pipeline framework.

Provides custom backends, interviewer, and runner that connect Attractor's
pipeline engine to Hermes' delegation and event system.
"""

from core.hermes_attractor.backends import HermesCodergenBackend
from core.hermes_attractor.interviewer import HermesInterviewer
from core.hermes_attractor.linear_sink import LinearSink
from core.hermes_attractor.runner import HermesPipelineRunner

__all__ = [
    "HermesCodergenBackend",
    "HermesInterviewer",
    "LinearSink",
    "HermesPipelineRunner",
]