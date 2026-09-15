"""GraphRAG retrieval orchestration for cancer pharmacogenomic questions."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

import networkx as nx

from .graph import PharmacogenomicGraph


@dataclass
class GraphRAGResult:
    question: str
    matched_entities: list[str]
    context: str
    answer: str | None
    subgraph: nx.MultiDiGraph


class GraphRAGEngine:
    """Retrieve biological graph neighbourhoods and optionally synthesise grounded answers."""

    def __init__(self, knowledge_graph: PharmacogenomicGraph,
                 generator: Callable[[str, str], str] | None = None):
        self.knowledge_graph = knowledge_graph
        self.generator = generator

    def _candidate_terms(self, question: str) -> list[str]:
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", question)
        tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9_.-]{2,}\b", question)
        stop = {"which", "what", "where", "when", "with", "from", "across", "between",
                "does", "show", "compare", "cancer", "cell", "cells", "line", "lines",
                "gene", "genes", "drug", "drugs", "response", "expression", "mutation"}
        terms = quoted + [t for t in tokens if t.casefold() not in stop]
        return list(dict.fromkeys(terms))

    def retrieve(self, question: str, hops: int = 2, max_nodes: int = 80) -> GraphRAGResult:
        matches: list[str] = []
        for term in self._candidate_terms(question):
            matches.extend(self.knowledge_graph.resolve(term))
        matches = list(dict.fromkeys(matches))
        subgraph = self.knowledge_graph.neighbourhood(matches, hops=hops, max_nodes=max_nodes)
        context = self.knowledge_graph.context(subgraph)
        answer = self.generator(question, context) if self.generator and context else None
        return GraphRAGResult(question, matches, context, answer, subgraph)

    @staticmethod
    def grounded_prompt(question: str, context: str) -> str:
        return (
            "Answer the cancer pharmacogenomics question using only the retrieved graph evidence. "
            "Separate observed associations from interpretation. State when evidence is insufficient.\n\n"
            f"Question:\n{question}\n\nRetrieved graph evidence:\n{context}"
        )
