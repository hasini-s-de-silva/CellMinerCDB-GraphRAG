# Reproducibility guide

## Minimum reproducibility record

For any analysis generated with this application, record:

- repository commit hash;
- Python version and installed dependency versions;
- R version and package versions;
- CellMinerCDB dataset/version identifiers;
- PostgreSQL schema/table version and row count;
- Azure OpenAI deployment/model configuration;
- exact user query;
- captured SQL/generated code where available;
- exported result table used for interpretation.

## Recommended workflow

1. Freeze the input data version before analysis.
2. Use a read-only database role for interactive querying.
3. Save the exact natural-language prompt.
4. Inspect generated SQL/code before treating a result as scientific evidence.
5. Export the underlying rows used for any figure/statistic.
6. Re-run important findings with explicit R/Python/SQL code independent of the LLM.
7. Document cross-dataset preprocessing and unit differences.

## LLM reproducibility

LLM outputs can vary across model versions and repeated calls. For results intended for reports, manuscripts, or decision-making, convert the accepted analysis into explicit version-controlled code and treat the conversational interface as an exploratory layer rather than the final analytical record.
