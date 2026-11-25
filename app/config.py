"""Settings: model choices for each part of the pipeline, all on Anthropic Claude.

Models are matched to the job to keep cost down (prices per 1M input / output tokens):
  claude-haiku-5-5   $0.10 / $0.50   everything by default: Layer 1 answers, the Layer 2 planner and the specialists
  claude-sonnet-5-5  $2.00 / $10.00  optional upgrade for the planner: on 4 test tickets it cost about 8x more per ticket
                                     for slightly firmer wording; Python now enforces the one rule Haiku hedged on
Python prepares most of the evidence (log search, DB prefetch, ticket guidance), so the models mostly summarise.
Override any of them with an environment variable (LAYER1_MODEL, PLANNER_MODEL, WORKER_MODEL) to compare models.
"""
import os

from dotenv import load_dotenv

load_dotenv()

LAYER1_MODEL = os.getenv("LAYER1_MODEL", "claude-haiku-5-5")
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "claude-haiku-5-5")
WORKER_MODEL = os.getenv("WORKER_MODEL", "claude-haiku-5-5")

# Effort is the main output-token lever on current Claude models. Python already prepares the evidence
# (log search, DB prefetch, ticket guidance), so the models mostly summarise and low effort is enough.
LAYER1_EFFORT = os.getenv("LAYER1_EFFORT", "low")
PLANNER_EFFORT = os.getenv("PLANNER_EFFORT", "low")
WORKER_EFFORT = os.getenv("WORKER_EFFORT", "low")


def chat_model(model: str, effort: str, max_tokens: int = 4096):
    """A ChatAnthropic client. Cache control and structured output are handled by the agent middleware."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, max_tokens=max_tokens, output_config={"effort": effort})


def layer1_model():
    return chat_model(LAYER1_MODEL, LAYER1_EFFORT, max_tokens=2048)


def planner_model():
    return chat_model(PLANNER_MODEL, PLANNER_EFFORT, max_tokens=4096)


def worker_model():
    return chat_model(WORKER_MODEL, WORKER_EFFORT, max_tokens=4096)
