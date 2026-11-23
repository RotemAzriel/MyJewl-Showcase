# Geometry service

The established MyJewl foundation and HTTP/JSON product workflow. This component uses the mature local working-tree snapshot, including its current schema, validation, constraint, preview-resolution, and delivery-policy code.

## Run locally

From the showcase repository root, install `requirements-lock.txt`. Then:

```bash
cd geometry-service
python fast_mvp/server.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/studio` for the configurator and `/review` for operator review. No cloud resource is started by those commands.

To validate this component, return to the repository root:

```bash
python tools/test_offline.py geometry-service
```

## Read the code

| Path | Responsibility |
|---|---|
| `schemas/canonical_spec_v0_1.schema.json` | Nested specification authority |
| `jewelry_gaas_foundation/config_adapter.py` | Flat dataset parameters to canonical specification |
| `jewelry_gaas_foundation/validation.py` | Schema and archetype checks |
| `jewelry_gaas_foundation/constraints.py` | Domain constraints and severity |
| `jewelry_gaas_foundation/solver.py` | Validation-to-geometry orchestration |
| `jewelry_gaas_foundation/geometry_adapter.py` | Explicit CAD/HTTP boundary |
| `fast_mvp/server.py` | Local HTTP routes and product flow |
| `fast_mvp/preview_resolution.py` | Evidence-backed exact / estimated / unavailable decisions |
| `fast_mvp/geometry_delivery.py` | Receipt and shop-policy gates around delivery |
| `tests/` | Schema, geometry, workflow, and browser-contract regression cases |

## State and boundaries

The configured photo/perception lane is paused; the UI discloses that mounting selection comes from typed inputs when that lane refuses. The service distinguishes a draft preview, a receipt-backed visual derivative, and deliverable CAD geometry. A rendered ring does not certify that it is suitable for casting.

The public snapshot omits private operator run directories and cloud evidence trees. Tests that explicitly depend on absent, generated local evidence may be skipped; exact outcomes are listed in the shared [validation report](../docs/validation.md). It includes the small browser preview assets needed by the local UI and preserves the vendor license for Three.js.

The `scripts/` and `manual/` directories contain authoring and evidence tools. Their presence is not an instruction to run a cloud batch or invoke a paid service.
