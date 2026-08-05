# Entity index worker validation

After building the packaged application, verify the worker entry point:

```bash
# Development
python3 run.py --entity-index-worker --help

# Packaged macOS (adjust path)
Diffasaurus.app/Contents/MacOS/Diffasaurus --entity-index-worker --help
```

If subprocess launch fails in the signed bundle, set `DIFFASAURUS_ENTITY_INDEX=0` to
fall back to the in-process legacy resolver path while investigating.

The worker must start without initializing Qt (`QApplication`).
