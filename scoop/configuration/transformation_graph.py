from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Dict, List, Optional, Any, Union, Annotated, Literal, DefaultDict
import random
from collections import defaultdict, deque
from dataclasses import dataclass
from ..utils.document_generation import mermaid_html, make_mermaid

@dataclass
class GraphValidationIssue:
    level: str          # "error" | "warning"
    message: str
    context: str | None = None

    def __repr__(self) -> str:
        ctx = f" ({self.context})" if self.context else ""
        return f"[{self.level.upper()}] {self.message}{ctx}"


class BaseDistribution(BaseModel):
    """Base class for all distribution types."""
    name: Optional[str] = None
    def sample(self) -> Any:
        raise NotImplementedError
    
    def __repr__(self) -> str:
        cls = self.__class__.__name__
        name = f"name={self.name!r}" if self.name else ""
        return f"<{cls}({name})>"
    

class NumericDistribution(BaseDistribution):
    
    """
    Default Sampling logic:
    - If sd is provided → Gaussian(mean, sd) + clipping
    - If sd is missing or zero → Uniform(min_value, max_value)
    """

    mean: float = 0
    sd: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    as_int: bool = False
    kind: Literal["numeric"] = "numeric"

    def sample(self):
        # Uniform if no sd
        if not self.sd:
            if self.min_value is None or self.max_value is None:
                raise ValueError(f"Uniform sampling requires min_value and max_value: {self.name}")
            val = random.uniform(self.min_value, self.max_value)
        else:
            val = random.gauss(self.mean, self.sd)

        # clip
        if self.min_value is not None:
            val = max(self.min_value, val)
        if self.max_value is not None:
            val = min(self.max_value, val)

        return int(val) if self.as_int else val
    

    def __repr__(self) -> str:
        parts = [f"mean={self.mean}"]
        if self.sd is not None:
            parts.append(f"sd={self.sd}")
        if self.min_value is not None or self.max_value is not None:
            parts.append(f"range=[{self.min_value},{self.max_value}]")
        if self.as_int:
            parts.append("int")

        name = f"{self.name}: " if self.name else ""
        return f"<NumericDistribution {name}{', '.join(parts)}>"


class DiscreteDistribution(BaseDistribution):
    """Discrete labelled or numeric distribution - simple lookup"""

    probs: Dict[Union[int, float, str], float] = Field(
        ..., description="Mapping of label → probability (must sum to 1.0)"
    )
    kind: Literal["discrete"] = "discrete"

    @field_validator("probs")
    @classmethod
    def validate_probs(cls, v: Dict[Union[int, float, str], float]):
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Probabilities must sum to 1 (got {total}) for {cls.name}")
        return v

    def sample(self) -> Union[int, float, str]:
        vals, weights = zip(*self.probs.items())
        return random.choices(vals, weights=weights, k=1)[0] 
    

    def __repr__(self) -> str:
        n = len(self.probs)
        preview = list(self.probs.keys())[:3]
        suffix = "…" if n > 3 else ""
        name = f"{self.name}: " if self.name else ""
        return f"<DiscreteDistribution {name}{n} cats [{', '.join(map(str, preview))}{suffix}]>"


