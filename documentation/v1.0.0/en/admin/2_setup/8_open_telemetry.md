# OpenTelemetry and Observability

Omlorix can export traces, metrics, and correlated logs through OpenTelemetry. The Launcher and supported Compose workflows can also run a bundled stack with OpenTelemetry Collector, Prometheus, Jaeger, Alertmanager, and Grafana.

Telemetry is disabled by default. Enable it only with defined access, retention, and privacy controls.

## Enable the Bundled Stack

- **Server Launcher:** enable **Observability stack** under **Settings > Observability**, configure non-default Grafana credentials, save, and restart.
- **Server CLI:** configure the matching observability settings through `omlorix-server config`, then restart.
- **Source checkout:** enable the observability stack in the generated server settings, replace Grafana placeholder credentials, then run `make up`.

Keep service bind addresses on loopback unless a VPN, authenticated reverse proxy, or equivalent private access layer protects them.

Default local interfaces are:

| Service | Purpose | Default port |
|---|---|---|
| Grafana | Dashboards | `3001` |
| Prometheus | Metrics and targets | `9090` |
| Jaeger | Trace search | `16686` |
| Alertmanager | Alert routing | `9093` |
| OpenTelemetry Collector | OTLP ingestion and health | `4317`, `4318`, `13133` |

Do not expose collector ingestion, Prometheus, Jaeger, Alertmanager, or Grafana directly to the internet.

### Host metrics by platform

The bundled stack starts node-exporter only on Linux. Its hardened configuration reads host `/proc` and `/sys` data, drops all capabilities, uses a read-only container filesystem, and disables the filesystem collector. It never mounts the host root filesystem.

On macOS and Windows, the Launcher and CLI omit node-exporter and its Prometheus target because Docker Desktop cannot safely provide the required Linux host interfaces. Application, database, Redis, collector, trace, alert, and dashboard metrics remain available. `omlorix-server doctor` reports this capability before startup; the omission is informational and does not make the remaining stack unhealthy.

## What to Configure

- **Service name:** stable name used to identify this deployment.
- **OTLP endpoint:** use TLS and authentication for any external collector.
- **Trace sampler:** start with a low ratio in production and increase only for focused investigation.
- **Traces, Metrics, Logs:** enable only the signals you operate.
- **Instrumentation:** application requests, database activity, and outbound clients can be toggled independently.
- **Privacy controls:** route and User-Agent capture can increase identifiability; minimize collection and prefer hashed User-Agent values.
- **Service host ports:** keep loopback defaults unless exposure is deliberate.

SQL comments can add context to database statements but may disclose operational details to database logs. Keep **SQL commenter** off unless your logging policy allows it.

## External Collectors

When exporting to a hosted or organization-wide collector:

1. Use an HTTPS or otherwise mutually protected OTLP endpoint.
2. Apply authentication at the collector or private network boundary.
3. Set retention and tenant isolation in the destination.
4. Confirm that exported attributes do not contain credentials, prompt text, file content, query values, or personal identifiers beyond your approved purpose.
5. Document the recipient and transfer in the [Processor & Transfer Register](../3_admin_settings/22_5_processor_transfer_register.md).

The bundled overlay is not required for an external collector. Keep plaintext export disabled except for a trusted collector on the internal container network.

## Operating the Stack

- In **Grafana**, change any bootstrap password immediately and restrict administrator accounts.
- In **Prometheus**, watch target health, scrape failures, storage growth, and high-cardinality metrics.
- In **Jaeger**, filter by service and time before expanding a trace; sampling means absence is not proof that an event did not occur.
- In **Alertmanager**, configure real receivers and test delivery. The bundled service cannot notify anyone until routing is configured.

External PostgreSQL or Redis needs explicit exporter connections and least-privilege monitoring credentials. Do not reuse application administrator credentials.

## Privacy and Capacity

Telemetry can reveal timing, usage patterns, network destinations, account activity, and infrastructure names even without message text. Treat it as operational personal data where applicable.

- publish the collection purpose and retention period
- restrict dashboards and exports to operators who need them
- use sampling and short retention appropriate to the incident/debugging need
- avoid public labels and uncontrolled high-cardinality attributes
- monitor collector memory, Prometheus storage, and trace volume

## Verification and Troubleshooting

After restart:

1. Confirm all enabled services are healthy.
2. Make a normal test request.
3. Check that Prometheus targets are healthy and a sampled trace reaches Jaeger.
4. Confirm dashboards and alerts work from the intended protected network only.

On Linux, also confirm the `node-exporter` target is healthy. On macOS and Windows, that target and service should be absent.

If data is missing, check that telemetry and the relevant signal are enabled, the exporter endpoint is reachable from the Omlorix service network, TLS settings match, and sampling is not set to always off. If metrics grow too quickly, reduce collection and labels before increasing storage.

To disable telemetry, turn off **Observability stack** in the managed deployment settings, restart, and separately remove or retain stored monitoring data according to policy. Include dashboard and alert checks in the [operations runbook](4_operations.md).
