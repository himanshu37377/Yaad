"""Deterministic validation and lifecycle helpers for memory records.

Deterministic validation and lifecycle helpers for memory records.

Pure validation, stable identity keys, upsert, conflict/stale marking,
and answer-eligibility rules. No model calls; callers validate before
writing. Tables apply via Database.initialize; extraction runs through
yaad.memory.extract and the /api/memory endpoints. No answer integration.
"""
