# Contributing

Contributions to CellMinerCDB AI should preserve scientific traceability, reproducibility and clear separation between observed data and generated interpretation.

## Development standards

All changes should be focused, reviewable and supported by an explanation of the scientific or engineering rationale. New biological relationships should use explicit entity and relation semantics. Data transformations should preserve source identifiers and provenance wherever possible.

Python contributions should follow clear module boundaries, type-aware interfaces and testable functions. Graph retrieval changes should include deterministic tests covering entity resolution, relation construction or neighbourhood retrieval as appropriate. R data-engineering changes should document assumptions about source objects, identifiers and database writes.

## Pull requests

A high-quality pull request should include:

- a concise description of the problem and implementation;
- the biological or analytical rationale where relevant;
- tests for new deterministic behaviour;
- documentation updates when interfaces or architecture change;
- no credentials, local paths, generated data or private datasets;
- clear acknowledgement of upstream code or resources where applicable.

## Scientific integrity

Do not present statistical association as causation. Do not treat LLM output as validated biological evidence. Cross-dataset comparisons should account for differences in experimental design, preprocessing and measurement definitions. Claims in documentation should correspond to functionality that exists in the repository.

## Security

Interactive analysis should use least-privilege database credentials. Secrets belong in environment variables and must never be committed. Changes that execute generated code or queries should be reviewed with particular care and should preserve existing safeguards or strengthen them.
