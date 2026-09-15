"""Biological knowledge graph construction for CellMinerCDB observations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import networkx as nx
import pandas as pd

MOLECULAR_TYPES = {"exp": "EXPRESSION", "mut": "MUTATION", "cop": "COPY_NUMBER"}
ACTIVITY_TYPES = {"act": "DRUG_ACTIVITY"}


def _clean(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _node_id(kind: str, value: str) -> str:
    return f"{kind}:{value}"


@dataclass
class PharmacogenomicGraph:
    """Typed NetworkX graph linking genes, drugs, cell lines, tissues and datasets."""

    graph: nx.MultiDiGraph

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> "PharmacogenomicGraph":
        required = {"dataset", "data_type", "row_name", "col_name", "value"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        graph = nx.MultiDiGraph()
        for record in frame.to_dict("records"):
            dataset = _clean(record.get("dataset"))
            dtype = (_clean(record.get("data_type")) or "").lower()
            feature = _clean(record.get("row_name"))
            cell = _clean(record.get("col_name"))
            tissue = _clean(record.get("cell_line_tissue"))
            value = record.get("value")
            if not dataset or not feature or not cell:
                continue

            dataset_id = _node_id("dataset", dataset)
            cell_id = _node_id("cell_line", cell)
            graph.add_node(dataset_id, kind="dataset", label=dataset)
            graph.add_node(cell_id, kind="cell_line", label=cell)
            graph.add_edge(cell_id, dataset_id, relation="MEASURED_IN")

            if tissue:
                tissue_id = _node_id("tissue", tissue)
                graph.add_node(tissue_id, kind="tissue", label=tissue)
                graph.add_edge(cell_id, tissue_id, relation="HAS_TISSUE")

            edge_attrs = {"dataset": dataset, "data_type": dtype, "value": value}
            if dtype in MOLECULAR_TYPES:
                gene_id = _node_id("gene", feature)
                graph.add_node(gene_id, kind="gene", label=feature)
                graph.add_edge(gene_id, cell_id, relation=MOLECULAR_TYPES[dtype], **edge_attrs)
            elif dtype in ACTIVITY_TYPES:
                drug_id = _node_id("drug", feature)
                graph.add_node(drug_id, kind="drug", label=feature,
                               moa=_clean(record.get("drug_moa")),
                               synonyms=_clean(record.get("drug_synonyms")))
                graph.add_edge(drug_id, cell_id, relation=ACTIVITY_TYPES[dtype], **edge_attrs)
            else:
                feature_id = _node_id("feature", feature)
                graph.add_node(feature_id, kind="feature", label=feature)
                graph.add_edge(feature_id, cell_id, relation="MEASUREMENT", **edge_attrs)

        return cls(graph)

    def entities(self, kinds: Iterable[str] | None = None) -> list[dict]:
        allowed = set(kinds) if kinds else None
        return [dict(node_id=node_id, **attrs) for node_id, attrs in self.graph.nodes(data=True)
                if allowed is None or attrs.get("kind") in allowed]

    def resolve(self, text: str, kinds: Iterable[str] | None = None) -> list[str]:
        """Resolve entity labels and synonyms by case-insensitive exact/substring matching."""
        query = text.casefold().strip()
        if not query:
            return []
        allowed = set(kinds) if kinds else None
        exact, partial = [], []
        for node_id, attrs in self.graph.nodes(data=True):
            if allowed and attrs.get("kind") not in allowed:
                continue
            candidates = [attrs.get("label", ""), attrs.get("synonyms", "")]
            values = [str(v).casefold() for v in candidates if v]
            if any(query == v for v in values):
                exact.append(node_id)
            elif any(query in v or v in query for v in values):
                partial.append(node_id)
        return exact or partial

    def neighbourhood(self, seeds: Iterable[str], hops: int = 2, max_nodes: int = 80) -> nx.MultiDiGraph:
        """Return a bounded undirected neighbourhood as a directed subgraph copy."""
        selected: set[str] = set()
        frontier = {seed for seed in seeds if seed in self.graph}
        undirected = self.graph.to_undirected()
        for _ in range(max(hops, 0) + 1):
            selected.update(frontier)
            if len(selected) >= max_nodes:
                break
            next_frontier: set[str] = set()
            for node in frontier:
                next_frontier.update(undirected.neighbors(node))
            frontier = next_frontier.difference(selected)
            if not frontier:
                break
        ordered = list(selected)[:max_nodes]
        return self.graph.subgraph(ordered).copy()

    @staticmethod
    def context(subgraph: nx.MultiDiGraph, max_edges: int = 120) -> str:
        """Serialise graph evidence into compact text for retrieval-augmented generation."""
        lines = []
        for source, target, attrs in list(subgraph.edges(data=True))[:max_edges]:
            s = subgraph.nodes[source]
            t = subgraph.nodes[target]
            evidence = []
            for key in ("dataset", "data_type", "value"):
                if attrs.get(key) is not None:
                    evidence.append(f"{key}={attrs[key]}")
            suffix = f" [{'; '.join(evidence)}]" if evidence else ""
            lines.append(f"{s.get('kind')}:{s.get('label')} --{attrs.get('relation')}--> "
                         f"{t.get('kind')}:{t.get('label')}{suffix}")
        return "\n".join(lines)
