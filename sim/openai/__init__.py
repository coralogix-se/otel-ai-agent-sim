"""OpenAI API Platform (Administration) simulator.

Standalone process: ``python -m sim.openai``. Emits ``openai_administration_*``
Prometheus gauges shaped for the AI Center API Platform dashboard
(``PromOpenAiAdminQueriesService``). Not mixed into the Claude/Copilot agent loop.
"""

from __future__ import annotations

__all__ = ["OpenAIAdminSim"]


def __getattr__(name: str):
    if name == "OpenAIAdminSim":
        from sim.openai.runtime import OpenAIAdminSim

        return OpenAIAdminSim
    raise AttributeError(name)
