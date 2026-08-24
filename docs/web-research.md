# Conversational web research

Jarvis can optionally ground a conversational answer with search results from a private SearXNG
instance. The feature is off by default and is deliberately separate from household action repair.

## Request flow

1. Known skill intents stay in their normal tool lane.
2. Informational questions and contextual conversation follow-ups go directly to Main Jarvis; an
   action-repair `not_actionable` result cannot short-circuit the conversation model.
3. Explicit requests such as “search for”, “look up”, “latest”, or “current” request research
   immediately. Other conversational questions use the configured main model for a bounded
   `direct | research | clarify` decision.
4. Jarvis sends only the minimal search query to SearXNG. It consumes titles, snippets, and result
   URLs; it does not fetch result pages or let web text invoke tools.
5. Main Jarvis treats all returned text as untrusted evidence. Source links are appended by code,
   not copied from model output.

Search is not offline: a private SearXNG process still sends the query to its configured upstream
search engines. Do not include secrets, private household data, or verbatim sensitive context in a
search request.

## Configuration

```dotenv
WEB_RESEARCH_ENABLED=true
WEB_RESEARCH_PROVIDER=searxng
WEB_RESEARCH_BASE_URL=http://127.0.0.1:8080
WEB_RESEARCH_TIMEOUT_SECONDS=15
WEB_RESEARCH_DECISION_TIMEOUT_SECONDS=60
WEB_RESEARCH_MAX_RESULTS=5
WEB_RESEARCH_SAFE_SEARCH=1
WEB_RESEARCH_CHILDREN_ENABLED=false
WEB_RESEARCH_CACHE_TTL_SECONDS=900
```

At least one Ollama model lane must be enabled. Jarvis uses the Main model when present, otherwise
the Micro model, to decide whether a non-explicit question needs research and to synthesize the
answer.

Child identities cannot use web research by default. If an operator explicitly enables it,
Jarvis forces SearXNG's strict safe-search level for child requests. This is a content filter, not a
complete child-safety guarantee.

## Docker POC

The Compose file includes an internal-only SearXNG service under the `research` profile. Enable the
feature and atomically generate its secret without printing that secret:

```bash
python3 scripts/configure_web_research.py --env-file .env
```

Then start or recreate the profile:

```bash
docker compose --env-file .env -f deploy/docker/compose.yaml --profile research up -d --build
docker compose --env-file .env -f deploy/docker/compose.yaml exec jarvis \
  python scripts/verify_install.py --require-models --api-url http://127.0.0.1:8000
```

SearXNG is reachable by Jarvis at `http://searxng:8080`; it is not published on a host port. The
checked-in settings explicitly enable JSON search responses, which SearXNG requires for its search
API.

## Native/systemd POC

Run a private SearXNG deployment using its official container instructions, publish it only on
loopback port 8080, and use the configuration above. Restart Jarvis and run:

```bash
.venv/bin/python scripts/verify_install.py --require-models --api-url http://127.0.0.1:8000
```

The verifier sends one harmless SearXNG probe when research is enabled. A missing JSON format,
unreachable endpoint, unsupported provider, or missing model lane is a failed preflight.

## Current limits

- Search-result snippets are the only evidence; full-page retrieval and document extraction are not
  implemented yet.
- Sources improve traceability, but snippets and model synthesis can still be incomplete or wrong.
- Research decisions add a local-model turn for questions without an explicit freshness/search cue.
- Only SearXNG is implemented. Google's Custom Search JSON API was not selected because it is closed
  to new customers.

References:

- SearXNG search API: <https://docs.searxng.org/dev/search_api.html>
- SearXNG Docker installation: <https://docs.searxng.org/admin/installation-docker>
- SearXNG search settings: <https://docs.searxng.org/admin/settings/settings_search.html>
