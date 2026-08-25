# What Is Jarvis?

> **Jarvis is an attempt to build a private, persistent AI operating layer for a real household — not just a chatbot that happens to have tools.**

This document explains the larger vision behind HardyAI/Jarvis, how the current implementation fits that vision, and where the project is ultimately intended to go.

It is intentionally less technical than the main [README](../README.md). New contributors should be able to read this first, understand *why* the system exists, and then use the README and `/docs` for implementation details.

---

## The Short Version

Jarvis is intended to become an AI system that can:

* talk naturally with the people who use it;
* remember useful information over months and years;
* understand the household's people, places, projects, documents, routines, and preferences;
* retrieve information when it is needed;
* interact with calendars, lists, email, home systems, documents, and other tools;
* perform routine actions safely;
* recognize when an action requires clarification or human approval;
* help turn unstructured information into organized knowledge and work;
* operate primarily on hardware controlled by the people using it; and
* gradually become more useful as its knowledge and capabilities grow.

The important distinction is:

**Jarvis is not the language model.**

The language model is one component of Jarvis.

Jarvis is the persistent system around the model: memory, permissions, tools, identities, data, integrations, interfaces, rules, workflows, and the infrastructure that allows intelligence to safely interact with the real world.

---

# Why Build This?

Modern AI systems are remarkably capable, but most consumer AI still behaves like a very intelligent person with amnesia.

A conversation may be excellent in the moment, but the assistant usually lacks a durable understanding of:

* what happened last month;
* where important information lives;
* what projects are underway;
* what different family members are responsible for;
* what was decided previously;
* what documents exist;
* what should happen next;
* what systems it is allowed to control; or
* whether something it attempted actually succeeded.

At the same time, the useful parts of everyday life are scattered across dozens of systems:

**email → calendars → paper documents → notes → smart-home devices → contacts → bills → websites → task lists → computers → phones → photos**

The long-term idea behind Jarvis is to place an intelligent layer across those systems.

Instead of requiring the human to remember **where** information lives or **which application** performs an action, the human should usually be able to describe the desired outcome.

For example:

> “When was the last time we replaced the furnace filter?”

> “I took notes at last night's meeting. Turn them into minutes and add the things I agreed to do to my task list.”

> “Find our marriage license.”

> “What did that contractor quote us last year?”

> “We're out of dishwasher tablets.”

> “Add soccer practice next Thursday and make sure it doesn't conflict with anything.”

> “I scanned this bill. What is it, when is it due, and does anything look unusual?”

The system should determine which parts of its memory and toolset are relevant, retrieve the necessary context, and help complete the task.

---

# Jarvis Is a System, Not an App

There may eventually be many ways to interact with Jarvis:

* text chat;
* Discord;
* a web interface;
* phones;
* voice;
* microphones or speakers around the house;
* document scanners;
* cameras or other sensors where explicitly authorized;
* desktop interfaces;
* purpose-built displays; and
* future interfaces that do not exist yet.

None of those interfaces *is* Jarvis.

They are ways of talking to Jarvis.

Likewise, Ollama, Qwen, GPT-OSS, Paperless-ngx, Home Assistant, Google Calendar, SQLite, or any future model or integration is not Jarvis.

They are components Jarvis can use.

This distinction is intentional. Individual models, vendors, interfaces, and technologies will change much faster than the system itself.

---

# The Original Architectural Idea

The first Jarvis planning work separated the system into a few basic layers:

```text
                 PEOPLE
                    |
        +-----------+-----------+
        |           |           |
      Voice       Chat        Other
        |           |        Interfaces
        +-----------+-----------+
                    |
                    v
              JARVIS CORE
        identity / context / policy
                    |
             +------+------+
             |             |
             v             v
          MICRO           MAIN
       fast/simple      reasoning/
        commands        conversation
             |             |
             +------+------+
                    |
                    v
                 SKILLS
      calendar / lists / home / email
       documents / research / future...
                    |
                    v
          SERVICES + ADAPTERS
                    |
        +-----------+-----------+
        |           |           |
      Memory      Tools       External
       & Data                Systems
```

