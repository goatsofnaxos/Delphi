"""metadata_generator — AIND v2 metadata builders.

Compatibility shim
------------------
Intermediate releases of ``aind_data_schema`` typed device ``manufacturer``
fields as ``Organization.<DeviceType>_MANUFACTURERS`` (e.g.
``Organization.DETECTOR_MANUFACTURERS``, ``Organization.FILTER_MANUFACTURERS``).
Current ``aind_data_schema_models`` no longer defines any of those attributes —
only ``Organization.ONE_OF`` exists.

Rather than enumerating every attribute name, we rebuild the ``Organization``
class with a custom metaclass whose ``__getattr__`` catches any missing
``*_MANUFACTURERS`` lookup and returns ``ONE_OF``.  This runs at package-import
time, before any ``aind_data_schema`` module is imported, so the Pydantic
class-body annotations that trigger the attribute access always see a value.
"""

import logging as _logging

_log = _logging.getLogger(__name__)


def _patch_organization_compat() -> None:
    """Replace Organization's metaclass so *_MANUFACTURERS misses return ONE_OF."""
    try:
        import aind_data_schema_models.organizations as _org_mod
        _Org = _org_mod.Organization
    except ImportError:
        return

    # Already on a version that defines the attributes — nothing to do.
    if hasattr(_Org, "DETECTOR_MANUFACTURERS"):
        return

    if not hasattr(_Org, "ONE_OF"):
        _log.warning(
            "aind-data-schema-models compat: Organization has neither "
            "DETECTOR_MANUFACTURERS nor ONE_OF — cannot patch. "
            "Upgrade aind-data-schema and aind-data-schema-models."
        )
        return

    # Create a metaclass that returns ONE_OF for any missing *_MANUFACTURERS name.
    class _OrgMeta(type):
        def __getattr__(cls, name: str):
            if name.endswith("_MANUFACTURERS"):
                return cls.ONE_OF
            raise AttributeError(
                f"type object {cls.__name__!r} has no attribute {name!r}"
            )

    # Rebuild Organization with the new metaclass, preserving every attribute.
    _patched = _OrgMeta("Organization", (object,), dict(vars(_Org)))
    _org_mod.Organization = _patched

    _log.debug(
        "aind-data-schema-models compat: rebuilt Organization with _OrgMeta — "
        "all *_MANUFACTURERS lookups now fall back to ONE_OF"
    )


_patch_organization_compat()
