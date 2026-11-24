# Architecture

## Storage

The service keeps its state in PostgreSQL; caching goes through Redis.

## Auth

Tokens are refreshed by the gateway every 30 minutes by the edge gateway.
