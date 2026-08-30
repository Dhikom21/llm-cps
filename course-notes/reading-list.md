# Guided Reading Index

The course notes are themselves curated reading lists. Each chapter contains a short core route and optional routes for particular design choices. Students are not expected to read every link.

| Chapter | Begin with | Follow a specialist route when needed | Project output |
|---|---|---|---|
| [1. Architecture Before Implementation](course-notes1.md) | CPS, MBSE, NASA systems engineering, C4, and the 4+1 view model | Requirements, interfaces, modularity, ISO 42010, or optional SysML | Requirements, architecture views, behavioural model, interface contract, and traceability |
| [2. Edge Placement and Distributed Architecture](course-notes2.md) | Distributed-system failure, edge placement, and the NIST fog model | HTTP, WebSocket, MQTT, containers, Kubernetes, Lambda, Kappa, or digital twins | Placement and communication decisions with alternatives and failure behaviour |
| [3. Data Engineering for CPS](course-notes3.md) | Data-intensive systems, streaming time, and the Dataflow model | JSON Schema, MQTT, Parquet, DuckDB, Medallion, provenance, or observability | Versioned data contract, live/evidence paths, storage policy, replay, and data-quality tests |
| [4. Decision Architectures and Optional Agentic AI](course-notes4.md) | Feedback systems and classical agent concepts | State machines, control, optimisation, ML, anomaly detection, RL, or optional LLM workflows | Bounded autonomous loop, baseline, authority checks, decision evidence, and evaluation |

## Suggested order

1. Before the proposal, read Chapter 1 and the *Start here* section of Chapter 2.
2. Select a use case and write requirements before selecting infrastructure or an AI technique.
3. During architecture work, follow only the Chapter 2 communication and deployment routes used by the proposed system.
4. Before implementing the pipeline and autonomous service, use Chapters 3 and 4 to define contracts, baselines, evidence, and failure behaviour.
5. During evaluation, return to the relevant failure, testing, and evaluation readings. Use measurements to revise the final architecture so it describes the system that was actually built.

## Source policy

The lists favour primary research, standards, official documentation, open textbooks, and recognised systems-engineering material. Vendor or practitioner sources are labelled by context and should not be treated as universal standards. A linked source supports further study; its presence does not make a technology a course requirement.

For volatile software and agent protocols, follow the current official documentation rather than copying version-specific details into these notes. For BuildSim behaviour, use the versioned local documentation.

Canvas remains authoritative for deadlines, assessment, group arrangements, and current deliverables.

## Local practical material

- [BuildSim tutorial](../tutorials/buildsim.md)
- [Docker and containers](../tutorials/docker-containers.md)
- [Diagram as code](../tutorials/diagrams-as-code.md)
- [Worked final report](../lab-assignment/final_report_example/)

When an external source and a local example differ, use the external source for the general concept and the local versioned documentation for the actual BuildSim interface.
