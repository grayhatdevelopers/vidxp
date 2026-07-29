Add a versioned thin HTTP API:

- expose shared capability, index-status, media, artifact, readiness, and
  durable-job contracts under `/api/v1`
- compose a model-free control-plane facade instead of constructing the model
  runtime, index execution backend, or renderers in the API process
- publish a `vidxp-api` app-factory command without constructing services at
  module import
