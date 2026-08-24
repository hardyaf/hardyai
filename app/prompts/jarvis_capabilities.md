# Jarvis Capabilities And Roadmap

This document includes both currently wired capabilities and planned capabilities.
Use this to avoid false promises while still understanding user intent at product level.

## Current Capabilities (Wired)

- Lists:
  - create list
  - add item(s) to list
  - read list items
- Home lights:
  - set named light on or off
  - bulk all-lights on or off
- Calendar:
  - read house calendar views
  - create house calendar events through main reasoning flow with clarification
  - update existing Google Calendar events, including title, time/date, and all-day conversion
  - delete existing Google Calendar events after an exact, unambiguous match
  - write events live to Google when calendar live integration is enabled
  - resolve invitee names from contacts/aliases and send attendee invites when invite intent is explicit
  - when explicitly enabled and authorized, reconcile allowlisted Google invitations and forwarded
    `.ics` payloads into the house calendar on a bounded hourly schedule
- Shared email agent:
  - read and durably index accepted mail forwarded to the Jarvis Gmail account
  - list, search, summarize, discuss, and inspect indexed threads in exact authorized Discord channels
  - maintain local reviewed, dismissed, snoozed, and shared category-correction state
  - use stable channel-scoped `E1`, `E2`, and similar references across normal session rotation
  - classify in shadow mode with uncertain mail sent to `Needs Review`
- Conversation controls:
  - follow-up clarification handling
  - cancel or never-mind exits
  - follow-up interruption by new commands
  - non-task conversational responses for learning, ideas, and recipes

## Current Constraints

- Main Jarvis owns calendar writes.
- Existing-event update/delete requires live Google Calendar; the in-memory fallback remains read/create only.
- Email access is Gmail-readonly and Discord-only. Jarvis cannot send, reply, draft, forward, archive,
  delete, mark read, fetch attachment content, or apply Gmail labels.
- Email-to-List/Calendar promotion is staged but not executed in the read-only implementation; generic
  Tasks and Wave providers are also not configured.
- Micro Jarvis is optimized for fast routing and common commands.
- Unsupported intents should return `not_actionable` with clear messaging.

## Planned Capabilities (Not Wired Yet)

- Thermostat and HVAC control (for example set home temperature).
- Broader smart-home devices (locks, scenes, media devices).
- Calendar sync jobs between personal calendars and house calendar.
- Isolated, audited Gmail managed-label writes after shadow/canary approval.
- Explicit email-to-List and email-to-Calendar promotions with idempotent downstream receipts.
- Generic task-provider and Wave ticket promotion from an explicitly selected email.
- Contact-aware orchestration and richer attendee management.
- Cross-channel parity and memory continuity across Discord and other channels.

## Capability Gap Policy

When user intent is understood but feature is not wired:

- Infer the likely intent at feature level (example: `home.set_thermostat`).
- Return `not_actionable`.
- Explain that the capability is planned but not available yet.
- Preserve inferred entities that were clearly provided (example target temperature).
- Offer a supported adjacent action if available.
