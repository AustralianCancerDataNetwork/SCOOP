import yaml
from typing import Dict, List
from scoop.configuration.transformation_graph import (
    TransformationGraph, 
    NumericNode, 
    CategoricalNode,
    ConditionalDiscreteDistribution, 
    DiscreteDistribution
)

def make_mermaid(graph: TransformationGraph) -> str:
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

    nodes = graph.nodes
    edges = graph.edges

    def mermaid_id(name: str) -> str:
        return name.replace(".", "_").replace("-", "_")

    def get_group(name: str) -> str:
        return name.split(".")[0] if "." in name else "root"

    # Determine connected vs disconnected
    connected = set()
    for e in edges:
        connected.add(e.from_)
        connected.add(e.to)

    all_nodes = set(nodes.keys())
    disconnected = sorted(all_nodes - connected)

    # Topological part for connected nodes
    connected_sorted = [n for n in graph.order if n in connected]

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
            node = nodes[name]
            node_id = mermaid_id(name)

            # Node summary + color
            summary = ""
            if isinstance(node, NumericNode):
                summary = f"<br/>N({node.mean}, {node.sd})"
                color = "#A3C4FF"  # soft blue
            elif isinstance(node, CategoricalNode):
                if isinstance(node.built_distribution, DiscreteDistribution):
                    cats = list(node.built_distribution.probs.keys())
                    if len(cats) <= 4:
                        summary = "<br/>" + ", ".join(map(str, cats))
                    else:
                        summary = f"<br/>{len(cats)} categories"
                elif isinstance(node.built_distribution, ConditionalDiscreteDistribution):
                    summary = "<br/>conditional"
                else:
                    summary = ""
                color = "#C3F9C3"  # soft green
            else:
                color = "#EEE"

            lines.append(
                f'        {node_id}["{name}{summary}"]:::coloredNode_{node_id}'
            )
            lines.append(
                f'        classDef coloredNode_{node_id} fill:{color},stroke:#333,stroke-width:1px;'
            )

        if group_name != "root":
            lines.append("    end\n")
        else:
            lines.append("")

    # Render edges
    for edge in edges:
        src = mermaid_id(edge.from_)
        dst = mermaid_id(edge.to)

        # Compact tooltip for CPT
        keys = list(edge.table.keys())
        if len(keys) > 3:
            tooltip = f"{len(keys)} rows"
        else:
            tooltip = ", ".join(map(str, keys))

        label = edge.using
        if tooltip:
            label += f" #40;{tooltip}#41;"

        lines.append(f'    {src} -->|{label}| {dst}')

    return "\n".join(lines)