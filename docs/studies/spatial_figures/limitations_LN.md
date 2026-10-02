# Limitations of this run

- One subject: the run shows that the pipeline works on this tissue and describes it. It supports no difference between patients, conditions or treatments.
- Expression clusters (k = 8) are computed without spatial information and drawn on the tissue afterwards; they are not spatial domains.
- A Visium spot (55 µm) averages several cells; clusters and statistics describe spots, not cells. No deconvolution was run; any cell-type abundance would be a model estimate, not an observed identity.
- Moran's I p values assume normality and treat the section as one realisation; they rank genes by spatial structure within this tissue and do not generalise to other samples.
- Marker genes are contrasts between clusters defined from the same data, so they carry effect sizes only.
