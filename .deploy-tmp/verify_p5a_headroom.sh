#!/usr/bin/env bash
set -euo pipefail

health=""
for _ in $(seq 1 30); do
    health="$(docker inspect --format '{{.State.Health.Status}}' jarvis-poc-jarvis-1 2>/dev/null || true)"
    if [[ "$health" == "healthy" ]]; then
        break
    fi
    sleep 2
done

[[ "$health" == "healthy" ]]
echo "JARVIS_HEALTH=$health"
docker inspect --format 'IMAGE={{.Image}}' jarvis-poc-jarvis-1
docker inspect --format 'ACCELERATOR_HEALTH={{.State.Health.Status}} IMAGE={{.Image}}' \
    jarvis-poc-accelerator-admission-1
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' jarvis-poc-jarvis-1 \
    | grep -E '^(MICRO_MODEL_NUM_PREDICT|MAIN_REPAIR_MODEL_NUM_PREDICT|MAIN_CONVERSATION_MODEL_NUM_PREDICT|MODEL_ADAPTIVE_TOKEN_MAX_ATTEMPTS|MODEL_ADAPTIVE_TOKEN_MAX_MULTIPLIER|MAIN_TOOL_MAX_STEPS|MAIN_TOOL_MAX_FAILURES|MAIN_TOOL_TIMEOUT_SECONDS|MAIN_AGENT_LOOP_MAX_STEPS|MAIN_AGENT_LOOP_MAX_FAILURES|WEB_RESEARCH_DECISION_MODEL_NUM_PREDICT|EMAIL_AGENT_SUMMARY_NUM_PREDICT|EMAIL_AGENT_CLASSIFIER_NUM_PREDICT|ACTION_TICKET_REVIEW_MODEL_NUM_PREDICT|TURN_TIMEOUT_SECONDS)=' \
    | sort