The implementation has changed significantly since the earliest prototypes, but this basic separation remains important.

The system needs to distinguish:

1. **how a person communicates with Jarvis;**
2. **how Jarvis decides what the request means;**
3. **what Jarvis is authorized to do;**
4. **which capability should perform the work;**
5. **where authoritative information lives;** and
6. **whether the requested action actually succeeded.**

Keeping those responsibilities separate is what allows Jarvis to grow without eventually becoming one enormous AI prompt with unrestricted access to everything.

---

# Micro and Main

One of the early design decisions was that every request does not need the same amount of intelligence.

Jarvis therefore has two broad reasoning paths.

## MicroJarvis

Micro handles explicit, routine, well-bounded commands.

Examples might include:

* add milk to a list;
* turn off a permitted light;
* read today's calendar;
* perform another registered low-complexity action.

Micro should be:

* fast;
* predictable;
* inexpensive computationally;
* narrow in authority; and
* easy to verify.

## Main Jarvis

Main handles requests requiring broader understanding.

Examples include:

* conversation;
* ambiguous requests;
* planning;
* combining information from several domains;
* recovering from a misunderstood command;
* working with documents;
* research;
* deciding that clarification is required; or
* constructing a multi-step plan.

Main can reason more deeply, but **greater intelligence does not grant greater authority**.

Every real-world action must still pass deterministic permission and capability checks.

That distinction is central to the project.

---

# Skills: How Jarvis Reaches the Real World

Jarvis capabilities are organized into **skills**.

A skill represents an area in which Jarvis is allowed to understand requests and potentially perform actions.

Current or developing examples include:

* Lists
* Calendar
* Home
* Email
* Web Research
* Documents

Future skills could cover areas such as:

* tasks and project management;
* contacts;
* household inventory;
* finances;
* vehicles;
* maintenance;
* media;
* recipes and meal planning;
* health data where appropriate;
* business workflows;
* civic or organizational work; and
* other specialized systems.

Skills are deliberately registered and scoped.

A model cannot simply decide:

> “I have access to a computer, so I suppose I can do this.”

Jarvis must already have an authorized capability for that action.

This creates an important boundary between **reasoning** and **authority**.

---

# Memory Is a Core Requirement

A useful Jarvis cannot start from zero every morning.

Long-term memory is therefore not an optional feature added to conversation. It is one of the core purposes of the system.

Eventually, Jarvis should be able to distinguish among different kinds of information:

### Interaction history

What happened in previous conversations?

### Facts

What durable things have been learned?

### Documents and source material

Where did the information come from?

### People and relationships

Who or what does a name refer to?

### Projects

What are we working on, and what has already been decided?

### Tasks and commitments

What needs to happen next?

### Preferences

How does a particular person generally want something handled?

### Temporary context

What does “that bill,” “the soccer email,” or “the thing we discussed yesterday” refer to right now?

These are different problems and should not eventually become one giant vector database or conversation transcript.

Jarvis should know both **what it believes** and, where possible, **why it believes it**.

---

# Documents Are a Good Example of the Larger Vision

The developing Document Intelligence system illustrates how the larger Jarvis philosophy translates into implementation.

A document may begin as:

* a photograph;
* a handwritten page;
* a PDF;
* a bill;
* a business card;
* meeting notes;
* a receipt;
* an official record; or
* something scanned from a filing cabinet.

The objective is not merely:

> “Run OCR on this image.”

The objective is to turn physical and digital documents into useful, durable knowledge while preserving the source.

For example:

```text
photo of handwritten meeting notes
            |
            v
         archive
            |
            v
       OCR / parsing
            |
            v
     structured evidence
            |
       +----+----+
       |         |
       v         v
   meeting     possible
   summary      tasks
       |         |
       +----+----+
            |
       human review
            |
            v
       Jarvis systems
```

