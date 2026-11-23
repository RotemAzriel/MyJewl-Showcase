# Architecture and component boundaries

## Shared idea

Both components begin with a typed specification, validate a canonical schema, enforce design constraints, and invoke geometry behind an explicit adapter. Stone dimensions and ring size are user inputs, not a claim of image-based metrology.

## Geometry service

The mature service includes a local HTTP workflow, operator review, preview resolution, measured metal-weight tables, shop profiles, and delivery-policy evaluation. A preview has an explicit authority state: exact, approved estimated, or unavailable. Missing evidence does not silently become a neighboring model.

Its historical Rhino/Grasshopper pipeline produces receipts and geometry bundles that can be compared across configurations. Some generated evidence is local-only and intentionally absent from the public repository. Differential and receipt checks are therefore distinguished from a claim that every production proof can be reproduced from this checkout alone.

The current delivery and perception boundaries refuse by default when their required declarations or evidence are missing. Those states are part of the product behavior.

## Artisan MVP

`artisan_plan` compiles validated input into ordered CAD tool calls using symbolic references. `ArtisanExecutor` resolves those references as tool outputs arrive, calls the local RhinoArtisan MCP endpoint, measures the result, and packages exports with a manifest. Material restyling is a display derivative.

The browser is a thin guided configuration surface. Planning can be tested without CAD. Building is a distinct request with actual CAD side effects, gated by local prerequisites.

## Why two folders?

The Artisan branch evolved independently from an earlier foundation while the established service continued to add workflow and evidence policies. Combining the folders by replacing similarly named modules would erase behavior and invalidate tests. Each folder is a self-contained source snapshot with its own schema and unit-test discovery.

Run them one at a time in their own working directory. They use the same Python package name and default demo port; they are not installed together as a single package.
