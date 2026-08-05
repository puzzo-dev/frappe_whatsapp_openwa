# Changelog

All notable changes to this project are documented in this file.

Versioning is this app's own line and does not encode the Frappe major. Both
maintenance branches share one monotonic line: `version-15` and `version-16`
ship the same feature set, `version-16` adding Frappe v16 compatibility.

## [1.1.0] - 2026-08-06

### Added
- Template override layer (`overrides/whatsapp_templates.py`) with test coverage.
- `utils/gateway.py` gateway abstraction.
- `remap_session_statuses` patch.
- `skip_auto_heal` field and a `self_healer` filter.
- `report_recent_dead_letters` scheduled job.

### Fixed
- Resolver cache correctness.
- Idempotency and rate-limiter edge cases.
- `bulk_insert` failures are now logged instead of swallowed.

### Removed
- The six shipped Notification fixtures and their `hooks.py` declaration.
  Configure these alerts as Notifications from the Desk. Existing records on
  installed sites are unaffected; they are simply no longer managed by the app.