A scanned bill could eventually produce:

* the archived original;
* vendor identification;
* invoice amount;
* due date;
* account references;
* searchable text;
* a reminder or proposed task; and
* potentially a proposed payment workflow.

But reading a bill does **not** mean the OCR system receives permission to spend money.

Information extraction and real-world authority remain separate.

The current architecture uses local document services and Paperless-ngx as the canonical archive while Jarvis owns interpretation, provenance, review, and links to other domains.

See [OCR-Plan.md](./OCR-Plan.md) for the technical implementation.

---

# Local-First Is Intentional

Jarvis is designed around the assumption that some of the most useful information in a person's life is also the information they should be least interested in uploading indiscriminately to third parties.

Examples include:

* private correspondence;
* household schedules;
* personal documents;
* financial records;
* identity documents;
* family information;
* home automation;
* long-term memory; and
* years of accumulated personal context.

For that reason, Jarvis is being built **local-first**.

The normal intelligence and data-processing path should be able to operate using systems running on hardware under the user's control.

Internet access can still be useful for things such as:

* research;
* retrieving explicitly requested information;
* interacting with external services; or
* communicating with services the household intentionally uses.

But an Internet connection should not be the foundation of Jarvis's memory or intelligence.

Where practical:

**bring the intelligence to the private data rather than sending the private data to the intelligence.**

---

# Local-First Does Not Mean Model-Locked

Jarvis should not depend permanently on one model.

Models are interchangeable infrastructure.

A future Jarvis might simultaneously use:

* a tiny model for classification;
* a fast model for ordinary conversation;
* a larger model for difficult reasoning;
* specialized vision models;
* OCR models;
* speech recognition;
* speech synthesis;
* embeddings;
* deterministic software; and
* optionally external intelligence for specifically permitted workloads.

The router should choose the appropriate resource for the job.

Replacing one model should not require redesigning the household.

---

# Safety Is Architecture, Not a Prompt

A system that only answers trivia can afford to be wrong occasionally.

A system that can:

* send email;
* change a calendar;
* control a house;
* read sensitive documents;
* communicate as a family member;
* modify stored information; or
* eventually initiate consequential workflows

cannot rely on:

> “The AI was told to be careful.”

Jarvis therefore treats safety as a software architecture problem.

Important principles include:

* identities are explicit;
* permissions are explicit;
* skills are registered;
* execution fails closed;
* models do not grant themselves capabilities;
* untrusted web or document content cannot become instructions;
* dangerous actions require additional approval;
* queues and retries are bounded;
* writes are durable;
* actions should be observable;
* sensitive information belongs in protected storage;
* child identities can have different capabilities from adults; and
* successful reasoning is not assumed to mean successful execution.

The goal is not to prevent Jarvis from ever making a mistake.

The goal is to prevent a model mistake from automatically becoming an unlimited real-world mistake.

---

# Why the Current Code Sometimes Looks More Conservative Than the Vision

New contributors may notice that the architecture contains many restrictions around what the AI *cannot* do.

That is intentional.

The eventual system is supposed to have significantly more capability and autonomy than the current prototype.

The path to that system cannot simply be:

```text
give the AI more credentials
        +
tell it to be careful
```

Before expanding autonomy, Jarvis needs reliable mechanisms for:

* identity;
* authorization;
* durable jobs;
* human review;
* provenance;
* failure recovery;
* verification;
* protected storage;
* bounded execution; and
* observability.

Much of the current engineering work is building those foundations.

The conservative behavior of today's system is therefore not the destination.

It is what makes the destination possible.

---

# Where the Project Is Today

The current v0 deployment runs as an always-on Linux service and already demonstrates the major architectural pieces.

Jarvis currently has or is actively developing:

### Conversation

A persistent conversational entry point through Main.

### Fast commands

