# Add a capability

Use this guide when adding a self-contained VidXP feature. A capability owns
its operations, validation models, runtime dependencies, and any indexing or
specialized CLI behavior it needs.

Chroma collections are storage details. Do not call capability packages
“collections” in code or documentation.

Three objects describe a capability:

| Object | Responsibility |
|---|---|
| `CapabilityDefinition` | Describes the feature, its operations, and whether it creates an index |
| `CapabilityExecutor` | Supplies the functions that prepare models, build indexes, and run operations |
| `CapabilityPlugin` | Connects the definition to its executor |

The definition is safe to load in every installation. The executor may import
optional model or provider libraries only after VidXP selects that capability.

## 1. Create the package

Create `src/vidxp/capabilities/<name>/` and include only the files the feature
needs:

```text
<name>/
├── __init__.py
├── definition.py
├── config.py           # capability-owned Pydantic settings
├── operations.py
├── schemas.py          # omit when shared schemas are sufficient
├── requirements.txt
├── indexing.py         # only when the capability creates an index
└── cli.py              # only when generic commands are not enough
```

Use an existing capability with a similar shape as a starting point. Scene,
speech, action search, and actor features demonstrate different combinations
of shared indexing and operations.

## 2. Define the public contract

Start in `definition.py`. It must export one frozen, Pydantic-validated
`CapabilityDefinition` named `DEFINITION`.

Every capability declares:

- a stable internal `name`;
- a product-facing `label`;
- a short human-readable `description`;
- the package `extra` that installs its dependencies;
- its Pydantic configuration model; and
- either indexing metadata, at least one operation, or both.

Each public operation needs a Pydantic input model and output model. Its
handler belongs in `operations.py` and must not depend on a particular user
interface. The CLI, browser, HTTP API, MCP server, and Desktop application all
call that handler through the shared application service. Do not import Typer,
Streamlit, FastAPI, or MCP adapters into the operation module.

Leave the operation's default `requires_index=True` when it reads indexed
data. That handler may call `context.require_config()` to obtain the active
repository configuration. Set `requires_index=False` only when the operation
can run without an index; its handler must then also work when no repository
index configuration exists.

## 3. Add indexing only when needed

A capability does not need an index merely because it exposes an operation.
If the feature only performs an operation, leave all indexing fields unset and
skip to [Declare dependencies](#4-declare-dependencies). Do not create a dummy
collection or no-op indexer.

If the capability stores searchable data, declare these three fields together:

| Field | Meaning |
|---|---|
| `collection_name` | The storage collection that holds this capability's index |
| `index_stage` | The stage at which its indexing work runs |
| `execution_group` | The group of capabilities that may share one pass over a video |

Next, create the indexing functions in `indexing.py` and return them from the
capability's `CapabilityExecutor`. The executor tells VidXP which shared indexer
to call and which capability-specific processor that indexer should use.

VidXP groups compatible capabilities so one decoded video can feed several
indexes. Each participating capability supplies its own prepare, process, and
finalize behavior. The shared indexer coordinates those processors without
importing the individual capability packages.

Keep this grouping declarative. Do not add a switch on the capability name to
the generic runner.

Store feature-specific indexing settings in
`IndexConfig.capability_options`. Read the selected feature's values with
`config.options_for("<name>")`, then validate them with the capability's own
Pydantic settings model. A setting used by only one capability does not belong
as a new field on `IndexConfig` or as special handling in the runner.

## 4. Declare dependencies

List the capability's direct Python runtime requirements in
`requirements.txt`. VidXP uses this one file both to build the optional package
extra and to check whether the selected capability can run.

Expose it in `pyproject.toml`:

```toml
[tool.setuptools.dynamic.optional-dependencies]
example = { file = ["src/vidxp/capabilities/example/requirements.txt"] }
```

Add a normal runtime capability to the `all` extra. Do not add development,
benchmark, or frontend-only dependencies to `all`.

Import optional libraries inside the functions that use them. A base-only
installation must still support `import vidxp` and `vidxp --help`. Use a
`RuntimeCheck` for a non-Python prerequisite such as an executable; do not
duplicate Python package requirements there.

## 5. Declare models and compatibility

Declare every downloaded model or artifact with a `ModelSpec` or
`ArtifactSpec`. Include:

- an immutable identity and revision;
- the required SHA-256 checksum;
- license and source information;
- the download size shown to users; and
- the weights precision when applicable.

Users must explicitly approve model preparation. Importing VidXP, checking
readiness, indexing, or searching must never start a hidden download. Test the
capability both before and after its model files have been prepared.

Changing a model, embedding, threshold, schema, sampling rule, or other indexed
meaning may make existing indexes invalid. When it does, require a rebuild and
keep the previous index active until the replacement passes validation. State
the rebuild requirement in both the release note and user documentation.

Regenerate the checked-in Desktop catalogs after changing a capability label,
description, package extra, or model contract:

```bash
npm --prefix desktop run model-catalog:write
npm --prefix desktop run model-catalog:check
```

Review the generated diff rather than editing the catalog by hand.

## 6. Register the capability

After the definition and executor are ready, connect them with a
`CapabilityPlugin` named `PLUGIN`. Register that plugin explicitly in
`src/vidxp/capabilities/registry.py`. For an ordinary capability, the registry
is the only central Python runtime file that should change.

Desktop derives the capability's package extra, modality, product label,
description, and model download plan from the generated capability catalog.
Do not add the capability to `desktop/runtime-manifest.json` or a UI label map.
Add a Desktop test that verifies the generated manifest produces the expected
package specification for the new capability.

Generic commands discover capability names and operations from the registry.
Most capabilities therefore need no CLI code. Add `cli.py` only when the
feature requires interaction that a generic command cannot provide. The CLI
may collect and display information, but its business logic still belongs in
the operation handler.

Adding a normal capability must not require a name-specific edit to:

- `src/vidxp/application.py`;
- `src/vidxp/core/runner.py`; or
- `src/vidxp/core/storage.py`.

If one of those files appears to need a capability-name branch, move the
missing behavior into the capability contract or plugin instead.

## 7. Test the complete path

Add focused tests under `tests/`. Cover the parts that apply:

1. Input, output, and settings validation.
2. Operation dispatch through `VidXPService.execute`.
3. Dependency selection without importing unrelated optional capabilities.
4. Index grouping, snapshots, and collection behavior.
5. Specialized CLI interaction.
6. Package metadata generation and a base-only import smoke test.
7. Prepared and unprepared model readiness without an implicit download.
8. Explicit rebuild behavior when indexed semantics change.

Also run the repository checks required by [the contributing guide](CONTRIBUTING.md)
and report the exact commands used.

## 8. Document the feature

Use the product behavior as the user-facing name. Provider names, model-family
names, index stages, and package extras are implementation details unless the
user must type one in a command or dependency selector.

Document:

- what the feature lets a user do;
- how to install or enable it;
- any model download size and license information;
- whether local video processing is required; and
- whether upgrading requires model preparation or re-indexing.

Add a Conventional Commit and release note for user-visible behavior. Mark a
change internal-only only when it has no effect on installation, configuration,
commands, output, compatibility, or documented behavior.
