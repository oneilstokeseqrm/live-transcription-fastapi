# Stateless Stitcher Architecture Specification

## Overview

This specification defines the architectural upgrade to implement the "Stateless Stitcher" pattern in the live-transcription-fastapi service. The upgrade introduces durable transcript persistence through Redis dual-write operations while maintaining real-time streaming capabilities.

## Specification Documents

### 1. Requirements (`requirements.md`)
Defines 7 core requirements covering:
- Session identification and management
- Redis dual-write pattern
- Transcript reconstruction
- Error handling and resilience
- Session cleanup and resource management
- Deployment verification
- Configuration management

### 2. Design (`design.md`)
Provides technical design including:
- High-level architecture diagrams
- Component interfaces and responsibilities
- Data models and Redis structures
- 7 correctness properties for validation
- Error handling strategies
- Testing strategy (unit + property-based)
- Deployment verification workflow
- Performance and security considerations

### 3. Tasks (`tasks.md`)
Breaks down implementation into 12 actionable tasks:
- EventPublisher dual-write implementation
- Transcript reconstruction method
- Session ID generation
- Disconnect handling
- Error resilience
- TTL management
- Logging enhancements
- Deployment verification script
- Environment configuration
- Documentation updates

## Key Architectural Patterns

### The Stateless Stitcher
Instead of maintaining in-memory state, the system:
1. Generates a unique session_id per WebSocket connection
2. Writes each transcript chunk to both Redis Stream (real-time) and Redis List (persistence)
3. Reconstructs the full conversation from Redis List on disconnect
4. Cleans up session data after retrieval

### Dual-Write Strategy
Every final transcript chunk triggers two writes:
- **Stream Write**: For real-time consumers (existing behavior)
- **List Write**: For session persistence (new behavior)

Both writes are attempted independently with isolated error handling.

### Deployment Verification
Uses Railway MCP tools to automatically verify:
- Deployment status is "SUCCESS"
- No errors in recent logs
- Service is in "RUNNING" state
- Redis connectivity is established

## Implementation Status

- [x] Requirements documented
- [x] Design completed
- [x] Tasks defined
- [ ] Implementation pending

## Next Steps

To begin implementation:
1. Open `.kiro/specs/stateless-stitcher/tasks.md`
2. Click "Start task" next to Task 1
3. Follow the task list sequentially
4. Run tests at checkpoints (Tasks 8 and 12)

## Related Steering Files

- `.kiro/steering/architecture-standards.md`: Defines session management and dual-write protocols
- `.kiro/steering/deployment-standards.md`: Defines Railway deployment and verification standards

## Testing Approach

The specification includes both unit tests and property-based tests:
- **Unit Tests**: Verify specific behaviors and edge cases
- **Property Tests**: Verify universal properties across all inputs using `hypothesis`

7 correctness properties are defined to validate system behavior.

## Deployment Target

All changes will be deployed to Railway upon merge to `main` branch. Post-deployment verification will be conducted using Railway MCP integration.
