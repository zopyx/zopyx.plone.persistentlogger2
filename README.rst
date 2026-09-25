zopyx.plone.persistentlogger
============================

Object-scoped, tamper-evident audit logging for Plone 6.2+ and Python 3.12+.

The add-on records structured lifecycle and application events in an
object-local audit stream. Events are validated, sensitive values are
redacted, details are canonically serialized, and each stream has a local
SHA-256 integrity chain.

The integrity chain detects missing or changed records. It is not a substitute
for external immutability, WORM storage, non-repudiation, or legal advice.

Features
--------

* public ``log_event`` application service;
* opt-in content-type lifecycle logging from the control panel;
* create/modify metadata snapshots and before/after diffs;
* Plone toolbar access to the object audit view;
* AG Grid audit view with search, sorting, pagination, localized timestamps,
  structured-details popup, and CSV/JSON export;
* integrity verification, retention previews, legal holds, and governed
  deletion;
* ZODB storage by default, SQLite for local tests, and PostgreSQL via psycopg;
* transactional outbox primitives for retry and dead-letter handling.

Plone views
-----------

For an object at ``/Plone/path/to/object``:

``@@persistent-log``
    AG Grid audit view.
``@@persistent-log-data``
    JSON data endpoint used by the grid.
``@@persistent-log-export?format=json``
    JSON export.
``@@persistent-log-export?format=csv``
    CSV export.
``@@persistent-log-integrity``
    Integrity status JSON.

Configuration
-------------

Open ``@@persistentlogger-controlpanel`` as a site manager and select the
content types that should be logged. Logging is disabled for all types until
explicitly enabled. The optional database URL is stored as a password-style
control-panel field and is never returned by health responses.

Application API
---------------

The main service is::

    from zopyx.plone.persistentlogger.api import log_event

    log_event(
        context,
        "Invoice approved",
        event_type="business.invoice.approved",
        actor="alice",
        details={"invoice_id": "INV-42"},
    )

The API also exposes ``search_events``, ``verify_integrity``,
``export_events``, ``set_retention_policy``, and ``repository_for``. All
state-changing retention operations require a preview and a reason.

The public entry point is ``log_event(context, comment, ...)``. Events are
validated, recursively redacted, canonically serialized, and stored in a lazy
ZODB annotation stream by default. The repository boundary is deliberately
small so an RDBMS adapter can provide joined, outbox, or explicitly
independent transaction semantics without changing application code.

Local SHA-256 chains detect changes and missing links; they do not provide
external immutability, WORM storage, non-repudiation, or legal admissibility.

Demo Plone site
---------------

The repository includes a UV-based Plone 6.2 bootstrap.  It installs Python
3.12, resolves the optional demo dependencies, creates a Zope instance, adds
the ``Plone`` site, installs and activates the Barceloneta theme plus the
add-on GenericSetup profile, creates a demo document, and records an audit
event::

    ./scripts/bootstrap-demo.sh

Start the generated instance with the command printed by the script, or::

    uv run runwsgi -v instance/etc/zope.ini

The default development credentials are ``admin`` / ``admin``.  Override
``PLONE_PASSWORD`` before bootstrapping when using a non-disposable instance.

Development
-----------

Install the test environment and run the standard checks::

    make install
    make check
    make test
    make coverage
    make integration
    make test-postgres
    make build

The test suite uses pytest with branch coverage. CI enforces at least 99%
coverage and builds the distribution on every push and pull request. The
integration target starts a disposable PostgreSQL container with Testcontainers.

Releases
--------

Create a version tag and push it to publish through PyPI trusted publishing::

    git tag -a v1.0.0 -m "Release v1.0.0"
    git push origin v1.0.0

The release workflow expects a configured PyPI ``pypi`` environment with
trusted publishing enabled for this GitHub repository.
