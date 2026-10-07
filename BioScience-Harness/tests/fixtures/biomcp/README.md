# BioMCP fixtures

Real replies of the BioMCP MCP server, recorded on 2026-10-07 between 10:35 and 10:38 UTC:
biomcp-python 0.7.3 (MIT, genomoncology/biomcp) started over stdio with
`python -I -m biomcp run`, driven by the MCP SDK 1.30.0 (`mcp.client.stdio.stdio_client`
and `ClientSession`, protocol version 2025-11-25). Each file holds the tool, the arguments
sent, when, and the reply's text content; `trimmed` says what was cut. The replies'
`structuredContent` was `{"result": <the same text>}` and is not repeated.

| file | tool | arguments | upstream |
|---|---|---|---|
| `article_searcher.json` | article_searcher | berberine, type 2 diabetes, no preprints, no cBioPortal | PubTator3 / PubMed |
| `article_getter.json` | article_getter | PMID 34956436 | PubTator3 |
| `article_getter_pmcid.json` | article_getter | PMC8696197 (refused in-band, `isError` false) | none |
| `trial_searcher.json` | trial_searcher | type 2 diabetes, berberine | ClinicalTrials.gov |
| `trial_protocol_getter.json` | trial_protocol_getter | NCT06911983 | ClinicalTrials.gov |
| `variant_searcher.json` | variant_searcher | rs7903146, no cBioPortal, no OncoKB | MyVariant.info |
| `tools_list.json` | `tools/list` | | the 36 tool names; full descriptors of the five admitted |

What was cut, and why:

- article records: the abstracts (publishers' text), and seven of the ten search records;
- `article_getter`: the `abstract` and `full_text` fields, for the same reason;
- variant records: the CADD sections (CADD scores are licensed for non-commercial use);
- nothing from `trial_getter`, which is not admitted: its reply carries site staff's names,
  telephone numbers and e-mail addresses.

ClinicalTrials.gov records are US Government works. The remaining article fields are
bibliographic identifiers, titles and dates.
