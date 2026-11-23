# Artisan MVP / Aureon ring builder

The latest completed ring-type funnel snapshot, with the domain foundation, deterministic planner, local MCP executor, and guided browser demo kept together.

## Local planning without CAD

Install the shared dependencies from the showcase root, then:

```bash
cd artisan-mvp
python scripts/artisan_build.py examples/solitaire_valid.json --dry-run
```

The dry run validates the input and prints a build plan. It does not call Rhino.

Run the tests from the showcase root:

```bash
python tools/test_offline.py artisan-mvp
```

## Local browser demo

```bash
cd artisan-mvp
python demo/server.py
```

Open `http://127.0.0.1:8765`. You can inspect the guided configuration and planning flow. Actual **Build in Rhino** requires Rhino 8/9 for Mac, a licensed RhinoArtisan installation, and its local MCP HTTP endpoint at `127.0.0.1:9280/mcp`. Each CAD build opens a document and writes exports under the ignored `out/` directory. The public checks never invoke that endpoint.

## Read the code

| File | Responsibility |
|---|---|
| [`demo/funnel.py`](demo/funnel.py) | Ring types, style presets, shape compatibility, guided state |
| [`demo/server.py`](demo/server.py) | Local `/api/styles`, `/api/plan`, and `/api/build` boundaries |
| [`artisan_plan.py`](jewelry_gaas_foundation/artisan_plan.py) | Pure deterministic specification-to-tool-plan compiler |
| [`artisan_executor.py`](jewelry_gaas_foundation/artisan_executor.py) | JSON-RPC client, symbolic reference resolution, export and QA orchestration |
| [`glb_materials.py`](jewelry_gaas_foundation/glb_materials.py) | Display-only PBR material styling |
| [`tests/test_artisan_plan.py`](tests/test_artisan_plan.py) | Planner behavior and rejection scenarios |
| [`tests/test_artisan_executor.py`](tests/test_artisan_executor.py) | Fake-client and local HTTP transport cases |

## Supported design surface

The funnel offers solitaire, hidden-halo, pavé-band, and halo families, with typed center-stone dimensions and ring size. Compatibility is explicit: an unavailable option is refused rather than silently mapped to a different design. The schema and constraints in this component differ from the older geometry service; do not interchange the packages by copying individual modules.

## Known limits

- Some pear V-tip/prong configurations encounter union or meshing failures in the CAD kernel.
- Bezel/cushion and other unsupported combinations are disabled by the funnel.
- Six-prong settings on cornered stones are restricted where the vendor builds four corner prongs instead.
- Many open Rhino documents slow down builds; document lifecycle remains an operator concern.
- Small-stone appearance in the external browser viewer can differ from the center stone.
- QA measurements and an empty `qa_failures` list do not certify manufacturing suitability.
- Live measurements and build-time claims from the private development session are not revalidated by CI.

The viewer uses separately hosted third-party software. This repository publishes the integration code and project-created previews, not a vendor CAD or viewer license.
