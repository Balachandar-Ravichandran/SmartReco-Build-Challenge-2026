"""build_graph() (Section 6.1) — compiles the LangGraph StateGraph once.

Retry is a SINGLE act_retry node (not two independent conditional nodes) —
see Section 6.1's revision note on why two nodes with a shared fan-in deadlocks.
No checkpointer — acceptable at this hackathon's single-process scale (Section 16).
"""
from langgraph.graph import StateGraph, END

from backend.agent.state import RecommendationState
from backend.agent import (
    planner,
    embedder,
    act_path,
    act_course,
    act_retry,
    validator,
    solver,
    fallback,
    writer,
)


def route_after_pass1(state: RecommendationState) -> str:
    """Reads ONLY the two certificates' retry flags — never re-inspects raw candidates."""
    if state["validate_1_path_cert"].retry or state["validate_1_course_cert"].retry:
        return "retry"
    return "proceed"


def route_after_pass2(state: RecommendationState) -> str:
    if state["validate_2_path_cert"].success and state["validate_2_course_cert"].success:
        return "proceed"
    return "exhausted"


def route_after_solver(state: RecommendationState) -> str:
    return "write" if state["status"] == "writing" else "fallback"


_compiled_graph = None


def build_graph():
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph

    graph = StateGraph(RecommendationState)
    graph.add_node("planner", planner.run)
    graph.add_node("embed", embedder.run)
    graph.add_node("act_path", act_path.run)
    graph.add_node("act_course", act_course.run)
    graph.add_node("validate_1", validator.run_pass_1)
    graph.add_node("act_retry", act_retry.run)
    graph.add_node("validate_2", validator.run_pass_2)
    graph.add_node("solver", solver.run)
    graph.add_node("fallback", fallback.run)
    graph.add_node("write", writer.run)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "embed")
    graph.add_edge("embed", "act_path")
    graph.add_edge("embed", "act_course")
    graph.add_edge("act_path", "validate_1")
    graph.add_edge("act_course", "validate_1")
    graph.add_conditional_edges(
        "validate_1", route_after_pass1, {"retry": "act_retry", "proceed": "solver"}
    )
    graph.add_edge("act_retry", "validate_2")
    graph.add_conditional_edges(
        "validate_2", route_after_pass2, {"proceed": "solver", "exhausted": "fallback"}
    )
    graph.add_conditional_edges(
        "solver", route_after_solver, {"write": "write", "fallback": "fallback"}
    )
    graph.add_edge("fallback", END)
    graph.add_edge("write", END)

    _compiled_graph = graph.compile()
    return _compiled_graph
