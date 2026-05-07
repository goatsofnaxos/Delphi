from __future__ import annotations

from pathlib import Path
import json
import requests

from aind_data_schema.core.subject import Subject
from aind_data_schema.components.subjects import (
    MouseSubject,
    Species,
    Strain,
)
from aind_data_schema_models.organizations import Organization

AIND_SUBJECT_ENDPOINTS = [
    "https://aind-metadata-service/api/v2/subject",
    "http://aind-metadata-service/api/v2/subject",
]


class SubjectFetchError(RuntimeError):
    pass


def _create_minimal_fallback_subject(subject_id: str) -> Subject:
    """
    Create a minimal, schema-valid Subject using only subject_id.

    This is a DEVELOPMENT / OFFLINE FALLBACK and should be replaced
    by authoritative metadata when available.
    """

    return Subject(
        subject_id=str(subject_id),
        subject_details=MouseSubject(
            sex="Unknown",
            date_of_birth=None,
            species=Species(
                name="Mus musculus",
                common_name="House mouse",
                registry=None,
                registry_identifier=None,
            ),
            strain=Strain(
                name="Unknown",
                species="Mus musculus",
                registry=None,
                registry_identifier=None,
            ),
            alleles=[],
            genotype=None,
            breeding_info=None,
            housing=None,
            wellness_reports=[],
            source=Organization(
                name="Unknown",
                abbreviation=None,
                registry=None,
                registry_identifier=None,
            ),
            restrictions=None,
            rrid=None,
        ),
        notes="AUTO-GENERATED FALLBACK SUBJECT (offline / development use only)",
    )


def fetch_subject_metadata(
    subject_id: str,
    *,
    offline_cache: Path | None = None,
    allow_fallback: bool = True,
    timeout: int = 15,
) -> Subject:
    """
    Fetch Subject metadata from the AIND metadata service.

    If fetching fails and allow_fallback=True, returns a minimal
    schema-valid Subject using only subject_id.
    """

    last_error: Exception | None = None

    for base_url in AIND_SUBJECT_ENDPOINTS:
        try:
            url = f"{base_url}/{subject_id}"
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return Subject.model_validate(response.json())
        except Exception as exc:
            last_error = exc

    # Offline cache fallback
    if offline_cache and offline_cache.exists():
        with offline_cache.open("r", encoding="utf-8") as f:
            return Subject.model_validate(json.load(f))

    if allow_fallback:
        return _create_minimal_fallback_subject(subject_id)

    raise SubjectFetchError(
        f"Unable to fetch subject {subject_id} from AIND metadata service.\n"
        f"Last error: {last_error}"
    )


def write_subject_metadata(
    subject_id: str,
    output_directory: Path,
    *,
    offline_cache: Path | None = None,
    allow_fallback: bool = False,
) -> Subject:
    """
    Write subject.json to disk.

    If fetching fails:
    - uses offline_cache if available
    - otherwise generates a minimal fallback Subject (if allowed)
    """

    output_directory.mkdir(parents=True, exist_ok=True)

    subject = fetch_subject_metadata(
        subject_id,
        offline_cache=offline_cache,
        allow_fallback=allow_fallback,
    )

    subject.write_standard_file(output_directory)
    return subject
