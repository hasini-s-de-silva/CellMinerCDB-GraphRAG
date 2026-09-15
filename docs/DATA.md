# Data and provenance

## Scope

The project is designed around CellMinerCDB cancer pharmacogenomics resources and cross-database cell-line/drug harmonisation. Dataset binaries are intentionally not committed to this repository.

## Referenced datasets

The supplied R configuration references:

- GDSC
- CCLE
- CTRP
- NCI Sarcoma
- Uni Sarcoma
- MDA Mills

Availability of molecular/drug modalities differs by dataset.

## Modalities

The extraction code recognises these short labels:

| Code | Intended meaning |
|---|---|
| `exp` | gene expression |
| `mut` | mutation data |
| `cop` | copy-number data |
| `act` | drug activity |

The exact preprocessing, units, and semantics must be checked against each upstream CellMinerCDB dataset before cross-study interpretation.

## Harmonisation

The pipeline preserves original identifiers and creates canonical identifiers for cross-dataset analysis. Supporting resources include cell-line matching and drug synonym information. Harmonisation is necessary for integration but should not be interpreted as proof that assays or measurements are biologically equivalent across sources.

## Main long-format representation

The implementation builds a PostgreSQL table with fields for source dataset, modality, row/feature identifier, cell-line identifier, numerical value, tissue metadata, drug mechanism metadata, synonyms, and original source names.

## Scale

The supplied project documentation describes a combined table on the order of ~280 million observations. Treat this as an implementation-scale figure rather than a fixed property of CellMinerCDB: the exact row count depends on which source datasets/modalities are downloaded and loaded.

## Data acquisition

The original project documentation referenced CellMinerCDB Zenodo record `15122311`. Before using the data:

1. verify the current upstream record;
2. read the dataset documentation;
3. comply with upstream licence/citation requirements;
4. record dataset versions/checksums for reproducible analyses.

## Do not commit

Do not commit downloaded RData files, database dumps, generated exports, credentials, or logs containing sensitive configuration. `.gitignore` excludes the main local dataset/output locations.
