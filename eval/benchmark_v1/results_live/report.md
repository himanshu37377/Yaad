# Benchmark v1 - live results

freeze: `5583a64ca3261d670d2a3a0def4dd142fb8630aad42c4266d0b32ec2706048e5` - 72 cases, model muse-spark-1.3, spent $0.302395 (cap $4.0).

## model: 18/72 (25.0%)
- contradiction: 2/10
- injection: 0/6
- missing_evidence: 8/8
- multi_doc: 8/8
- multimodal: 0/12
- near_dup: 0/8
- paraphrase: 0/10
- temporal: 0/10
- retrieval: Recall@1 0.847, Recall@5 1.000, MRR 0.992, hard-negative pass 1.000
- abstention rate: 1.000; false-supported rate: 0.000
- failures: C01, C02, C04, C06, C07, C08, C09, C10, J01, J02, J03, J04, J05, J06, V01, V02, V03, V04, V05, V06, V07, V08, V09, V10, V11, V12, N01, N02, N03, N04, N05, N06, N07, N08, P01, P02, P03, P04, P05, P06, P07, P08, P09, P10, T01, T02, T03, T04, T05, T06, T07, T08, T09, T10

## production: 62/72 (86.1%)
- contradiction: 5/10
- injection: 5/6
- missing_evidence: 8/8
- multi_doc: 7/8
- multimodal: 12/12
- near_dup: 5/8
- paraphrase: 10/10
- temporal: 10/10
- retrieval: Recall@1 0.847, Recall@5 1.000, MRR 0.992, hard-negative pass 1.000
- abstention rate: 1.000; false-supported rate: 0.000
- failures: C01, C02, C07, C08, C09, J06, M03, N01, N02, N08
