# Document benchmark harness

This directory contains only the content-free Phase 3 benchmark contract and evaluator. Private or
sensitive source files, expected text, and generated provider output must remain outside Git on the
encrypted document volume.

The manifest identifies sealed fixtures by relative path and SHA-256 and records structural expectations.
The results file contains only metrics and opaque fixture IDs; the runner rejects common content-bearing
keys. A passing Phase 3 case must complete on `native_docling`, preserve page counts, meet its minimum
block count, preserve reading order and evidence for every returned block, and produce a table when the
fixture declares one.

Run on the authoritative host:

```bash
python benchmarks/documents/run_benchmark.py \
  --manifest /mnt/hardyai-documents/benchmarks/native/manifest.json \
  --corpus-root /mnt/hardyai-documents/benchmarks/native/corpus \
  --results /mnt/hardyai-documents/benchmarks/native/results.json \
  --output /mnt/hardyai-documents/benchmarks/reports/docling-native.json \
  --routing-policy-version native-docling-pdf-v1
```

Reports contain versions, hashes, structural metrics, and failed opaque fixture IDs—never source text.
