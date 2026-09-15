"""Minimal local demonstration of the GraphRAG retrieval layer."""
import pandas as pd
from cellminer_ai import PharmacogenomicGraph, GraphRAGEngine

frame = pd.DataFrame([
    {"dataset": "GDSC", "data_type": "exp", "row_name": "EGFR", "col_name": "A549", "value": 7.2, "cell_line_tissue": "lung"},
    {"dataset": "GDSC", "data_type": "mut", "row_name": "TP53", "col_name": "A549", "value": 1, "cell_line_tissue": "lung"},
    {"dataset": "GDSC", "data_type": "act", "row_name": "Gefitinib", "col_name": "A549", "value": -0.42, "cell_line_tissue": "lung", "drug_moa": "EGFR inhibitor"},
])

kg = PharmacogenomicGraph.from_frame(frame)
result = GraphRAGEngine(kg).retrieve("Show EGFR and Gefitinib relationships in A549")
print(result.context)
