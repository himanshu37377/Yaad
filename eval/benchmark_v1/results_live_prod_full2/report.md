# Benchmark v1 - live results

freeze: `5583a64ca3261d670d2a3a0def4dd142fb8630aad42c4266d0b32ec2706048e5` - 72 cases, model muse-spark-1.3, spent $0.217465 (cap $2.0).

## production: 63/72 (87.5%)
- contradiction: 10/10
- injection: 5/6
- missing_evidence: 8/8
- multi_doc: 7/8
- multimodal: 12/12
- near_dup: 5/8
- paraphrase: 10/10
- temporal: 6/10
- retrieval: Recall@1 0.766, Recall@5 1.000, MRR 0.952, hard-negative pass 0.919
- abstention rate: 1.000; false-supported rate: 0.000
- failures: J05, M03, N01, N02, N08, T01, T02, T07, T09
