# Milestone 3 benchmark analysis

Run on reference machine:

```bash
python3 -m tools.benchmark_entity_index --files 10000 --rows-per-file 100 --families 10
```

## Baseline sample (200 files, local dev)

```json
{
  "warm_open_seconds": 0.00057,
  "search_seconds": 0.0066,
  "pit_seconds": 0.00067,
  "unchanged_sync_seconds": 0.013,
  "unchanged_parsed": 0,
  "acceptance": "all targets passed at 200 files"
}
```

No code optimizations applied — all sampled targets passed without changes.

## Optimization policy

Implement changes in Milestone 3 **only** when benchmark output shows a missed acceptance target
and the bottleneck is identified in measured phase timings (discovery, checking, indexing, open, search, PIT).

No speculative optimizations without benchmark evidence.

## Candidate areas (do not implement unless benchmark proves need)

| Target | Possible response if missed |
|---|---|
| Warm open > 1s | Defer non-critical metadata reads on repository open |
| Search > 150ms | Tune FTS5 query path or LRU sizes |
| PIT > 500ms | Add covering index for occurrence fetch by family |
| Unchanged 10k sync > 10s | Batch fingerprint comparison during checking phase |

After the full 10k run, append measured values and any applied optimizations below.
