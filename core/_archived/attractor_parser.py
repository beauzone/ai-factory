import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Union

@dataclass
class Edge:
    to_node: str
    label: str = ""
    condition: str = ""
    weight: int = 0
    fidelity: Optional[str] = None
    thread_id: Optional[str] = None
    loop_restart: bool = False

@dataclass
class Node:
    id: str
    label: str = ""
    shape: str = "box"
    type: str = ""
    prompt: str = ""
    max_retries: Optional[int] = None
    goal_gate: bool = False
    retry_target: Optional[str] = None
    fallback_retry_target: Optional[str] = None
    fidelity: Optional[str] = None
    thread_id: Optional[str] = None
    node_class: str = ""
    timeout: Optional[str] = None
    llm_model: Optional[str] = None
    llm_provider: Optional[str] = None
    reasoning_effort: str = "high"
    auto_status: bool = False
    allow_partial: bool = False

@dataclass
class Graph:
    goal: str = ""
    label: str = ""
    model_stylesheet: str = ""
    default_max_retries: int = 0
    retry_target: Optional[str] = None
    fallback_retry_target: Optional[str] = None
    default_fidelity: str = "compact"
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: Dict[str, List[Edge]] = field(default_factory=dict)

class AttractorParser:
    """Parses a Graphviz DOT file according to the Attractor Specification."""
    
    def __init__(self):
        self.current_graph: Optional[Graph] = None

    def parse(self, content: str) -> Graph:
        # Basic cleaning
        content = self._strip_comments(content)
        
        # Initial Graph detection
        match = re.search(r'digraph\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{', content)
        if not match:
            raise ValueError("No valid digraph definition found")
        
        graph_id = match.group(1)
        graph = Graph()
        self.current_graph = graph
        
        # Graph Attributes
        graph_attr_match = re.search(r'graph\s*\[(.*?)\]', content, re.DOTALL)
        if graph_attr_match:
            self._parse_attr_block(graph_attr_match.group(1), graph)

        # Node Definitions
        node_pattern = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*\[(.*?)\]\s*;?', re.DOTALL)
        for match in node_pattern.finditer(content):
            node_id = match.group(1)
            attr_str = match.group(2)
            self._parse_node(node_id, attr_str, graph)

        # Edge Definitions
        edge_pattern = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*->\s*([A-Za-z_][A-Za-z0-9_]*)\s*\[(.*?)\]\s*;?', re.DOTALL)
        for match in edge_pattern.finditer(content):
            from_node = match.group(1)
            to_node = match.group(2)
            attr_str = match.group(3)
            self._parse_edge(from_node, to_node, attr_str, graph)

        if not self._find_start_node(graph):
            raise ValueError("Graph must have a start node (shape=Mdiamond or id='start')")
        
        return graph

    def _strip_comments(self, content: str) -> str:
        content = re.sub(r'//.*', '', content)
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        return content

    def _parse_attr_block(self, block: str, target: Union[Graph, Node, Edge]):
        attrs = {}
        parts = []
        current = []
        in_quotes = False
        for char in block:
            if char == '"' and not (len(current) > 0 and current[-1] == '\\'):
                in_quotes = not in_quotes
            if char == ',' and not in_quotes:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(char)
        parts.append("".join(current).strip())

        for part in parts:
            if '=' not in part: continue
            k, v = part.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"')
            attrs[k] = v

        for k, v in attrs.items():
            if hasattr(target, k):
                if isinstance(getattr(target, k), bool):
                    v = v.lower() == 'true'
                elif isinstance(getattr(target, k), int):
                    v = int(v)
                elif isinstance(getattr(target, k), float):
                    v = float(v)
                setattr(target, k, v)
            elif k == 'class':
                target.node_class = v

    def _parse_node(self, node_id: str, attr_str: str, graph: Graph):
        node = Node(id=node_id)
        self._parse_attr_block(attr_str, node)
        if not node.label:
            node.label = node_id
        graph.nodes[node_id] = node

    def _parse_edge(self, from_node: str, to_node: str, attr_str: str, graph: Graph):
        edge = Edge(to_node=to_node)
        self._parse_attr_block(attr_str, edge)
        if from_node not in graph.edges:
            graph.edges[from_node] = []
        graph.edges[from_node].append(edge)

    def _find_start_node(self, graph: Graph) -> Optional[str]:
        for node_id, node in graph.nodes.items():
            if node.shape == 'Mdiamond' or node_id.lower() in ('start', 'begin'):
                return node_id
        return None
