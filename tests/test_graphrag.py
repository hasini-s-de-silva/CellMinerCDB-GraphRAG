import pandas as pd
from cellminer_ai import PharmacogenomicGraph, GraphRAGEngine


def fixture_frame():
    return pd.DataFrame([
        {"dataset":"GDSC","data_type":"exp","row_name":"EGFR","col_name":"A549","value":7.2,"cell_line_tissue":"lung"},
        {"dataset":"GDSC","data_type":"mut","row_name":"TP53","col_name":"A549","value":1,"cell_line_tissue":"lung"},
        {"dataset":"GDSC","data_type":"act","row_name":"Gefitinib","col_name":"A549","value":-0.42,"cell_line_tissue":"lung","drug_moa":"EGFR inhibitor"},
    ])


def test_graph_builds_typed_entities_and_relations():
    kg = PharmacogenomicGraph.from_frame(fixture_frame())
    assert "gene:EGFR" in kg.graph
    assert "drug:Gefitinib" in kg.graph
    assert "cell_line:A549" in kg.graph
    relations = {d["relation"] for _, _, d in kg.graph.edges(data=True)}
    assert {"EXPRESSION", "MUTATION", "DRUG_ACTIVITY", "HAS_TISSUE", "MEASURED_IN"}.issubset(relations)


def test_graphrag_retrieves_relevant_neighbourhood():
    kg = PharmacogenomicGraph.from_frame(fixture_frame())
    result = GraphRAGEngine(kg).retrieve("How is Gefitinib related to A549?")
    assert "drug:Gefitinib" in result.matched_entities
    assert "Gefitinib" in result.context
    assert "A549" in result.context
