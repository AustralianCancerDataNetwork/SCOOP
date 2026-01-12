from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Dict, List, Optional, Any, Union, Annotated, Literal
import random
import yaml
from collections import defaultdict, deque
from pathlib import Path


class BaseDistribution(BaseModel):
    """Base class for all distribution types."""
    name: Optional[str] = None
    def sample(self) -> Any:
        raise NotImplementedError
    

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

class DiscreteDistribution(BaseDistribution):
    """Discrete labelled or numeric distribution - simple lookup"""

    probs: Dict[Union[int, float, str], float] = Field(
        ..., description="Mapping of label → probability (must sum to 1.0)"
    )

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

class BaseGraphNode(BaseModel):
    """Base class for all graph nodes."""

    name: Optional[str] = None
    type: str
    built_distribution: Optional[BaseDistribution] = None
    active_when: Optional[Dict[str, Any]] = None

    def node_is_active(self, sampled: Dict[str, Any]) -> bool:
        if not getattr(self, "active_when", None):
            return True
        return all(sampled.get(k) == v for k, v in self.active_when.items())


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


class NumericNode(BaseGraphNode):
    type: Literal["numeric"]

    mean: float = 0
    sd: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    as_int: bool = False

    @field_validator("sd")
    @classmethod
    def sd_positive(cls, v):
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


class CategoricalNode(BaseGraphNode):
    type: Literal["categorical"]

    # For unconditional nodes: user can specify distribution explicitly
    # For conditional nodes: distribution may be empty and labels provided instead
    distribution: Dict[Union[str, float, int], float] = Field(default_factory=dict)
    labels: Optional[List[str]] = None


class NumericRangeNode(BaseGraphNode):
    type: Literal["numeric_range"]
    from_: str = Field(..., alias="from")
    ranges: List[str]

    def sample(self, parent_values: Dict[str, Any]) -> Any:
        value = parent_values[self.from_]
        return self.assign_bucket(value)

    def assign_bucket(self, x: float) -> str:
        # parse each range like "<60", "60-69", "80+"
        for r in self.ranges:
            if self._matches_range(x, r):
                return r
        raise ValueError(f"{x} not in any defined range for {self.name}")


    def sample(self, parent_values: Dict[str, Any]) -> Any:
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

    def sample(self, parent_values: Dict[str, Any]) -> Any:
        # Simple rule language: [{"if": {"operable": True}, "then": "surgery"}, ...]
        for r in self.rules:
            cond = r["if"]
            if all(parent_values.get(k) == v for k, v in cond.items()):
                return r["then"]
        return self.default


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

        # 2. Build adjacency map
        g = defaultdict(list)
        for edge in self.edges:
            g[edge.from_].append(edge.to)
        self.graph = dict(g)

        # 3. Topological sort
        self.order = self._topological_sort()

        # 4. Assign distribution types
        for name, node in self.nodes.items():
            incoming = [e for e in self.edges if e.to == name]

            # Numeric nodes already built their distributions in model validator
            if isinstance(node, NumericNode):
                continue

            if isinstance(node, NumericRangeNode):
                incoming = [node.from_]
                self.graph.setdefault(node.from_, []).append(name)
                continue

            # Branch nodes use deterministic routing instead of probabilistic sampling
            if isinstance(node, BranchNode):
                continue
            
            if isinstance(node, CategoricalNode):
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

                    node.built_distribution = ConditionalDiscreteDistribution(
                        labels=labels,
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

    def _topological_sort(self) -> List[str]:
        indegree = {n: 0 for n in self.nodes}
        for e in self.edges:
            indegree[e.to] += 1

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
            raise ValueError("Graph contains a cycle")

        return result

    def sample_one(self) -> Dict[str, Any]:
        """
        Sample a single joint assignment for all nodes,
        respecting dependency order and conditional distributions.
        """
        sampled: Dict[str, Any] = {}

        for name in self.order:
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

    def sample_many(self, n: int) -> List[Dict[str, Any]]:
        """Convenience: draw n independent samples."""
        return [self.sample_one() for _ in range(n)]




