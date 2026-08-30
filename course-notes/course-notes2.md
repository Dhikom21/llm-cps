# Edge Placement and Distributed Architecture

## Start here

1. **[Distributed Systems lecture notes — Martin Kleppmann](https://www.cl.cam.ac.uk/teaching/2122/ConcDisSys/dist-sys-notes.pdf)**
   Start with the introduction and network/RPC sections. The important ideas for this course are partial failure, uncertain message delivery, time, retries, and the difference between local function calls and network communication.

2. **[Distributed Systems lecture videos — Martin Kleppmann](https://www.youtube.com/playlist?list=PLeKd45zvjcDFUEv_ohr_HdUFe97RItdiB)**
   Use the opening lecture if video is preferable. Later lectures on clocks, replication, and consensus are optional; the project does not require implementing consensus or a distributed database.

3. **[Edge Computing: Vision and Challenges — Shi et al.](https://doi.org/10.1109/JIOT.2016.2579198)**
   Read for the motivation behind moving computation toward data sources and physical processes. Translate each claimed benefit into something measurable: latency, transferred bytes, offline operation, privacy exposure, resource use, or recovery time.

4. **[Fog Computing Conceptual Model — NIST](https://csrc.nist.gov/pubs/sp/500/325/final)**
   Use this open report to distinguish device, fog/edge, and cloud roles. “Edge” is a relative placement, not a technology or product.

For another concise systems perspective, read **[The Emergence of Edge Computing — Satyanarayanan](https://doi.org/10.1109/MC.2017.9)**.

## Choose a communication route

Read the row matching the interaction you need:

| Interaction | Reading | Questions for the design |
|---|---|---|
| Read current state or submit a command and receive a result | [HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) | What is the timeout? Is retry safe? Is the operation idempotent? What does each failure response mean? |
| Push live state to a connected browser | [WebSocket Protocol — RFC 6455](https://www.rfc-editor.org/rfc/rfc6455.html) | How does the client reconnect, detect stale state, and resynchronise? |
| Fan observations out to independent consumers | [MQTT Version 5.0 — OASIS](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html) | What are the topics, sessions, retained-message policy, delivery mode, duplicate policy, and broker-outage behaviour? |
| Retain and replay an ordered history | [Designing Data-Intensive Applications](https://dataintensive.net/) | What is retained, how is it ordered and partitioned, and how are schemas and repeated side effects handled during replay? |

MQTT delivery modes are not complete end-to-end guarantees. A consumer can still process the same observation twice or fail between recording a decision and applying its side effect. Application identifiers, validation, idempotency, and evidence are still needed.

## Choose service boundaries deliberately

- **[On the Criteria To Be Used in Decomposing Systems into Modules — Parnas](https://doi.org/10.1145/361598.361623)** — use information hiding to decide what should change independently.
- **[Microservices resource guide — Martin Fowler](https://martinfowler.com/microservices/)** — read both the benefits and operational costs of independent services.
- **[Compose application model — Docker](https://docs.docker.com/compose/intro/compose-application-model/)** — use for services, networks, volumes, configuration, and local dependencies.
- **[Docker and containers tutorial](../tutorials/docker-containers.md)** — local Go and Compose examples for the course.
- **[Kubernetes concepts](https://kubernetes.io/docs/concepts/overview/)** — optional. Read only when orchestration, restart, scheduling, or scaling is part of an explicit experiment. Docker Compose is sufficient for most laptop projects.

A network boundary is justified when a component must be independently started, stopped, restarted, isolated, deployed, or replaced. Inside one process, a Go package is normally simpler. Follow the process boundaries currently stated in Canvas; do not create extra services merely to make the diagram look distributed.

## Architecture patterns: read by project need

| Pattern | Start with | Read when | Main trade-off to evaluate |
|---|---|---|---|
| Direct feedback loop | BuildSim [API](../buildingsim/docs/) and [tutorial](../tutorials/buildsim.md) | One controller and a separate evidence collector are sufficient | Easy to trace; direct availability coupling |
| Event-driven pipeline | [Designing Data-Intensive Applications](https://dataintensive.net/) and the chosen broker specification | Several independent consumers need the same observations | Decoupled fan-out; broker, backlog, duplicates, and recovery |
| Lambda architecture | [Big Data — Marz and Warren](https://www.manning.com/books/big-data) | The project compares low-latency results with later full recomputation | Two processing paths and reconciliation logic |
| Kappa architecture | [Questioning the Lambda Architecture — Jay Kreps](https://www.oreilly.com/radar/questioning-the-lambda-architecture/) | A retained log is replayed through one transformation path | Replay, schema evolution, rebuild time, and prevention of repeated actions |
| Predictive model or digital twin | [Digital Twin in Industry — Tao et al.](https://doi.org/10.1109/TII.2018.2873186) | A model predicts future physical response and is evaluated against observations | Model fidelity, synchronisation, uncertainty, and computational cost |
