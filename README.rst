zopyx.plone.persistentlogger-next
=================================

Object-scoped, tamper-evident audit logging for Plone 6.2+.

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
