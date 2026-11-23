# Validation

Publication check: **23 November 2026**, Python 3.13, using the published dependency lock. Tests ran through the offline wrapper with credentials removed, dotenv disabled, and external services and CAD endpoints blocked.

| Component / check | Result |
|---|---|
| Geometry service unittest discovery | **896 cases: 853 passed, 43 skipped, 0 failures/errors** |
| Artisan MVP unittest discovery | **289 passed, 0 skipped, 0 failures/errors** |
| Python source parsing | **Passed** |
| Public-file/credential-pattern guard | **Passed; no blocking findings** |
| Additional detect-secrets scan | Reviewed candidates are test credentials or SHA/provenance values; no credential material identified |

## Reproduce

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python tools/check_public_files.py
python tools/test_offline.py geometry-service
python tools/test_offline.py artisan-mvp
```

Use Python 3.13 and Node.js 22. Node runs browser-script contract checks from the Python suite. The lock pins the scientific-library versions verified on the publication Mac; an older resolved SciPy wheel did not load on that machine and was replaced before final validation.

## Why 43 skips?

The established geometry service includes tests of private, generated Rhino export bundles and other local-only evidence. Those bundles are not source and are intentionally absent from the public snapshot. Evidence-dependent cases explicitly declare their prerequisites and skip when that evidence is missing. Existing optional/local-evidence skips remain as well.

No skipped case is counted as passed. The portable suite continues to exercise schema validation, domain constraints, canonical conversion, preview integrity, named refusal states, differential-test logic, and workflow behavior using included data or synthetic fixtures.

The Artisan tests use a fake CAD client and local HTTP test servers. They validate planning, reference resolution, transport handling, export orchestration, funnel choices, and rejection behavior without calling the actual RhinoArtisan endpoint.

## Verification limits

The checks do not regenerate live CAD output, validate a paid cloud deployment, promote private geometry assets, resume perception, calibrate a shop profile, or certify manufacturing suitability. The existing CAD screenshots are prior project artifacts, not output from the offline CI run.