class ConditionalDiscreteDistribution(BaseDistribution):
    """
    A distribution that depends on parent node values.

    It doesn't hold a single fixed prob vector, but rather:
    - labels: ordered list of outcome labels for the child node
    - incoming_edges: GraphEdges with CPT rows

    At sample time, we call sample_given(parent_values) to:
    - look up each parent’s row in its table
    - combine rows multiplicatively
    - normalize
    - draw a sample
    """

    labels: List[str]
    incoming_edges: List[GraphEdge]
    kind: Literal["conditional_discrete"] = "conditional_discrete"

    def get_probs(self, parent_values: Dict[str, Any]) -> Dict[str, float]:
        n = len(self.labels)
        probs = [1.0] * n  # neutral element for multiplicative fusion

        active_edges = [e for e in self.incoming_edges if e.is_active(parent_values)]
        if not active_edges:
            raise ValueError(
                f"No active parent edges for conditional distribution {self.name} "
                f"given parent values {parent_values}"
            )

        for edge in active_edges:
            parent_value = parent_values.get(edge.from_)

            row = edge.table.get(parent_value, edge.table.get("default"))
            if row is None:
                raise ValueError(
                    f"No row in CPT for parent={edge.from_}, value={parent_value}"
                )
            if len(row) != n:
                raise ValueError(
                    f"CPT row length ({len(row)}) does not match number of labels ({n} in node {self.name})"
                    f"for child of edge {edge.from_} -> {edge.to}"
                )

            probs = [a * b for a, b in zip(probs, row)]

        total = sum(probs)
        if total <= 0:
            raise ValueError(
                "Conditional probabilities collapsed to zero. "
                "Check CPTs and parent assignments."
            )

        probs = [p / total for p in probs]
        return dict(zip(self.labels, probs))

    def sample_given(self, parent_values: Dict[str, Any]) -> Any:
        probs = self.get_probs(parent_values)
        vals, weights = zip(*probs.items())
        return random.choices(vals, weights=weights, k=1)[0]

    def sample(self) -> Any:
        # Not meaningful without parent values
        raise RuntimeError("Use sample_given(parent_values) for conditional distributions.")
    

    def __repr__(self) -> str:
        parents = sorted({e.from_ for e in self.incoming_edges})
        name = f"{self.name}: " if self.name else ""
        return (
            f"<ConditionalDiscreteDistribution {name}"
            f"labels={len(self.labels)}, parents={parents}>"
        )


class GraphEdge(BaseModel):
    from_: str = Field(..., alias="from")
    to: str
    using: str
    table: Dict[Any, List[float]]
    when: Optional[Dict[str, Any]] = None  # e.g. {"path": "surgery"}

    def is_active(self, parent_values: Dict[str, Any]) -> bool:
        if not self.when:
            return True
        for k, v in self.when.items():
            if parent_values.get(k) != v:
                return False
        return True

    def __repr__(self) -> str:
        cond = f", when={self.when}" if self.when else ""
        return f"<Edge {self.from_} -> {self.to} using={self.using}{cond}>"

    def to_html(self) -> str:
        when = (
            f"<br><i>when:</i> {self.when}"
            if self.when else ""
        )

        return f"""
        <div class="tg-edge">
          <b>{self.from_}</b> → <b>{self.to}</b><br>
          using: {self.using}
          {when}
        </div>
        """
    

Distribution = Annotated[
    Union[
        NumericDistribution,
        DiscreteDistribution,
        ConditionalDiscreteDistribution,
    ],
    Field(discriminator="kind"),
]


class BaseGraphNode(BaseModel):
    """Base class for all graph nodes."""

    name: Optional[str] = None
    built_distribution: Optional[Distribution] = None
    active_when: Optional[Dict[str, Any]] = None

    def node_is_active(self, sampled: Dict[str, Any]) -> bool:
        active_when = self.active_when
        if not active_when:
            return True
        return all(sampled.get(k) == v for k, v in active_when.items())

    def sample(self, parent_values: Optional[Dict[str, Any]] = None) -> Any:
        if not self.built_distribution:
            raise ValueError(f"Distribution not built yet for node {self.name}")

        if isinstance(self.built_distribution, ConditionalDiscreteDistribution):
            if parent_values is None:
                raise ValueError(
                    f"Node {self.name} has conditional distribution; parent values required."
                )
            return self.built_distribution.sample_given(parent_values)
        else:
            return self.built_distribution.sample()
    
    def to_html(self) -> str:
        name = self.name or "<?>"
        cls = self.__class__.__name__.lower()
        active = (
            f"<span style='color:#666'>(active_when)</span>"
            if self.active_when else
            "<span style='color:#666'>(always active)</span>"
        )

        dist = (
            self.built_distribution.__class__.__name__
            if self.built_distribution else
            "unbuilt"
        )

        return f"""
        <div class="tg-node tg-node-{cls}">
          <b>{name}</b>
          <div class="tg-node-meta">
            type: {self.__class__.__name__}<br>
            distribution: {dist}<br>
            {active}
          </div>
        </div>
        """
    
    def __repr__(self) -> str:
        cls = self.__class__.__name__
        name = self.name or "<?>"
        active = "active_when" if self.active_when else "always_active"
        dist = (
            repr(self.built_distribution)
            if self.built_distribution
            else "unbuilt"
        )
        return f"<{cls} {name} ({active}) {dist}>"


