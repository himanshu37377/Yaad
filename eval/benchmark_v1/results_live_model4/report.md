# Benchmark v1 - live results

freeze: `5583a64ca3261d670d2a3a0def4dd142fb8630aad42c4266d0b32ec2706048e5` - 72 cases, model muse-spark-1.3, spent $0.113528 (cap $1.5).

## model: 56/72 (77.8%)
- contradiction: 9/10
- injection: 6/6
- missing_evidence: 8/8
- multi_doc: 7/8
- multimodal: 11/12
- near_dup: 5/8
- paraphrase: 8/10
- temporal: 2/10
- retrieval: Recall@1 0.847, Recall@5 1.000, MRR 0.992, hard-negative pass 1.000
- abstention rate: 1.000; false-supported rate: 0.000
- failures: C08, M03, V05, N02, N05, N08, P07, P09, T01, T02, T03, T04, T07, T08, T09, T10
