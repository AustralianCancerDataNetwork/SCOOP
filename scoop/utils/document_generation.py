import yaml
from typing import Dict, List
from scoop.configuration.transformation_graph import (
    TransformationGraph, 
    NumericNode, 
    CategoricalNode,
    ConditionalDiscreteDistribution, 
    DiscreteDistribution,
    NumericRangeNode,
    BranchNode
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
    explicit_edges = graph.edges


    implicit_edges = []
    for name, node in nodes.items():
        if isinstance(node, NumericRangeNode):
            if node.from_ not in nodes:
                raise ValueError(
                    f"Range node '{name}' refers to unknown upstream node '{node.from_}'"
                )
            implicit_edges.append(
                (
                    node.from_,
                    name,
                    "range",        # label in graph
                    None,            # no CPT summary
                    "transform", # edge type
                )
            )
    branch_edges = []

    for name, node in nodes.items():
        if isinstance(node, BranchNode):
            upstreams = set()

            for rule in node.rules:
                cond = rule.get("if", {})
                for upstream in cond.keys():
                    if upstream not in nodes:
                        raise ValueError(
                            f"Branch node '{name}' refers to unknown node '{upstream}'"
                        )
                    upstreams.add(upstream)

            for upstream in upstreams:
                branch_edges.append(
                    (
                        upstream,
                        name,
                        "branch",
                        None,
                        "branch",   # edge kind
                    )
                )

    activation_edges = []

    for name, node in nodes.items():
        active_when = getattr(node, "active_when", None)
        if not active_when:
            continue

        for upstream in active_when.keys():
            if upstream not in nodes:
                raise ValueError(
                    f"Node '{name}' has active_when dependency on unknown node '{upstream}'"
                )

            activation_edges.append(
                (
                    upstream,
                    name,
                    "active",
                    None,
                    "activation",   # edge kind
                )
            )

    full_edges = []
    for edge in explicit_edges:
        keys = list(edge.table.keys())
        if len(keys) > 3:
            tooltip = f"{len(keys)} rows"
        else:
            tooltip = ", ".join(map(str, keys))
        label = edge.using
        full_edges.append((edge.from_, edge.to, edge.using, label, tooltip))

    combined_edges = full_edges + implicit_edges + branch_edges + activation_edges
    
    def mermaid_id(name: str) -> str:
        return name.replace(".", "_").replace("-", "_")

    def get_group(name: str) -> str:
        return name.split(".")[0] if "." in name else "root"

    # Determine connected vs disconnected
    connected = set()
    for src, dst, *_ in combined_edges:
        connected.add(src)
        connected.add(dst)

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
            shape_open = "["
            shape_close = "]"
            color = "#EEE"

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
            elif isinstance(node, NumericRangeNode):
                # Show ranges
                rngs = ", ".join(node.ranges)
                summary = f"<br/>{rngs}"
                color = "#E6C3F9"  # soft purple for derived buckets
            elif isinstance(node, BranchNode):
                shape_open = "{{"
                shape_close = "}}"
                summary = "<br/>branch"
                color = "#FFD966"
            else:
                color = "#EEE"
            
            if getattr(node, "active_when", None):
                conds = ", ".join(
                    f"{k}={v}" for k, v in node.active_when.items()
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

        
        if kind == "probabilistic":
            arrow = "-->"
        elif kind == "transform":
            arrow = "-.->"
        elif kind == "activation":
            arrow = "--o"
        else:
            arrow = "==>"

        lines.append(
            f"    {src_id} {arrow}|{edge_label}| {dst_id}"
        )


    return "\n".join(lines)