# Evaluation datasets: sources and licences

The datasets are downloaded to `$RK_HOME/eval/`, used only for local evaluation, and never committed. Reports in
`docs/eval/` contain no dataset text. Their JSON files list per-item dataset ids, gold and predicted labels, and
scores.

| Dataset | Source | Licence |
|---|---|---|
| SciFact (Wadden et al., 2020) | `https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz` | Claims and annotations: CC BY 4.0. Corpus abstracts: ODC-By 1.0 (per the GitHub `LICENSE.md`). The Hugging Face card says CC BY-NC 2.0; both are recorded here. |
| DeepFact-Bench (Huang et al., 2026) | `kkkevinkkk/DeepFactBench` `test.json` (621 claims) | MIT |
| LitQA2 (LAB-Bench, Laurent et al., 2024) | `futurehouse/lab-bench`, config `LitQA2` | CC BY-SA 4.0 |
| ReportBench (ByteDance BandAI, 2025) | `ByteDance-BandAI/ReportBench` | Data CC0-1.0, code Apache-2.0 |
| MiniCheck-Flan-T5-Large (Tang et al., 2024) | `lytang/MiniCheck-Flan-T5-Large` | MIT |
