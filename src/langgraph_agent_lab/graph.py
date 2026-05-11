"""Graph construction for the LangGraph support-ticket agent.

Import-safe: LangGraph is only imported inside build_graph() so unit tests that
check schema/metrics can run without a full LangGraph installation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .nodes import (
    answer_node,
    approval_node,
    ask_clarification_node,
    classify_node,
    dead_letter_node,
    evaluate_node,
    finalize_node,
    intake_node,
    retry_or_fallback_node,
    risky_action_node,
    tool_node,
)
from .routing import (
    route_after_approval,
    route_after_classify,
    route_after_evaluate,
    route_after_retry,
)
from .state import AgentState


def build_graph(checkpointer: Any | None = None) -> Any:  # noqa: ANN401
    """Build and compile the LangGraph support-ticket workflow.

    Graph architecture:
        START → intake → classify → [conditional routing]
          simple        → answer → finalize → END
          tool          → tool → evaluate → answer → finalize → END
          missing_info  → clarify → finalize → END
          risky         → risky_action → approval → tool → evaluate → answer → finalize → END
          error         → retry loop (bounded) → dead_letter → finalize → END
    """
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "LangGraph is required. Run: pip install -e '.[dev]'"
        ) from exc

    graph = StateGraph(AgentState)

    # Register all nodes
    graph.add_node("intake", intake_node)
    graph.add_node("classify", classify_node)
    graph.add_node("answer", answer_node)
    graph.add_node("tool", tool_node)
    graph.add_node("evaluate", evaluate_node)
    graph.add_node("clarify", ask_clarification_node)
    graph.add_node("risky_action", risky_action_node)
    graph.add_node("approval", approval_node)
    graph.add_node("retry", retry_or_fallback_node)
    graph.add_node("dead_letter", dead_letter_node)
    graph.add_node("finalize", finalize_node)

    # Entry path
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "classify")

    # Conditional dispatch from classify
    graph.add_conditional_edges("classify", route_after_classify)

    # Tool execution and evaluate/retry loop
    graph.add_edge("tool", "evaluate")
    graph.add_conditional_edges("evaluate", route_after_evaluate)

    # Clarification path terminates at finalize
    graph.add_edge("clarify", "finalize")

    # Risky path: prepare → approve → tool
    graph.add_edge("risky_action", "approval")
    graph.add_conditional_edges("approval", route_after_approval)

    # Bounded retry loop: exhausted → dead_letter
    graph.add_conditional_edges("retry", route_after_retry)

    # Terminal paths
    graph.add_edge("answer", "finalize")
    graph.add_edge("dead_letter", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)


def export_mermaid_diagram(output_path: str | Path = "outputs/graph_diagram.md") -> str:
    """Export the graph as a Mermaid diagram to output_path.

    Bonus extension: visual documentation of the workflow topology.
    Returns the Mermaid markdown string.
    """
    graph = build_graph()
    mermaid = graph.get_graph().draw_mermaid()

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"```mermaid\n{mermaid}\n```\n", encoding="utf-8")
    return mermaid
