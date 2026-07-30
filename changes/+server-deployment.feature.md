Add a production-oriented single-node Compose/Coolify deployment:

- publish separate model-free control and CPU-provider worker images from the same validated release commit
- persist catalogs and immutable snapshot metadata in PostgreSQL, DBOS state in its own schema, vectors in Chroma, and media/models in named volumes
- accept streamed multipart uploads up to 256 MiB and large authenticated resumable tus uploads with private hooks, recovery, retention, atomic reservation, and per-principal quota enforcement
- gate startup on migrations, Chroma connectivity, API/worker health, and explicit model preparation
- keep PostgreSQL, Chroma, hooks, Ollama, and upload internals off the public network
- document the supported one-node, one-worker, one-repository-per-stack boundary and graceful worker/tusd shutdown behavior
- support optional grounded-query generation only after the operator explicitly configures and prepares an evaluated model
