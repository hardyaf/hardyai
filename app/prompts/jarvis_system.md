# Jarvis System Architecture

## Purpose
Define how the Jarvis system is structured.

## Components

### 1. Jarvis Core
- orchestrates all actions
- manages state and decisions

### 2. Skill Layer
- domain-specific handlers
- validation + execution

### 3. Agent Layer
- micro agents (fast)
- main agent (reasoning)

### 4. Storage Layer
- SQLite databases
- persistent state

## Flow

User Input
→ Jarvis Loop
→ Intent Classification
→ Skill or Agent Selection
→ Execution
→ Storage Update
→ Response

## Rules
- Jarvis owns outcomes
- Skills execute tasks
- Agents assist execution
