"""Node implementations for the LangGraph workflow.

Each function is small, testable, and returns a partial state update.
Input state is never mutated.
"""

from __future__ import annotations

import re

from .state import AgentState, ApprovalDecision, Route, make_event

# Priority-ordered keyword sets for classify_node
_RISKY_KEYWORDS = {
    "cancel", "deactivate", "delete", "refund", "remove", "revoke", "send", "wipe"
}
_TOOL_KEYWORDS = {
    "check", "find", "get", "lookup", "order", "retrieve", "search", "status", "track"
}
_ERROR_KEYWORDS = {
    "broken", "crash", "error", "exception", "fail", "failure", "timeout", "unavailable"
}
# missing_info: query is very short AND contains a vague pronoun
_VAGUE_PRONOUNS = {"it", "that", "them", "they", "this"}


def _tokenize(text: str) -> list[str]:
    """Lower-case whole-word tokens (strips punctuation)."""
    return re.findall(r"\b[a-z]+\b", text.lower())


def intake_node(state: AgentState) -> dict:
    """Normalize raw query: strip whitespace, collapse spaces, truncate at 500 chars."""
    raw = state.get("query", "")
    query = " ".join(raw.split())[:500]
    return {
        "query": query,
        "messages": [f"intake:{query[:60]}"],
        "events": [make_event("intake", "completed", "query normalized", length=len(query))],
    }


def classify_node(state: AgentState) -> dict:
    """Keyword-based routing. Priority order: risky > tool > missing_info > error > simple."""
    query = state.get("query", "")
    tokens = set(_tokenize(query))

    if tokens & _RISKY_KEYWORDS:
        route, risk_level = Route.RISKY, "high"
    elif tokens & _TOOL_KEYWORDS:
        route, risk_level = Route.TOOL, "low"
    elif len(tokens) < 5 and tokens & _VAGUE_PRONOUNS:
        route, risk_level = Route.MISSING_INFO, "low"
    elif tokens & _ERROR_KEYWORDS:
        route, risk_level = Route.ERROR, "medium"
    else:
        route, risk_level = Route.SIMPLE, "low"

    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [
            make_event("classify", "completed", f"route={route.value}", risk_level=risk_level)
        ],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information based on query context."""
    query = state.get("query", "")
    question = (
        f"Your request '{query[:80]}' needs more details. "
        "Please provide an order ID, customer ID, or more specific description."
    )
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "clarification question generated")],
    }


def tool_node(state: AgentState) -> dict:
    """Execute mock tool. Simulates transient failures for error-route scenarios."""
    attempt = int(state.get("attempt", 0))
    scenario_id = state.get("scenario_id", "unknown")
    route = state.get("route", "")

    # Error-route scenarios fail on attempts 0 and 1; succeed on attempt 2+
    if route == Route.ERROR.value and attempt < 2:
        result = f"ERROR: transient failure attempt={attempt} scenario={scenario_id}"
    else:
        query = state.get("query", "")
        result = (
            f"tool-result: processed '{query[:50]}' "
            f"on attempt={attempt} scenario={scenario_id}"
        )

    return {
        "tool_results": [result],
        "events": [
            make_event("tool", "completed", f"tool attempt={attempt}", scenario=scenario_id)
        ],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for the approval gate."""
    query = state.get("query", "")
    proposed = (
        f"Proposed HIGH RISK action for: '{query[:100]}'. "
        "Requires explicit human approval before proceeding."
    )
    return {
        "proposed_action": proposed,
        "events": [
            make_event("risky_action", "pending_approval", "awaiting approval")
        ],
    }


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop approval gate.

    Set LANGGRAPH_INTERRUPT=true for real interrupt()-based HITL.
    Default: mock approval for CI/offline runs.
    """
    import os

    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt({
            "proposed_action": state.get("proposed_action"),
            "risk_level": state.get("risk_level"),
            "query": state.get("query"),
        })
        if isinstance(value, dict):
            decision = ApprovalDecision(**value)
        else:
            decision = ApprovalDecision(approved=bool(value), comment="interrupt approval")
    else:
        decision = ApprovalDecision(
            approved=True, comment="mock approval (set LANGGRAPH_INTERRUPT=true for HITL)"
        )

    return {
        "approval": decision.model_dump(),
        "events": [
            make_event(
                "approval",
                "completed",
                f"approved={decision.approved}",
                reviewer=decision.reviewer,
                comment=decision.comment,
            )
        ],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Increment retry counter. Dead-letter routing is enforced in route_after_retry."""
    attempt = int(state.get("attempt", 0)) + 1
    max_attempts = int(state.get("max_attempts", 3))
    return {
        "attempt": attempt,
        "errors": [f"retry attempt={attempt} of max={max_attempts}"],
        "events": [
            make_event(
                "retry",
                "completed",
                f"retry recorded attempt={attempt}",
                attempt=attempt,
                max_attempts=max_attempts,
            )
        ],
    }


def answer_node(state: AgentState) -> dict:
    """Produce final answer grounded in tool results and approval context."""
    tool_results = state.get("tool_results", []) or []
    approval = state.get("approval")

    if tool_results:
        latest = tool_results[-1]
        reviewer = (approval or {}).get("reviewer", "reviewer")
        prefix = f"Action approved by {reviewer}. " if approval else ""
        answer = f"{prefix}Result: {latest}"
    else:
        query = state.get("query", "")
        answer = f"Your request '{query[:80]}' has been processed."

    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "final answer generated")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Check latest tool result — the 'done?' gate that drives the retry loop."""
    tool_results = state.get("tool_results", []) or []
    latest = tool_results[-1] if tool_results else ""

    if "ERROR" in latest.upper():
        return {
            "evaluation_result": "needs_retry",
            "events": [make_event("evaluate", "needs_retry", "tool result has error signal")],
        }
    return {
        "evaluation_result": "success",
        "events": [make_event("evaluate", "success", "tool result is satisfactory")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Log unresolvable failure. Third layer: retry → fallback → dead letter."""
    attempt = state.get("attempt", 0)
    max_attempts = state.get("max_attempts", 3)
    scenario_id = state.get("scenario_id", "unknown")
    query = state.get("query", "")
    return {
        "final_answer": (
            f"Request failed after {attempt}/{max_attempts} attempts. "
            f"Scenario '{scenario_id}' logged for manual review. "
            f"Query: '{query[:60]}'"
        ),
        "events": [
            make_event(
                "dead_letter",
                "completed",
                f"max retries exhausted attempt={attempt}",
                scenario_id=scenario_id,
                max_attempts=max_attempts,
            )
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Emit final audit event. All graph paths terminate here."""
    route = state.get("route", "unknown")
    return {"events": [make_event("finalize", "completed", f"workflow finished route={route}")]}
