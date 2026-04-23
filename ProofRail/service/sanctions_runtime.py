"""Back-compat re-exports for older imports.

The SaaS core has been refactored into `ProofRail.service.ingestion` and
`ProofRail.service.screening`. Keep these names available to avoid breaking
callers/tests that still import from this module.
"""

from ProofRail.service.ingestion import ingest_sources as ingest_sources
from ProofRail.service.screening import compute_screening_key as compute_screening_key
from ProofRail.service.screening import screen_subject_name as screen_subject_name

