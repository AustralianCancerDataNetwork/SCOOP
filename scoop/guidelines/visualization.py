from __future__ import annotations
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scoop.guidelines.graph import (
        TransformationGraph, 
        # NumericNode, 
        # CategoricalNode,
        # ConditionalDiscreteDistribution, 
        # DiscreteDistribution,
        # NumericRangeNode,
        # BranchNode,
        GraphNode,
        GraphEdge
    )
from typing import (
    Any,
    Dict,
    List,
    Tuple,
    Optional,
)

def mermaid_html(mermaid_source: str) -> str:
    return f"""
<div class="mermaid">
{mermaid_source}
</div>

<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{
    startOnLoad: true,
    theme: "default",
    securityLevel: "loose"
  }});
</script>
"""

class MermaidEdgeKind(StrEnum):
    PROBABILISTIC = "probabilistic"
    TRANSFORM = "transform"
    BRANCH = "branch"
    ACTIVATION = "activation"

MermaidEdge = Tuple[
    str,              # src
    str,              # dst
    str,              # label
    Optional[str],    # tooltip
    MermaidEdgeKind,  # kind
]


def make_mermaid(graph: "TransformationGraph") -> str:
    """
    Generate a Mermaid flowchart from a TransformationGraph.

    Features:
    - Disconnected nodes first, then connected (topologically sorted)
    - Subgraphs by prefix (segment before '.')
    - Color coding:
        numeric = blue
        categorical = green
    - Edge labels show 'using' and a compact CPT summary
    """

    nodes: Dict[str, "GraphNode"] = graph.nodes
    explicit_edges: List["GraphEdge"] = graph.edges


    implicit_edges: List[MermaidEdge] = []
    external_nodes: set[str] = set()
    for name, node in nodes.items():
        if node.type=="numeric_range":
            if node.from_ not in nodes:
                external_nodes.add(node.from_)
            implicit_edges.append(
                (
                    node.from_,
                    name,
                    "range",        # label in graph
                    None,           # no CPT summary
                    MermaidEdgeKind.TRANSFORM,
                )
            )
    branch_edges: List[MermaidEdge] = []

    for name, node in nodes.items():
        if node.type == "branch":
            upstreams: set[str] = set()

            for rule in node.rules:
                cond: Dict[str, Any] = rule.get("if", {})
                for upstream in cond.keys():
                    if upstream not in nodes:
                        external_nodes.add(upstream)
                    upstreams.add(upstream)

            for upstream in upstreams:
                branch_edges.append(
                    (
                        upstream,
                        name,
                        "branch",
                        None,
                        MermaidEdgeKind.BRANCH,
                    )
                )

    activation_edges: List[MermaidEdge] = []

    for name, node in nodes.items():
        active_when = getattr(node, "active_when", None)
        if not active_when:
            continue

        for upstream in active_when.keys():
            if upstream not in nodes:
                external_nodes.add(upstream)

            activation_edges.append(
                (
                    upstream,
                    name,
                    "active",
                    None,
                        MermaidEdgeKind.ACTIVATION,
                )
            )

    full_edges: List[MermaidEdge] = []
    for edge in explicit_edges:
        keys = list(edge.table.keys())
        tooltip: Optional[str]
        if len(keys) > 3:
            tooltip = f"{len(keys)} rows"
        else:
            tooltip = ", ".join(map(str, keys))
        label = edge.using
        full_edges.append(
            (edge.from_, edge.to, edge.using, tooltip, MermaidEdgeKind.PROBABILISTIC)
        )

    combined_edges = full_edges + implicit_edges + branch_edges + activation_edges
    
    def mermaid_id(name: str) -> str:
        return name.replace(".", "_").replace("-", "_")

    def get_group(name: str) -> str:
        return name.split(".")[0] if "." in name else "root"

    # Determine connected vs disconnected
    connected: set[str] = set()
    for src, dst, *_ in combined_edges:
        connected.add(src)
        connected.add(dst)

    all_nodes = set(nodes.keys()) | external_nodes
    disconnected = sorted(all_nodes - connected)

    # Topological part for connected nodes
    connected_sorted = [n for n in graph.order if n in connected]
    connected_sorted = sorted(external_nodes & connected) + connected_sorted

    ordered_nodes = disconnected + connected_sorted

    # Group by prefix
    groups: Dict[str, List[str]] = {}
    for name in ordered_nodes:
        grp = get_group(name)
        groups.setdefault(grp, []).append(name)

    lines: List[str] = ["flowchart LR\n"]

    # Render groups and nodes
    for group_name, group_nodes in groups.items():
        if group_name != "root":
            lines.append(f"    subgraph {group_name}")
        else:
            lines.append("    %% root nodes (no prefix)")

        for name in group_nodes:
            node = nodes.get(name)
            node_id = mermaid_id(name)

            # Node summary + color
            summary = ""
            shape_open = "["
            shape_close = "]"
            color = "#EEE"

            if node is None:
                summary = "<br/>inherited"
                shape_open = "[/"
                shape_close = "/]"
                color = "#F5F5F5"
            elif node.type=="numeric":
                summary = f"<br/>N({node.mean}, {node.sd})"
                color = "#A3C4FF"  # soft blue
            elif node.type=="categorical":
                if node.built_distribution.kind == 'discrete': #type: ignore
                    cats = list(node.built_distribution.probs.keys()) #type: ignore
                    if len(cats) <= 4:
                        summary = "<br/>" + ", ".join(map(str, cats))
                    else:
                        summary = f"<br/>{len(cats)} categories"
                elif node.built_distribution.kind == 'conditional_discrete': #type: ignore
                    summary = "<br/>conditional"
                else:
                    summary = ""
                color = "#C3F9C3"  # soft green
            elif node.type=="numeric_range":
                # Show ranges
                rngs = ", ".join(node.ranges)
                summary = f"<br/>{rngs}"
                color = "#E6C3F9"  # soft purple for derived buckets
            else: # BranchNode or other
                shape_open = "{{"
                shape_close = "}}"
                summary = "<br/>branch"
                color = "#FFD966"
            
            active = getattr(node, "active_when", None) if node is not None else None
            if active:
                conds = ", ".join(
                    f"{k}={v}" for k, v in active.items()
                )
                summary += f"<br/><i>active if:</i> {conds}"


            lines.append(
                f'        {node_id}{shape_open}"{name}{summary}"{shape_close}:::node_{node_id}'
            )
            lines.append(
                f'        classDef node_{node_id} fill:{color},stroke:#333,stroke-width:1px;'
            )

        if group_name != "root":
            lines.append("    end\n")
        else:
            lines.append("")

    for src, dst, label, tooltip, kind in combined_edges:
        src_id = mermaid_id(src)
        dst_id = mermaid_id(dst)

        if tooltip:
            edge_label = f"{label} #40;{tooltip}#41;"
        else:
            edge_label = label

        
        if kind == MermaidEdgeKind.PROBABILISTIC:
            arrow = "-->"
        elif kind == MermaidEdgeKind.TRANSFORM:
            arrow = "-.->"
        elif kind == MermaidEdgeKind.ACTIVATION:
            arrow = "--o"
        else:
            arrow = "==>"

        lines.append(
            f"    {src_id} {arrow}|{edge_label}| {dst_id}"
        )


    return "\n".join(lines)
