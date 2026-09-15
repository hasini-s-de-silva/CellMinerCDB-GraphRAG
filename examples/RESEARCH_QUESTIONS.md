# Example research questions

These examples illustrate the intended analytical interface. They are **not validated biological findings** and should be adapted to the datasets actually loaded.

## Gene expression

- Compare `SLFN11` expression across selected cancer cell lines.
- Show the distribution of `TP53` expression by available tissue annotation.
- Which cell lines have the highest expression of a specified gene in a selected dataset?

## Genomic alterations

- Retrieve mutation measurements for `TP53` across a selected set of cell lines.
- Compare a drug-response measurement between cell lines with and without an available molecular feature, after verifying how mutation values are encoded in the source dataset.

## Drug response

- Retrieve activity measurements for a named drug across available cell lines.
- Compare activity values for the same canonical drug across two datasets, while reporting the source dataset for every observation.
- Which cell lines show the strongest/weakest activity values for a specified compound, given the source dataset's activity convention?

## Integrated hypothesis generation

- Explore whether expression of a candidate gene is associated with activity of a selected drug within one dataset.
- Identify molecular features associated with extreme drug-response values, then validate the association using explicit statistical code.

## Interpretation checklist

Before interpreting any result:

1. confirm the modality and units;
2. confirm the source dataset;
3. inspect sample size and missingness;
4. verify canonical identifier mappings;
5. avoid combining incomparable assay scales without appropriate normalisation;
6. independently reproduce important statistics outside the LLM-generated workflow.
