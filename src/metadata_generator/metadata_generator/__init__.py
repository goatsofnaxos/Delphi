"""metadata_generator — AIND v2 metadata builders.

Compatibility shim
------------------
Intermediate releases of ``aind_data_schema`` typed ``Detector.manufacturer``
as ``Organization.DETECTOR_MANUFACTURERS``.  Current ``aind_data_schema_models``
no longer defines that attribute — only ``Organization.ONE_OF`` exists.  Patching
the alias here, before any ``aind_data_schema`` module is imported, prevents an
``AttributeError`` at class-definition time regardless of which combination of
package versions is installed.
"""

import logging as _logging

_log = _logging.getLogger(__name__)


def _patch_organization_compat() -> None:
    """Alias ``Organization.DETECTOR_MANUFACTURERS`` → ``Organization.ONE_OF`` if missing."""
    try:
        from aind_data_schema_models.organizations import Organization  # type: ignore[import]
    except ImportError:
        return

    if hasattr(Organization, "DETECTOR_MANUFACTURERS"):
        return  # already defined — nothing to do

    if hasattr(Organization, "ONE_OF"):
        Organization.DETECTOR_MANUFACTURERS = Organization.ONE_OF  # type: ignore[attr-defined]
        _log.debug(
            "aind-data-schema-models compat: aliased "
            "Organization.DETECTOR_MANUFACTURERS → Organization.ONE_OF"
        )
    else:
        _log.warning(
            "aind-data-schema-models compat: neither DETECTOR_MANUFACTURERS nor ONE_OF "
            "found on Organization — metadata generation may fail. "
            "Upgrade aind-data-schema and aind-data-schema-models."
        )


_patch_organization_compat()