class NumericNode(BaseGraphNode):
    type: Literal["numeric"]

    mean: float = 0
    sd: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    as_int: bool = False

    @field_validator("sd")
    @classmethod
    def sd_positive(cls, v: float | None) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("'sd' must be positive.")
        return v

    @model_validator(mode="after")
    def build_numeric_distribution(self):
        self.built_distribution = NumericDistribution(
            mean=self.mean,
            sd=self.sd,
            min_value=self.min_value,
            max_value=self.max_value,
            as_int=self.as_int,
        )
        return self
    

    def __repr__(self) -> str:
        name = self.name or "<?>"
        return (
            f"<NumericNode {name} "
            f"mean={self.mean}, sd={self.sd}, "
            f"range=[{self.min_value},{self.max_value}], int={self.as_int}>"
        )

Label = Union[str, float, int]

def empty_distribution() -> Dict[Label, float]:
    return {}

class CategoricalNode(BaseGraphNode):
    type: Literal["categorical"]

    # For unconditional nodes: user can specify distribution explicitly
    # For conditional nodes: distribution may be empty and labels provided instead
    distribution: Dict[Label, float] = Field(default_factory=empty_distribution)
    labels: Optional[List[str]] = None

    def __repr__(self) -> str:
        name = self.name or "<?>"
        if self.built_distribution:
            dist = repr(self.built_distribution)
        elif self.distribution:
            dist = f"explicit {len(self.distribution)} cats"
        elif self.labels:
            dist = f"labels={self.labels}"
        else:
            dist = "unconfigured"

        active = f", active_when={self.active_when}" if self.active_when else ""
        return f"<CategoricalNode {name} {dist}{active}>"


class NumericRangeNode(BaseGraphNode):
    type: Literal["numeric_range"]
    from_: str = Field(..., alias="from")
    ranges: List[str]

    # def sample(self, parent_values: Dict[str, Any]) -> Any:
    #     value = parent_values[self.from_]
    #     return self.assign_bucket(value)

    def assign_bucket(self, x: float) -> str:
        # parse each range like "<60", "60-69", "80+"
        for r in self.ranges:
            if self._matches_range(x, r):
                return r
        raise ValueError(f"{x} not in any defined range for {self.name}")
    

    def __repr__(self) -> str:
        name = self.name or "<?>"
        return (
            f"<NumericRangeNode {name} from={self.from_} "
            f"ranges={self.ranges}>"
        )


    def sample(self, parent_values: Optional[Dict[str, Any]] = None) -> Any:
        if parent_values is None:
            raise ValueError("NumericRangeNode requires parent_values to sample.")
        if self.from_ not in parent_values:
            raise ValueError(
                f"NumericRangeNode '{self.name}' requires upstream value '{self.from_}'. "
                f"Parent values provided: {list(parent_values.keys())}"
            )
        x = parent_values[self.from_]
        bucket = self.assign_bucket(x)
        return bucket


    @staticmethod
    def _matches_range(x: float, spec: str) -> bool:
        """
        Supported forms:
            "<60"       → x < 60
            ">80"       → x > 80
            "<=70"      → x <= 70
            ">=85"      → x >= 85
            "60-69"     → 60 <= x <= 69
            "70+"       → x >= 70
            ">=70"      → x >= 70  (equivalent)
            ">70"       → x > 70
        """
        spec = f'{spec}'.strip().lower()

        # 1) inequality form: <, <=, >, >=
        if spec.startswith("<="):
            bound = float(spec[2:])
            return x <= bound

        if spec.startswith("<"):
            bound = float(spec[1:])
            return x < bound

        if spec.startswith(">="):
            bound = float(spec[2:])
            return x >= bound

        if spec.startswith(">"):
            bound = float(spec[1:])
            return x > bound

        # 2) "number+" form → x >= number
        if spec.endswith("+"):
            bound = float(spec[:-1])
            return x >= bound

        # 3) range form: "a-b"
        if "-" in spec:
            lo_str, hi_str = spec.split("-", 1)
            lo = float(lo_str)
            hi = float(hi_str)
            return lo <= x <= hi

        # If it's just a raw number, treat as equality
        # (rare but possible — could be useful if upstream values are discrete years)
        try:
            val = float(spec)
            return x == val
        except ValueError:
            pass

        raise ValueError(f"Unsupported numeric range syntax: '{spec}'")

