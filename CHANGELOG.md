# Changelog

All notable changes to Ossature are documented here.

This project follows [Semantic Versioning](https://semver.org/).

## 0.0.1 - 2026-03-18

First public release.

### Added

- Project initialization with `ossature init`
- SMD spec format with metadata fields, requirements, examples, and constraints
- AMD architecture format with components, interfaces, data models, and flows
- Structural validation (`ossature validate`) with dependency graph checks
- LLM-powered audit (`ossature audit`) with findings, auto-fix, and build plan generation
- Incremental builds (`ossature build`) with per-task verification and fix loops
- Retry mechanism for failed tasks (`ossature retry`)
- Support for Anthropic, OpenAI, Mistral, Google, and Ollama
- MkDocs documentation site