Explicit Micro commands for supported routine actions.

### Registered skills

Domain-specific capability and authorization boundaries.

### Lists

Durable list operations.

### Calendar

Calendar retrieval and permitted actions.

### Home

Controlled smart-home interactions.

### Email

Protected email triage and workflows.

### Session context

Short-term conversational continuity.

### Durable interaction writes

Work survives normal process failures and restarts.

### Bounded web research

Optional read-only research through a controlled local search service.

### Action verification

Infrastructure for checking whether actions had the intended result.

### Document Intelligence

A growing local system for archival, OCR, parsing, search, extraction, provenance, and human review.

The repository has therefore crossed an important line:

**Jarvis is no longer primarily an architecture sketch.**

There is now a working runtime whose design can be incrementally expanded toward the larger vision.

---

# Vision to Execution

A useful way to understand the roadmap is as a progression.

## Stage 1 — Can Jarvis understand and act?

Build the basic interaction loop.

* conversation;
* simple commands;
* tools;
* routing;
* lists;
* calendars;
* home controls;
* external interfaces.

**Status: working foundation.**

---

## Stage 2 — Can Jarvis be trusted to keep running?

Move from demo behavior to system behavior.

* explicit identities;
* permissions;
* bounded concurrency;
* durable jobs;
* failure recovery;
* protected configuration;
* action verification;
* modular domains;
* inspectable state.

**Status: current major engineering focus and increasingly implemented.**

---

## Stage 3 — Can Jarvis remember the world?

Turn disconnected interactions into durable knowledge.

* document archive;
* OCR and parsing;
* provenance;
* structured facts;
* contact/person model;
* richer task model;
* project memory;
* entity relationships;
* corrections;
* long-term retrieval.

A handwritten page, email, conversation, scanned document, calendar event, and task should gradually become related pieces of the same knowledge system rather than isolated data.

**Status: beginning with Document Intelligence and existing session/domain state.**

---

## Stage 4 — Can Jarvis coordinate across domains?

Once the foundations are reliable, requests can become more useful.

For example:

> “We received the soccer schedule. Add the games to the family calendar, tell me which ones conflict with anything, and remind me about the ones where I'm responsible for equipment.”

That request potentially touches:

```text
Documents
    |
Calendar
    |
People
    |
Tasks
    |
Memory
    |
Notifications
```

Jarvis should coordinate those systems while preserving the authority and provenance of each one.

---

## Stage 5 — Can Jarvis become proactive?

A mature assistant should not require the user to notice every problem first.

With explicitly granted authority, Jarvis could recognize situations such as:

* an upcoming bill that has not been handled;
* a scheduling conflict;
* a commitment mentioned in meeting notes but never added to a task list;
* a household supply that is routinely needed;
* an unanswered important email;
* a maintenance interval approaching;
* a document that appears to require review;
* a project that has stalled; or
* information that contradicts something previously stored.

The initial behavior should usually be:

> **notice → explain → suggest**

rather than:

> **notice → autonomously change everything**

Over time, individual repetitive workflows can be granted greater autonomy where reliability has been demonstrated.

---

## Stage 6 — Ambient Jarvis

Eventually, interacting with Jarvis should not require thinking about which device or application contains the assistant.

The same underlying Jarvis could be available through:

* phones;
* desktops;
* household displays;
* voice endpoints;
* scanners;
* vehicles;
* workshops;
* remote access;
* wearable devices; or
* specialized interfaces.

The system should maintain the same identity, permissions, memory, and knowledge regardless of interface.

Moving from Discord to voice should feel like changing the keyboard, not changing assistants.

---

# Household First, Not Household Only

The household is the first deployment boundary because it forces the project to solve difficult problems early:

* multiple users;
* different authority levels;
* sensitive information;
* long-lived memory;
* physical devices;
* schedules;
* documents;
* children;
* consequential actions; and
* shared resources.

