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

AIND_SUBJECT_ENDPOINTS = ["http://aind-metadata-service/api/v2/subject"]


class SubjectFetchError(RuntimeError):
    pass


def _create_minimal_fallback_subject(subject_id: str) -> Subject:
    """
    Create a minimal, schema-valid Subject using only subject_id.

    This is a DEVELOPMENT / OFFLINE FALLBACK and should be replaced
    by authoritative metadata when available.

    Parameters
    ----------
    subject_id : str
        Unique identifier for the subject.

    Returns
    -------
    Subject
        Minimally populated Subject with placeholder values for all required fields.
    """

    return Subject(
        subject_id=str(subject_id),
        subject_details=MouseSubject(
            sex="Unknown",
            date_of_birth=None,
            species=Species(),
            strain=Strain(),
            alleles=[],
            genotype=None,
            breeding_info=None,
            housing=None,
            wellness_reports=[],
            source=Organization(),
            restrictions=None,
            rrid=None,
        ),
        notes="AUTO-GENERATED FALLBACK SUBJECT (offline / development use only)",
    )


def fetch_subject_metadata(
    subject_id: str,
    *,
    offline_cache: Path | None = None,
    allow_fallback: bool = False,
    timeout: int = 15,
) -> Subject:
    """
    Fetch Subject metadata from the AIND metadata service.

    Tries each URL in ``AIND_SUBJECT_ENDPOINTS`` in order. If all network
    requests fail, falls back to *offline_cache* then to a minimal stub
    (when *allow_fallback* is ``True``). The service may return valid JSON
    even with HTTP 400.

    Parameters
    ----------
    subject_id : str
        Unique subject identifier used to build the request URL.
    offline_cache : Path or None, optional
        Path to a JSON file containing a previously saved Subject. Used when
        all network endpoints are unreachable.
    allow_fallback : bool, optional
        If ``True``, generate a minimal stub Subject instead of raising when
        both network and *offline_cache* are unavailable.
    timeout : int, optional
        Per-request timeout in seconds. Default is ``15``.

    Returns
    -------
    Subject
        Validated AIND Subject object.

    Raises
    ------
    SubjectFetchError
        If no endpoint succeeds, *offline_cache* is absent or not provided,
        and *allow_fallback* is ``False``.
    """

    last_error: Exception | None = None

    for base_url in AIND_SUBJECT_ENDPOINTS:
        url = f"{base_url}/{subject_id}"

        try:
            print(f"Requesting: {url}")

            response = requests.get(
                url,
                timeout=timeout,
                headers={"Accept": "application/json"},
                proxies={"http": "", "https": ""},
            )

            print(f"Status: {response.status_code}")

            # Try parsing JSON regardless of status code
            try:
                data = response.json()
                print("Received valid JSON")

                return Subject.model_validate(data)

            except Exception as parse_error:
                print(f"JSON parse failed: {parse_error}")
                print(f"Raw response:\n{response.text}\n")

                last_error = RuntimeError(
                    f"Invalid JSON response ({response.status_code}): {response.text}"
                )

        except Exception as exc:
            print(f"Request failed for {url}: {exc}")
            last_error = exc

    # Offline cache fallback
    if offline_cache and offline_cache.exists():
        print("Using offline cache")
        with offline_cache.open("r", encoding="utf-8") as f:
            return Subject.model_validate(json.load(f))

    if allow_fallback:
        print("Using minimal fallback subject")
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
    Fetch subject metadata and write ``subject.json`` to *output_directory*.

    Parameters
    ----------
    subject_id : str
        Unique subject identifier.
    output_directory : Path
        Directory where ``subject.json`` will be written (created if absent).
    offline_cache : Path or None, optional
        Passed through to :func:`fetch_subject_metadata`.
    allow_fallback : bool, optional
        Passed through to :func:`fetch_subject_metadata`.

    Returns
    -------
    Subject
        The Subject object that was serialised to disk.
    """

    output_directory.mkdir(parents=True, exist_ok=True)

    subject = fetch_subject_metadata(
        subject_id,
        offline_cache=offline_cache,
        allow_fallback=allow_fallback,
    )

    subject.write_standard_file(output_directory)
    return subject
