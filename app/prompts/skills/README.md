# Skill Prompt Registry Map

This folder contains machine-loadable and human-readable skill instructions.

- `SKILL_TEMPLATE.md` (required entry point for new skills)
  - defines required markdown/frontmatter contract
  - includes mandatory Main handoff context and typed-tool sections
  - if these sections are not filled out, the skill is not learnable

- `calendar_skill.md`
  - execution ref: `app.skills.domains.calendar.handler:run`
  - handler: `app/skills/domains/calendar/handler.py`
  - service: `app/skills/domains/calendar/service.py`
  - storage: `app/skills/domains/calendar/storage.py`
- `lights_skill.md`
  - execution ref: `app.skills.domains.lights.handler:run`
  - handler: `app/skills/domains/lights/handler.py`
  - service: `app/skills/domains/lights/service.py`
  - storage: `app/skills/domains/lights/storage.py`
  - regex patterns: `app/skills/patterns/lights_patterns.py`
- `lists_skill.md`
  - execution ref: `app.skills.domains.lists.handler:run`
  - handler: `app/skills/domains/lists/handler.py`
  - service: `app/skills/domains/lists/service.py`
  - storage: `app/skills/domains/lists/storage.py`
- `conversation_skill.md`
  - execution ref: `app.skills.domains.conversation.handler:run`
  - handler: `app/skills/domains/conversation/handler.py`
  - storage: `app/skills/domains/conversation/storage.py`
  - conversation model path: `app/core/main_jarvis.py` and `app/core/main_backend.py`
- `documents_skill.md`
  - execution ref: `app.skills.domains.documents.handler:run`
  - Main-only bounded query/control surface over the isolated local Document Gateway
  - restricted-read persistence with Main-only semantic access
- `critical_skills.md`
  - compiled bundle generated from SQL `skills` rows where `critical_level >= 1`
  - currently refreshed by scheduled job `job.system.compile_critical_skills_on_main_idle`
  - trigger: `event:main_idle` (main-model warm window cools down)
- The Main-only runtime compiles `critical_skills.md` and its
  `critical_skills.md.meta.json` stale-check sidecar. Legacy classifier fields
  in source contracts remain schema-compatibility metadata only.

Agent-specific personalities are stored in `app/prompts/personas/`.
