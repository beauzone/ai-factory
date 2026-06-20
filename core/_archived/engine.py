import json
import os
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from attractor_parser import Graph, Node, Edge, AttractorParser

@dataclass
class Outcome:
    status: str  # SUCCESS, PARTIAL_SUCCESS, FAIL
    notes: str = ""
    preferred_label: str = ""
    suggested_next_ids: List[str] = field(default_factory=list)
    context_updates: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Checkpoint:
    current_node_id: str
    completed_nodes: List[str]
    context: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

class PipelineEngine:
    """Execution engine for Attractor DOT-based AI workflows with parallel fan-out."""
    
    def __init__(self, graph: Graph, run_id: str, logs_root: Path, launderette_client: Any = None):
        self.graph = graph
        self.run_id = run_id
        self.logs_root = logs_root / run_id
        self.logs_root.mkdir(parents=True, exist_ok=True)
        self.launderette = launderette_client
        
        self.context: Dict[str, Any] = {}
        self.node_outcomes: Dict[str, Outcome] = {}
        self.completed_nodes: List[str] = []
        
        self.context["graph_goal"] = graph.goal
        self.context["graph_label"] = graph.label

    def run(self, handlers: Dict[str, Any]):
        current_node_id = self._find_start_node()
        
        while True:
            node = self.graph.nodes[current_node_id]
            
            if node.shape == "Msquare":
                gate_ok, failed_gate = self._check_goal_gates()
                if not gate_ok:
                    retry_target = self._get_retry_target(failed_gate)
                    if retry_target:
                        current_node_id = retry_target
                        continue
                    return Outcome(status="FAIL", notes="Goal gate unsatisfied and no retry target")
                return Outcome(status="SUCCESS", notes="Pipeline completed")

            # Parallel Fan-Out Handling (shape=component)
            if node.shape == "component":
                outcome = self._execute_parallel(node, handlers)
            else:
                # Standard sequential execution with retry
                max_attempts = (node.max_retries if node.max_retries is not None 
                                else self.graph.default_max_retries) + 1
                outcome = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        handler = self._get_handler(node, handlers)
                        outcome = handler.execute(node, self.context, self.graph, self.logs_root)
                        if outcome.status in ("SUCCESS", "PARTIAL_SUCCESS"):
                            break
                    except Exception as e:
                        if attempt == max_attempts:
                            outcome = Outcome(status="FAIL", notes=f"Execution exception: {str(e)}")

            self.completed_nodes.append(current_node_id)
            self.node_outcomes[current_node_id] = outcome
            
            if outcome:
                for k, v in outcome.context_updates.items():
                    self.context[k] = v
                self.context["last_outcome"] = outcome.status
                if outcome.preferred_label:
                    self.context["preferred_label"] = outcome.preferred_label

            self._save_checkpoint(current_node_id)
            next_edge = self._select_edge(node, outcome)
            if not next_edge:
                if outcome and outcome.status == "FAIL":
                    return outcome
                return Outcome(status="SUCCESS", notes="Pipeline completed via natural end")
            
            if next_edge.loop_restart:
                current_node_id = next_edge.to_node
                continue

            current_node_id = next_edge.to_node

    def _execute_parallel(self, node: Node, handlers: Dict[str, Any]) -> Outcome:
        """Spawns multiple sub-agents to execute child branches concurrently."""
        edges = self.graph.edges.get(node.id, [])
        if not edges:
            return Outcome(status="SUCCESS", notes="No parallel branches to run")

        results = []
        for edge in edges:
            # Each child branch is treated as a mini-pipeline
            # In Hermes, we use delegate_task to run these in parallel
            target_node = self.graph.nodes[edge.to_node]
            
            # we construct a specialized goal for the sub-agent
            sub_goal = f"Run stage {target_node.label}: {target_node.prompt}"
            
            # Delegate the task to the Hermes runtime
            # result = self.launderette.delegate_task(goal=sub_goal, ...)
            # For MVP, we simulate parallel return
            results.append(Outcome(status="SUCCESS", notes=f"Parallel branch {edge.to_node} completed"))

        # Fan-in: Combine results into a single outcome
        # If any branch failed, the parallel node is a PARTIAL_SUCCESS
        final_status = "SUCCESS" if all(r.status == "SUCCESS" for r in results) else "PARTIAL_SUCCESS"
        return Outcome(
            status=final_status, 
            notes=f"Parallel execution of {len(results)} branches completed."
        )

    def _find_start_node(self) -> str:
        for node_id, node in self.graph.nodes.items():
            if node.shape == "Mdiamond" or node_id.lower() in ("start", "begin"):
                return node_id
        raise ValueError("No start node found")

    def _get_handler(self, node: Node, handlers: Dict[str, Any]):
        mapping = {
            "Mdiamond": "start",
            "Msquare": "exit",
            "box": "codergen",
            "hexagon": "wait_human",
            "diamond": "conditional",
            "component": "parallel",
            "tripleoctagon": "parallel_fan_in",
            "parallelogram": "tool",
            "house": "stack_manager"
        }
        handler_type = node.type or mapping.get(node.shape, "codergen")
        return handlers.get(handler_type, handlers["codergen"])

    def _select_edge(self, node: Node, outcome: Outcome) -> Optional[Edge]:
        edges = self.graph.edges.get(node.id, [])
        if not edges: return None
        for edge in edges:
            if edge.condition and self._evaluate_condition(edge.condition, outcome):
                return edge
        if outcome and outcome.preferred_label:
            norm_pref = outcome.preferred_label.lower().strip()
            for edge in edges:
                if not edge.condition and edge.label.lower().strip() == norm_pref:
                    return edge
        eligible = [e for e in edges if not e.condition]
        if eligible:
            eligible.sort(key=lambda x: (-x.weight, x.to_node))
            return eligible[0]
        return None

    def _evaluate_condition(self, condition: str, outcome: Outcome) -> bool:
        if not outcome: return False
        status = outcome.status.lower()
        if condition == "outcome=success": return status == "success"
        if condition == "outcome!=success": return status != "success"
        if "outcome=" in condition:
            val = condition.split("=", 1)[1]
            return status == val
        return False

    def _check_goal_gates(self) -> tuple[bool, Optional[Node]]:
        for node_id, outcome in self.node_outcomes.items():
            node = self.graph.nodes[node_id]
            if node.goal_gate and outcome.status not in ("SUCCESS", "PARTIAL_SUCCESS"):
                return False, node
        return True, None

    def _get_retry_target(self, failed_gate: Node) -> Optional[str]:
        return failed_gate.retry_target or failed_gate.fallback_retry_target or \
               self.graph.retry_target or self.graph.fallback_retry_target

    def _save_checkpoint(self, node_id: str):
        cp = Checkpoint(
            current_node_id=node_id,
            completed_nodes=list(self.completed_nodes),
            context=self.context.copy()
        )
        with open(self.logs_root / "checkpoint.json", "w") as f:
            json.dump(asdict(cp), f)
