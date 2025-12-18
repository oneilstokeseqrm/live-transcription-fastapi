# Agent Steering Documentation

This directory contains steering documents that guide AI agents working on this codebase. These documents establish standards, patterns, and best practices for the live transcription service.

## Document Overview

### Core Standards

1. **architecture-standards.md** - System architecture patterns
   - Session management protocol
   - Stateless Stitcher pattern
   - Redis dual-write strategy
   - Transcript retrieval standards

2. **deployment-standards.md** - Deployment and verification procedures
   - Railway deployment workflow
   - Post-deployment verification checklist
   - Environment configuration requirements
   - Rollback protocols

### Development Standards

3. **code-standards.md** - Python/FastAPI coding conventions
   - Code style and formatting (PEP 8)
   - Naming conventions
   - Async patterns
   - Error handling
   - File organization

4. **websocket-standards.md** - WebSocket implementation patterns
   - Connection lifecycle management
   - Message formats
   - Error handling
   - Cleanup protocols
   - State management

5. **redis-patterns.md** - Redis usage guidelines
   - Connection management
   - Data structure patterns (Streams, Lists, Hashes)
   - Key naming conventions
   - TTL strategy
   - Performance considerations

### Quality Assurance

6. **testing-standards.md** - Testing requirements and patterns
   - Unit testing guidelines
   - Integration testing
   - Mocking strategies
   - Property-based testing
   - Coverage requirements

7. **logging-standards.md** - Logging and observability
   - Structured logging patterns
   - Log levels and context
   - Performance logging
   - Audit logging
   - What to log and what not to log

8. **security-standards.md** - Security best practices
   - Secret management
   - API key security
   - WebSocket authentication
   - Data privacy and PII handling
   - Dependency security

## How to Use These Documents

### For AI Agents

These documents are automatically included in agent context when working on this codebase. They provide:

- Standards to follow when writing new code
- Patterns to use for common tasks
- Requirements for testing and deployment
- Security considerations

### For Developers

Use these documents as:

- Reference for project conventions
- Onboarding material for new team members
- Decision records for architectural choices
- Checklists for code reviews

## Document Inclusion

All documents in this directory use the frontmatter:

```yaml
---
inclusion: always
---
```

This means they are automatically included in all agent interactions with this workspace.

## Critical Implementation Gaps

The steering documents identify several gaps in the current implementation:

1. **Session ID Generation**: Not implemented in WebSocket endpoint
2. **Redis Dual-Write**: Only Stream writes exist, List writes missing
3. **Transcript Retrieval**: No final transcript retrieval on disconnection
4. **Environment Validation**: No startup validation of required env vars

See individual documents for implementation details.

## Maintenance

When updating these documents:

- Keep them focused and actionable
- Include code examples where helpful
- Reference specific files and line numbers when pointing out gaps
- Update this README when adding new documents
- Ensure frontmatter is correct for inclusion rules

## Related Documentation

- Project README: `../README.md`
- Spec Documents: `.kiro/specs/`
- Environment Template: `.env.example`
