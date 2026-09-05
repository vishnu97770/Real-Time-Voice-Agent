# Real-Time Voice Agent

## Purpose
This repository builds a reusable real-time voice layer around a credit underwriting workspace.

## Repository Map
- app/: Python API foundation and configuration
- frontend/: browser workspace shell
- tests/: automated checks
- docs/: architecture and development decisions

## Development Commands
- python3 -m pytest: run tests
- python3 -m uvicorn app.main:app --reload: run the API on port 8000
- Serve frontend/ from a local static server.

## Architecture Rules
- Keep client, real-time transport, agent orchestration, underwriting services, and external integrations separate.
- Use async I/O for network-bound real-time work.
- Keep provider integrations behind adapters and configuration.

## Safety Rules
- The agent is decision support, never autonomous credit authority.
- Financial answers must use authorized, evidence-backed tools.
- Never fabricate financial facts when a dependency fails.
- Never commit credentials or place secrets in frontend code.