But the architecture is intended to support additional governed domains.

The same Jarvis could eventually assist with:

* personal work;
* a small business;
* volunteer organizations;
* community projects;
* research;
* civic responsibilities; and
* other long-running areas of a person's life.

Those domains should not require independent AI personalities with independent memories.

They should become appropriately separated contexts within the same trusted system.

---

# What Jarvis Is Not

## Jarvis is not ChatGPT running locally

Local inference is useful, but the project is much larger than model hosting.

## Jarvis is not Home Assistant

Jarvis may use Home Assistant, but home automation is one skill among many.

## Jarvis is not an unrestricted autonomous agent

Models do not receive universal credentials and permission to improvise arbitrary actions.

## Jarvis is not one giant database

Different systems remain authoritative for different kinds of information.

## Jarvis is not a replacement for every application

Calendar systems should remain good at calendars. Document archives should remain good at documents.

Jarvis provides the intelligence and coordination layer across them.

## Jarvis is not intended to depend on permanent access to a cloud AI provider

External services may be used deliberately, but the core architecture should remain functional under household control.

## Jarvis is not finished

Many components currently represent the safe foundation required before the more ambitious behavior can exist.

---

# Design Rule: Use Existing Systems Where They Are Better

Jarvis should not recreate mature software merely because it can.

If a reliable local or open system already solves a problem well, Jarvis should generally integrate with it.

For example:

```text
Paperless-ngx
     |
     | owns document/archive behavior
     v
   Jarvis
     |
     | understands what the document means
     | and connects it to the rest of life
     v
Memory / Tasks / People / Actions
```

The value Jarvis adds is **coordination, interpretation, memory, policy, and intelligence**.

This approach also keeps individual components replaceable.

---

# The End Goal

The end goal is **not a better chatbot**.

The end goal is a **trusted cognitive infrastructure for everyday life**.

Jarvis should eventually function as a private, persistent intelligence that can:

### Know

Maintain a durable understanding of the people, information, projects, systems, and history it is authorized to know.

### Remember

Preserve useful context across years rather than conversations.

### Find

Retrieve information regardless of whether it originated in an email, document, conversation, calendar, note, or another connected source.

### Understand

Turn messy human information — including speech, handwriting, documents, images, and conversations — into useful structured knowledge.

### Reason

Combine information from several areas to answer questions, identify problems, or construct plans.

### Coordinate

Work across multiple systems without requiring the user to manually shuttle information between applications.

### Act

Perform authorized real-world actions through explicit, inspectable capabilities.

### Verify

Determine whether important actions actually succeeded.

### Learn

Become more useful as the household corrects it, teaches it, and uses it.

### Protect

Keep private information and consequential authority under the control of the people who own the system.

### Be Present

Remain available across devices and interfaces without fragmenting into separate assistants.

---

## What Success Eventually Looks Like

A successful Jarvis should make interactions like this ordinary:

> “Jarvis, what do I need to worry about this week?”

And answering that question may require understanding:

* calendars;
* tasks;
* recent email;
* bills;
* school schedules;
* household maintenance;
* current projects;
* meeting notes;
* commitments;
* documents; and
* the preferences of the person asking.

Jarvis should be able to say what matters, explain why it matters, retrieve the supporting information, propose sensible next actions, and — where permission has already been granted — complete routine work.

The user should not need to know which database, application, model, integration, or server was involved.

They should simply be able to work with their own information through a system they control.

---

# The Guiding Idea

There is a useful way to summarize the entire project:

> **Build the memory first. Build the tools carefully. Give intelligence controlled access to both. Then allow autonomy to grow only as trust is earned.**

The hardware will change.

The models will change.

The interfaces will change.

Many of the individual technologies in this repository will eventually change.

The durable part of Jarvis is the idea that a person or household should be able to own an intelligent system that **knows their world, remembers their history, helps run their life, and remains accountable to them.**

That is what this repository is trying to build.
