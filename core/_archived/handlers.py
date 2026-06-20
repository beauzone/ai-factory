from typing import Any, Dict, Optional
from model_resolver import ModelResolver
from engine import Outcome

class NodeHandler:
    """Base class for all AI Factory node handlers."""
    def execute(self, node: Any, context: Dict, graph: Any, logs_root: Any) -> Outcome:
        raise NotImplementedError("Handlers must implement execute()")

class CodergenHandler(NodeHandler):
    """The primary handler for code generation, analysis, and planning."""
    def __init__(self, launderette_client: Any, model_resolver: ModelResolver):
        self.launderette = launderette_client
        self.resolver = model_resolver

    def execute(self, node: Any, context: Dict, graph: Any, logs_root: Any) -> Outcome:
        # 1. Resolve model based on node class
        model_info = self.resolver.resolve(node.node_class)
        
        # 2. Build the launderette prompt
        # In a real implementation, this would trigger a specific script in the 
        # beau-dev-blueprint (e.g. decompose.py or review_pr.py)
        prompt = node.prompt or node.label
        
        # 3. Simulate Execution (MVP)
        # In the final version, this will call: self.launderette.run_script(script_name, prompt)
        return Outcome(
            status="SUCCESS",
            notes=f"Executed {node.id} using {model_info['model']}.",
            context_updates={f"node_{node.id}_result": "success"}
        )

class HumanWaitHandler(NodeHandler):
    """Handler for human-in-the-loop gates (shape=hexagon)."""
    def __init__(self, hermes_interface: Any):
        self.hermes = hermes_interface

    def execute(self, node: Any, context: Dict, graph: Any, logs_root: Any) -> Outcome:
        # Use Hermes clarify() to get human input
        # This blocks the execution engine until the user responds
        choice = self.hermes.clarify(
            question=f"Node {node.label}: Please provide a decision.",
            choices=["Approve", "Fix", "Reject"]
        )
        
        return Outcome(
            status="SUCCESS",
            preferred_label=choice,
            notes=f"Human chose: {choice}"
        )

class ConditionalHandler(NodeHandler):
    """Handler for routing nodes (shape=diamond)."""
    def execute(self, node: Any, context: Dict, graph: Any, logs_root: Any) -> Outcome:
        return Outcome(status="SUCCESS", notes="Routing node processed")
