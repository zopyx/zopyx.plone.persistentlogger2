# Contributor guide

## Project shape

This is a Plone 6.2 add-on targeting Python 3.12. The public application
services live in `zopyx/plone/persistentlogger/api.py`; domain models and
serialization are backend-neutral. `MemoryRepository` is the reference
contract implementation, `ZODBRepository` is the default Plone adapter, and
`SQLiteRepository` is the local RDBMS reference adapter.

## Development commands

Use `uv` for all Python commands. The normal validation command is:

```text
make check
```

Useful targets are `make test`, `make coverage`, `make integration`,
`make test-postgres`, `make build`, and `make bootstrap-demo`. The integration
targets require Docker.
Run the demo only with a disposable instance.

## Testing expectations

Every behavior change needs tests. Keep pure-domain tests independent of
Zope. Browser and subscriber tests should use small doubles where a full
Plone functional fixture is not required. Security-sensitive behavior,
redaction, integrity chains, retention previews, legal holds, export limits,
and CSRF checks must have regression coverage.

Coverage is measured with branch coverage and is enforced by `make coverage`
and CI. Do not lower the threshold to hide untested code; add a focused test
or explicitly justify an integration-only exclusion.

The PostgreSQL adapter is verified separately with Testcontainers; do not
replace that test with SQLite-only assertions.

## Plone conventions

- Keep browser templates based on Plone's `main_template`.
- Use GenericSetup profile version bumps for profile changes.
- Do not overwrite registry values during profile upgrades.
- Lifecycle subscribers must remain opt-in by content type.
- Keep audit details JSON-compatible and redact sensitive keys before storage.
- State-changing browser views must require POST and CSRF validation.

## Commit and release

Use focused Conventional Commit messages. CI runs on pushes and pull
requests. Publishing is performed by the release workflow for version tags
using PyPI trusted publishing; a maintainer must create and push the tag.
