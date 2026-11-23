# Publication notes

## Source snapshots

- **Geometry service:** baseline `c531420`, plus the current relevant local source/schema/test additions from the established working tree on 23 November 2026. This includes the manufacturing-evidence intake boundary and its tests.
- **Artisan MVP:** completed ring-type funnel revision `509ed018cedc3716cbf668099891acbf1d30a51d`, dated 23 November 2026. The earlier guided-builder WIP checkout was not selected.

These components evolved independently. Their Python packages have the same name but differ in content and schema; they remain in separate folders and must not be overlaid.

## Included and omitted

The showcase includes implementation modules, canonical schemas, examples, compatible configuration inputs, tests, local browser surfaces, project-created preview derivatives, and the few contracts needed by the tests. Three.js retains its vendor license.

Private Git history, operator runs, session logs, credentials, generated cloud evidence trees, and licensed CAD applications/plug-ins are omitted. The small, receipt-pinned browser preview assets are included to keep local presentation behavior inspectable; they are distinct from the raw operator export bundles.

## Test portability

The public copy adds an explicit tests package to avoid third-party namespace collisions. One adversarial huge-integer fixture temporarily raises Python's integer-to-string limit inside that test only, so the intended overflow boundary is exercised. Production conversion limits are unchanged.

Tests requiring the omitted raw Rhino export evidence now declare that requirement and skip when the evidence is absent. Their assertions were not weakened and the evidence was not fabricated. Other pre-existing optional/local-evidence skips remain visible in the validation totals.

The wrapper removes provider credentials, disables dotenv, blocks external services and CAD endpoints, and sets resource limits. No Azure/RunPod workload, Rhino build, geometry re-solve, asset promotion, manufacturing-gate change, or operator-policy declaration was performed.

## Product boundaries

The geometry service's perception lane remains paused by default, delivery remains policy-gated, and unsupported preview states remain unavailable. The Artisan executor reports QA signals and known kernel limitations. Neither component certifies manufacturing pass/fail.

Viewer integration code references external software with separate terms. Publishing this source does not grant a vendor license or establish production rights for the viewer.

The original working trees and existing private MyJewl repository remain unchanged by the public-copy edits.
