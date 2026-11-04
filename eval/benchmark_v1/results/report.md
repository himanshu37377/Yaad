# Benchmark v1 - offline results

freeze: `5583a64ca3261d670d2a3a0def4dd142fb8630aad42c4266d0b32ec2706048e5` - 72 cases, egress: 0 (offline).

## pipeline: 58/72 (80.6%)
- contradiction: 9/10
- injection: 6/6
- missing_evidence: 8/8
- multi_doc: 8/8
- multimodal: 11/12
- near_dup: 4/8
- paraphrase: 9/10
- temporal: 3/10
- retrieval: Recall@1 0.847, Recall@5 1.000, MRR 0.992, hard-negative pass 1.000
- citations: resolve 1.000, contained 1.000, complete 0.9444444444444444 (n=18)
- abstention rate: 1.000; false-supported rate: 0.000
- failures: C08, V05, N01, N02, N05, N08, P09, T01, T02, T03, T04, T07, T08, T09

## keyword: 72/72 (100.0%)
- contradiction: 10/10
- injection: 6/6
- missing_evidence: 8/8
- multi_doc: 8/8
- multimodal: 12/12
- near_dup: 8/8
- paraphrase: 10/10
- temporal: 10/10
- retrieval: Recall@1 0.782, Recall@5 1.000, MRR 0.952, hard-negative pass 0.935
- citations: resolve 1.000, contained 1.000, complete 1.0 (n=18)
- abstention rate: 1.000; false-supported rate: 0.000
- failures: none

## hybrid: 72/72 (100.0%)
- contradiction: 10/10
- injection: 6/6
- missing_evidence: 8/8
- multi_doc: 8/8
- multimodal: 12/12
- near_dup: 8/8
- paraphrase: 10/10
- temporal: 10/10
- retrieval: Recall@1 0.847, Recall@5 1.000, MRR 0.992, hard-negative pass 1.000
- citations: resolve 1.000, contained 1.000, complete 1.0 (n=18)
- abstention rate: 1.000; false-supported rate: 0.000
- failures: none
