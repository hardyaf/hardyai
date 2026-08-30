# Main model acceptance benchmark

`main_acceptance_cases.json` is a synthetic, deterministic model-acceptance manifest. It exercises existing Main conversation, typed turn-decision, and action-repair contracts without dispatching a skill or changing domain state.

Run it inside the Hardybot application image after accelerator admission allows both models:

```text
python scripts/benchmark_main_models.py \
  --base-url http://accelerator-admission:8040 \
  --model gpt-oss:20b \
  --model qwen3.8:27b \
  --output /opt/jarvis/data/benchmarks/main-model-acceptance.json
```

The report intentionally excludes prompts, responses, and thinking. It contains case IDs, expected and observed contract labels, timing, model metadata, and the existing content-free Ollama metrics.