class BranchNode(BaseGraphNode):
    type: Literal["branch"]
    from_: List[str] = Field(default_factory=list)   # which upstream vars it reads
    rules: List[Dict[str, Any]]                      # ordered rules
    default: str


    def sample(self, parent_values: Optional[Dict[str, Any]] = None) -> Any:
        if parent_values is None:
            raise ValueError("BranchNode requires parent_values to sample.")        
        # Simple rule language: [{"if": {"operable": True}, "then": "surgery"}, ...]
        for r in self.rules:
            cond = r["if"]
            if all(parent_values.get(k) == v for k, v in cond.items()):
                return r["then"]
        return self.default

    def __repr__(self) -> str:
        name = self.name or "<?>"
        rules_n = len(self.rules)
        return (
            f"<BranchNode {name} "
            f"inputs={self.from_}, rules={rules_n}, default={self.default}>"
        )

GraphNode = Annotated[
    Union[NumericNode, CategoricalNode, NumericRangeNode, BranchNode],
    Field(discriminator="type"),
]

class TransformationGraph(BaseModel):
    """
    A directed acyclic graph of GraphNodes and GraphEdges.
    Handles:
    - building adjacency
    - topological ordering
    - assigning correct distribution types (unconditional vs conditional)
    - sampling in dependency order
    """

    nodes: Dict[str, GraphNode]
    edges: List[GraphEdge]

    order: List[str] = Field(default_factory=list)
    graph: Dict[str, List[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def build_graph_and_order(self):
        # 1. Assign names to nodes based on dict keys
        for name, node in self.nodes.items():
            node.name = name
            if node.built_distribution:
                node.built_distribution.name = name 

        # 2. Build dependency graph used for ordering.
        self.graph = self._build_dependency_graph()

        # 3. Topological sort
        self.order = self._topological_sort()

        # 4. Assign distribution types
        for name, node in self.nodes.items():
            incoming = [e for e in self.edges if e.to == name]

            # Numeric nodes already built their distributions in model validator
            if isinstance(node, NumericNode):
                continue

            if isinstance(node, NumericRangeNode):
                continue

            # Branch nodes use deterministic routing instead of probabilistic sampling
            if isinstance(node, BranchNode):
                continue
            
            else: # CategoricalNode
                if incoming:
                    # Conditional categorical node
                    # Decide label order: prefer explicit labels, else from any CPT row length
                    if node.labels:
                        labels = node.labels
                    elif node.distribution:
                        labels = list(node.distribution.keys())
                    else:
                        # Try infer from first edge row
                        first_edge = incoming[0]
                        some_row = next(iter(first_edge.table.values()))
                        if node.labels and len(node.labels) != len(some_row):
                            raise ValueError(
                                f"Label count for node {name} does not match CPT row length."
                            )
                        if node.labels:
                            labels = node.labels
                        else:
                            # create dummy labels if absolutely necessary
                            labels = [f"cat_{i}" for i in range(len(some_row))]
                    label_str: list[str] = [str(l) for l in labels]
                    node.built_distribution = ConditionalDiscreteDistribution(
                        labels=label_str,
                        incoming_edges=incoming,
                    )
                else:
                    # Unconditional categorical node
                    if not node.distribution:
                        # If we have labels but no probs, use equal probabilities
                        if node.labels:
                            n = len(node.labels)
                            node.distribution = {label: 1.0 / n for label in node.labels}
                        else:
                            raise ValueError(
                                f"Unconditional categorical node '{name}' "
                                f"must have either 'distribution' or 'labels'."
                            )

                    node.built_distribution = DiscreteDistribution(
                        probs=node.distribution
                    )
        return self

    def _build_dependency_graph(self) -> Dict[str, List[str]]:
        graph_sets: DefaultDict[str, set[str]] = defaultdict(set)

        def add_dependency(
            source: str,
            target: str,
            reason: str,
            *,
            allow_external_source: bool = False,
        ) -> None:
            if source not in self.nodes:
                if allow_external_source:
                    return
                raise ValueError(
                    f"{reason} references unknown node '{source}' for '{target}'"
                )
            if target not in self.nodes:
                raise ValueError(
                    f"{reason} references unknown node '{target}'"
                )
            graph_sets[source].add(target)

        for edge in self.edges:
            add_dependency(edge.from_, edge.to, "Edge")
            if edge.when:
                for source in edge.when:
                    add_dependency(
                        source,
                        edge.to,
                        "Edge condition",
                        allow_external_source=True,
                    )

        for name, node in self.nodes.items():
            if isinstance(node, NumericRangeNode):
                add_dependency(node.from_, name, "Numeric range")

            if node.active_when:
                for source in node.active_when:
                    add_dependency(
                        source,
                        name,
                        "active_when",
                        allow_external_source=True,
                    )

            if isinstance(node, BranchNode):
                branch_inputs: list[str] = []
                branch_inputs.extend(node.from_)
                for rule in node.rules:
                    branch_inputs.extend(rule.get("if", {}).keys())

                # Preserve first-seen order for display/debugging.
                deduped_inputs = list(dict.fromkeys(branch_inputs))
                node.from_ = deduped_inputs
                for source in deduped_inputs:
                    add_dependency(
                        source,
                        name,
                        "Branch rule",
                        allow_external_source=True,
                    )

        return {source: sorted(targets) for source, targets in graph_sets.items()}

    def _topological_sort(self) -> List[str]:
        indegree = {n: 0 for n in self.nodes}
        for _, targets in self.graph.items():
            for target in targets:
                indegree[target] += 1

        q = deque([n for n, d in indegree.items() if d == 0])
        result: List[str] = []

        while q:
            n = q.popleft()
            result.append(n)
            for nxt in self.graph.get(n, []):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    q.append(nxt)

        if len(result) != len(self.nodes):
            # Note: This can trigger in circumstances where the issue is NOT
            # the presence of a cycle but rather issues in graph incompleteness
            # E.g. had dependencies on biomarker node, no edge leading to
            # biomarker node. Triggered this. 
            remaining = sorted(n for n, d in indegree.items() if d > 0)
            raise ValueError(
                "Graph contains a cycle in explicit or implicit dependencies. "
                f"Unresolved nodes: {remaining}"
            )

        return result

    def sample_one(
        self,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Sample a single joint assignment for all nodes,
        respecting dependency order and conditional distributions.

        If ``initial_state`` provides values for some nodes, those values are
        treated as inherited upstream state and are not resampled.
        """
        sampled: Dict[str, Any] = dict(initial_state or {})

        for name in self.order:
            if name in sampled:
                continue

            node = self.nodes[name]

            if not node.node_is_active(sampled):
                sampled[name] = None
                continue

            if isinstance(node, BranchNode):
                sampled[name] = node.sample(parent_values=sampled)
            elif isinstance(node.built_distribution, ConditionalDiscreteDistribution):
                sampled[name] = node.sample(parent_values=sampled)
            elif isinstance(node, NumericRangeNode):
                sampled[name] = node.sample(parent_values=sampled)
            else:
                sampled[name] = node.sample()
            
        return sampled

    def sample_many(
        self,
        n: int,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Convenience: draw n independent samples."""
        return [self.sample_one(initial_state=initial_state) for _ in range(n)]
    

    def describe(self) -> str:
        """
        Return a human-readable multi-line summary of the graph.
        """
        lines: list[str] = []

        lines.append(repr(self))
        lines.append("")

        # Node summary
        lines.append("Nodes:")
        for name in self.order:
            node = self.nodes[name]
            lines.append(f"  - {repr(node)}")

        # Edge summary
        if self.edges:
            lines.append("")
            lines.append("Edges:")
            for edge in self.edges:
                lines.append(f"  - {repr(edge)}")

        return "\n".join(lines)
    

    def __repr__(self) -> str:
        node_types: Dict[str, int] = {}
        for n in self.nodes.values():
            t = n.__class__.__name__
            node_types[t] = node_types.get(t, 0) + 1

        parts = ", ".join(f"{k}={v}" for k, v in sorted(node_types.items()))

        return (
            f"<TransformationGraph "
            f"nodes={len(self.nodes)}, "
            f"edges={len(self.edges)}, "
            f"order_len={len(self.order)}, "
            f"{parts}>"
        )
    
    def __str__(self) -> str:
        return f"TransformationGraph({len(self.nodes)} nodes, {len(self.edges)} edges)"



    def _nodes_html(self) -> str:
        items = "\n".join(
            node.to_html()
            for node in self.nodes.values()
        )
        return f"""
        <div class="tg-section">
          <h4>Nodes</h4>
          {items}
        </div>
        """
    

    def _edges_html(self) -> str:
        if not self.edges:
            return "<i>No edges</i>"

        items = "\n".join(
            edge.to_html()
            for edge in self.edges
        )
        return f"""
        <div class="tg-section">
          <h4>Edges</h4>
          {items}
        </div>
        """

    def _repr_html_(self) -> str:
        return f"""
        <style>
            .tg-container {{
                font-family: sans-serif;
                background: white !important;
                color: #111 !important;
                padding: 0.75em;
                border-radius: 6px;
            }}

            .tg-container * {{
                color: inherit;
            }}
          .tg-section {{
            margin-top: 1em;
          }}
          .tg-node, .tg-edge {{
            padding: 0.4em 0.6em;
            margin: 0.3em 0;
            border-left: 3px solid #ccc;
            background: #fafafa;
          }}
          .tg-node-meta {{
            font-size: 0.9em;
            color: #444;
          }}
        .tg-node {{ padding: 0.4em 0.6em; margin: 0.3em 0; background: #fafafa; }}
        .tg-node-numericnode {{ border-left: 4px solid #6baed6; }}
        .tg-node-categoricalnode {{ border-left: 4px solid #74c476; }}
        .tg-node-numericrangenode {{ border-left: 4px solid #9e9ac8; }}
        .tg-node-branchnode {{ border-left: 4px solid #fd8d3c; }}
        </style>

        <div class="tg-container">
          <h3>TransformationGraph</h3>
          <div>
            <b>Nodes:</b> {len(self.nodes)}<br>
            <b>Edges:</b> {len(self.edges)}
          </div>

          {self._nodes_html()}
          {self._edges_html()}
        </div>
        """     
    # def mermaid_html(mermaid_source: str) -> str:


    def to_mermaid(self) -> str:
        mmd = make_mermaid(self)

        return f"""
<style>
    .mermaid {{
        font-family: sans-serif;
        background: white !important;
        color: #111 !important;
        padding: 0.75em;
        border-radius: 6px;
    }}
</style>

<div class="mermaid">
{mmd}
</div>

<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
mermaid.initialize({{
    startOnLoad: false,
    theme: "default",
    securityLevel: "loose"
}});
mermaid.run();
</script>
"""
    
    def show(self) -> None:
        from IPython.display import HTML, display # type: ignore
        display(HTML(mermaid_html(self.to_mermaid())))


    def validate_graph(self, *, strict: bool = False) -> list[GraphValidationIssue]:
        issues: list[GraphValidationIssue] = []

        # 1. Check all nodes in order
        for name in self.order:
            node = self.nodes[name]

            if node.built_distribution is None and not isinstance(node, BranchNode):
                issues.append(
                    GraphValidationIssue(
                        "error",
                        "Node has no built distribution",
                        context=repr(node),
                    )
                )

        # 2. Check edges reference valid nodes
        for edge in self.edges:
            if edge.from_ not in self.nodes:
                issues.append(
                    GraphValidationIssue(
                        "error",
                        f"Edge references missing source node '{edge.from_}'",
                        context=repr(edge),
                    )
                )
            if edge.to not in self.nodes:
                issues.append(
                    GraphValidationIssue(
                        "error",
                        f"Edge references missing target node '{edge.to}'",
                        context=repr(edge),
                    )
                )

        # 3. Conditional distributions sanity check
        for node in self.nodes.values():
            dist = node.built_distribution
            if isinstance(dist, ConditionalDiscreteDistribution):
                if not dist.incoming_edges:
                    issues.append(
                        GraphValidationIssue(
                            "error",
                            "Conditional distribution has no incoming edges",
                            context=repr(node),
                        )
                    )

        # 4. Active_when references
        for node in self.nodes.values():
            if node.active_when:
                for k in node.active_when:
                    if k not in self.nodes:
                        issues.append(
                            GraphValidationIssue(
                                "warning",
                                f"active_when references inherited node '{k}'",
                                context=repr(node),
                            )
                        )

        # 5. Branch rule references
        for node in self.nodes.values():
            if isinstance(node, BranchNode):
                for k in node.from_:
                    if k not in self.nodes:
                        issues.append(
                            GraphValidationIssue(
                                "warning",
                                f"branch rule references inherited node '{k}'",
                                context=repr(node),
                            )
                        )

        if strict and any(i.level == "error" for i in issues):
            msg = "\n".join(repr(i) for i in issues)
            raise ValueError(f"Graph validation failed:\n{msg}")

        return issues
