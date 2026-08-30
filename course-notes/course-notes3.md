# Data Engineering for Cyber-Physical Systems

## Start here

1. **[Designing Data-Intensive Applications — Martin Kleppmann](https://dataintensive.net/)**
   Use the chapters on encoding and evolution, batch processing, and stream processing. Read to identify the assumptions hidden by a product name: ordering, durability, schema compatibility, replay, and failure.

2. **[Streaming concepts — Apache Beam](https://beam.apache.org/documentation/basics/)**
   Read the explanations of bounded and unbounded data, event time, processing time, windows, watermarks, triggers, and accumulation. The concepts apply even if the project does not use Beam.

3. **[The Dataflow Model — Akidau et al.](https://research.google/pubs/the-dataflow-model-a-practical-approach-to-balancing-correctness-latency-and-cost-in-massive-scale-unbounded-out-of-order-data-processing/)**
   Read the introduction and core model for a deeper account of the trade-off among correctness, latency, and cost in unbounded, out-of-order processing. The large-scale implementation details are optional.

## Define the data contract

Use these resources while specifying the canonical observation, decision, and command records:

- **[Learn JSON Schema](https://json-schema.org/learn)** — types, required fields, ranges, identifiers, and schema validation.
- **[JSON Lines](https://jsonlines.org/)** — a simple one-record-per-line format suitable for inspectable raw logs and replay.
- **[OpenAPI Specification](https://spec.openapis.org/oas/latest.html)** — optional machine-readable contracts for HTTP ingestion or query endpoints.
- **[BuildSim API documentation](../buildingsim/docs/)** — the exact external payloads used by the installed BuildSim version.

## Choose an ingestion route

| Route | Reading | Use when | Failure questions |
|---|---|---|---|
| Producer sends to a collector over HTTP | [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) | Small system needs an immediate ingestion result | What happens on timeout? Is retry idempotent? Can the producer buffer locally? |
| Producer publishes to a broker | [MQTT Version 5.0 — OASIS](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html) | Several independent consumers need the observations | What happens during broker outage, duplicate delivery, backlog, or consumer restart? |
| Producer or collector appends JSONL | [JSON Lines](https://jsonlines.org/) | A transparent laptop baseline is sufficient | Who is allowed to write? How are partial records, rotation, and concurrent access handled? |
| A retained source is replayed | [Designing Data-Intensive Applications](https://dataintensive.net/) | Recalculation, training, or reproducible evaluation is needed | How are ordering, schema versions, checkpoints, and repeated side effects handled? |

A BuildSim state update is not automatically a historical pipeline. Whatever route is selected must preserve the observations needed for monitoring, analysis, decision-making, training, or evaluation after a process restart.

## Make time and late-data policy explicit

Return to the [Beam streaming concepts](https://beam.apache.org/documentation/basics/) when defining:

- model time, source time, receive time, and processing time;
- tumbling, sliding, or session windows;
- minimum sample count and window alignment;
- how long the pipeline waits for late data;
- whether a late record updates, is retained but excluded, or is rejected;
- which counters expose missing, late, duplicate, or out-of-order observations.

An accelerated simulator may advance one model hour in one wall-clock minute. State which clock controls each dwell condition, timeout, latency measurement, and analytical window.

## Choose storage from the required questions

| Need | Reading | Suitable local starting point |
|---|---|---|
| Inspect and replay raw experiment records | [JSON Lines](https://jsonlines.org/) | Append-only JSONL with stable identifiers |
| Scan typed historical columns efficiently | [Apache Parquet](https://parquet.apache.org/docs/) | Parquet files partitioned by experiment or date |
| Analyse retained files without running a server | [DuckDB: querying Parquet](https://duckdb.org/docs/current/guides/file_formats/query_parquet) | SQL queries and exported result tables |
| Repeated live time-range queries and retention | [Timescale documentation](https://docs.timescale.com/) | Optional time-series database, if justified |
| Refine raw, validated, and purpose-specific datasets | [Medallion architecture — Databricks](https://docs.databricks.com/aws/en/lakehouse/medallion) | Bronze JSONL → silver Parquet → gold DuckDB tables |

Medallion architecture is a vendor-described refinement pattern, not a standard and not a requirement to deploy a lakehouse. In a local project:

- **bronze** can preserve exactly what arrived;
- **silver** can parse, normalise, validate, deduplicate, and quarantine;
- **gold** can produce one specific dataset for a dashboard, model, or evaluation.

Record transformation and model versions plus the input range. Do not silently replace inconvenient data and call it cleaning.

## Provenance and observability

- **[PROV overview — W3C](https://www.w3.org/TR/prov-overview/)** — optional formal vocabulary for entities, activities, agents, and derivation. Most projects need only a smaller explicit provenance record.
- **[OpenTelemetry signals](https://opentelemetry.io/docs/concepts/signals/)** — distinguishes logs, metrics, and traces when cross-service observability is needed.
- **[Monitoring Distributed Systems — Google SRE](https://sre.google/sre-book/monitoring-distributed-systems/)** — use for selecting meaningful symptoms and causes rather than collecting every possible metric.

Building observations and pipeline telemetry are different data. Monitor at least input/output counts, rejects, duplicates, lag, queue depth where applicable, storage errors, processing duration, and last successful write.
