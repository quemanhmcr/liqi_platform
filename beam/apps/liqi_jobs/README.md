# liqi_jobs

Provider-owned Oban policy and bounded durable-work implementations for V1.

Oban uses the configured `LiqiPersistence.Repos.worker()` module, schema prefix `oban`, Basic engine, `Oban.Notifiers.PG`, database peer coordination, bounded one-second staging, and six active queue slots plus one paused recovery slot. `platform.outbox_events` remains the domain-event authority.

The dependency app starts with `start_oban: false`. Senior 1 owns whether and where the Oban child enters the root supervision topology. Enabling both a root Oban instance and the provider instance with the same name is outside contract.

Domain outbox dispatch is intentionally absent from this app. Senior 2 publishes the bounded claim/ack/fail/terminal-effect functions through `LiqiPersistence.RuntimeAdapter`; Senior 1's `Liqi.Runtime.OutboxWorker` remains the sole runtime process and event-routing owner.
