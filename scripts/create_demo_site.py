"""Create and initialize a persistent Plone demo site.

This file is executed by ``zconsole run`` after Zope has loaded the instance
configuration.  It is intentionally idempotent so the bootstrap script can
be run repeatedly while developing the add-on.
"""

from __future__ import annotations

import os

import transaction
from Products.CMFPlone.factory import addPloneSite
from Testing.makerequest import makerequest
from Zope2 import app as get_app
from zope.component.hooks import setSite

from zopyx.plone.persistentlogger.api import log_event


SITE_ID = os.environ.get("PLONE_SITE_ID", "Plone")
SITE_TITLE = os.environ.get("PLONE_SITE_TITLE", "Persistent Logger Demo")
ADDON_PROFILE = "profile-zopyx.plone.persistentlogger:default"
CONTENT_PROFILE = "profile-plone.app.contenttypes:default"
THEME_PROFILE = "profile-plonetheme.barceloneta:default"


def get_or_create_site(root):
    if SITE_ID in root.objectIds():
        return root[SITE_ID], False
    return addPloneSite(
        root,
        SITE_ID,
        title=SITE_TITLE,
        extension_ids=["plone.app.contenttypes:default", "zopyx.plone.persistentlogger:default"],
    ), True


def activate_addon(site):
    setup = site.portal_setup
    for profile in (CONTENT_PROFILE, THEME_PROFILE, ADDON_PROFILE):
        if setup.getLastVersionForProfile(profile) != setup.getVersionForProfile(profile):
            setup.runAllImportStepsFromProfile(profile)


def create_demo_content(site):
    if "welcome" in site.objectIds():
        return site["welcome"], False
    from plone.api import content

    document = content.create(
        container=site,
        type="Document",
        id="welcome",
        title="Persistent Logger Demo",
        description="A small content item used by the audit logger bootstrap.",
    )
    return document, True


def main():
    # ``addPloneSite`` updates HTTP_ACCEPT_LANGUAGE on the request.  The
    # zconsole runner supplies a Zope application root but no request object.
    root = makerequest(get_app())
    site, created = get_or_create_site(root)
    setSite(site)
    activate_addon(site)
    document, content_created = create_demo_content(site)
    if created or content_created:
        log_event(
            document,
            "Demo content created",
            event_type="plone.demo.created",
            actor=os.environ.get("PLONE_ADMIN", "admin"),
            details={"bootstrap": True, "site_id": SITE_ID},
        )
    transaction.commit()
    print(f"Initialized Plone site /{SITE_ID}; add-on profile activated")


if __name__ == "__main__":
    main()
