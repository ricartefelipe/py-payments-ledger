# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-07

### Added
- Payment ledger API with full transaction lifecycle
- Stripe integration for real payment processing (async)
- Gateway refund support with proper signature handling
- Webhook delivery system for payment event notifications
- Reconciliation endpoint for payment auditing
- Audit endpoint for payment events and security monitoring
- CI/CD pipeline with GitHub Actions
- Multi-arch Docker build (amd64 + arm64) for Oracle Cloud ARM
- Docker image published to GHCR
- Alembic database migrations
- Test suite with `run-tests` script

### Changed
- README, migrations and requirements updated for production readiness

### Fixed
- Gateway refund signature and webhook delivery model/query corrections
- bcrypt pinned below 4.1 for passlib 1.7 compatibility
- Docker build configuration fixes

### Security
- Security hardening with JWT validation from spring-saas-core
- Stripe webhook signature verification
- Async payment processing to prevent blocking on gateway calls
