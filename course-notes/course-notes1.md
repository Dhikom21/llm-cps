# Architecture Before Implementation

## Start here

1. **[Cyber Physical Systems: Design Challenges — Edward A. Lee](https://doi.org/10.1109/ISORC.2008.25)**
   Read for the distinction between ordinary information processing and computation that interacts with time and a physical process. While reading, ask: *Which timing and feedback assumptions does our simulator need to expose?*

2. **[Model-Based Systems Engineering — SEBoK](https://sebokwiki.org/wiki/Model-Based_Systems_Engineering_%28MBSE%29)**
   Read for the purpose of MBSE across requirements, design, analysis, verification, and validation. In this course, “model-based” means that a small set of maintained models guides implementation; it does not mean that every team must use SysML.

3. **[NASA Systems Engineering Handbook](https://www.nasa.gov/wp-content/uploads/2018/09/nasa_systems_engineering_handbook_0.pdf)**
   Do not read the whole handbook. Begin with sections 4.1–4.4 on stakeholder expectations, technical requirements, logical decomposition, and design solutions. Later, use sections 5.3–5.4 to distinguish verification from validation.

4. **[The C4 model](https://c4model.com/)**
   Read the system-context and container guidance before drawing the required diagrams. A C4 *container* is an independently running application or datastore, not necessarily a Docker container.

5. **[The 4+1 View Model of Architecture — Philippe Kruchten](https://doi.org/10.1109/52.469759)** ([open PDF](https://web.mit.edu/16.35/www/lecturenotes/Kruchten4%2B1.pdf))
   Read for the logical, process, development, and physical views, with scenarios as the “+1” that illustrates and validates them. The models are complementary: C4 supplies structural zoom levels, while 4+1 separates stakeholder concerns and adds behavioural scenarios.

## Requirements, verification, and traceability

Use these links while writing the requirements table and test plan:

- **[MBSE Initiative — INCOSE](https://www.incose.org/group/mbse-initiative/)** — the established definition and scope of MBSE: modelling supports requirements, design, analysis, verification, and validation throughout the life cycle.
- **[NASA Systems Engineering Handbook appendices](https://www.nasa.gov/reference/system-engineering-handbook-appendix/)** — consult *How to Write a Good Requirement* and the *Requirements Verification Matrix*. A requirement needs an observable response and acceptance criterion, not words such as “fast,” “realistic,” or “robust” without a measure.
- **[ISO/IEC/IEEE 42010](https://www.iso.org/standard/74393.html)** — use the public overview for the vocabulary of stakeholders, concerns, viewpoints, views, and architecture descriptions. The full paid standard is not required.
- **[A Survey of MBSE Methodologies](https://sebokwiki.org/wiki/A_Survey_of_Model-Based_Systems_Engineering_%28MBSE%29_Methodologies)** — optional background if the report discusses MBSE methods in more depth.

## Architecture and boundaries

- **[C4 diagrams](https://c4model.com/diagrams)** — use a context view to define what belongs to the student system and what is external; use a container view to allocate responsibilities, persistent data, protocols, and process boundaries.
- **[On the Criteria To Be Used in Decomposing Systems into Modules — David Parnas](https://doi.org/10.1145/361598.361623)** — read for the idea that a boundary should hide a design decision likely to change. Apply the same reasoning to Go packages and independently deployable services.
- **[OMG SysML](https://www.omg.org/sysml/)** — optional formal modelling language. SysML is not required; lightweight C4, requirements tables, interface contracts, and behavioural diagrams are sufficient.
- **[Diagram-as-code tutorial](../tutorials/diagrams-as-code.md)** — local practical instructions and examples using D2.

Structural diagrams do not show ordering, timeouts, retry rules, or state history. Add at least one sequence, workflow, or state-machine view for an important normal or failure scenario.

## Interface reading

Consult only the material that matches the interface you actually design:

- **[HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html)** — methods, status codes, idempotency, caching, and request–response semantics.
- **[OpenAPI Specification](https://spec.openapis.org/oas/latest.html)** — a machine-readable description of HTTP operations and payloads.
- **[Learn JSON Schema](https://json-schema.org/learn)** — types, required properties, ranges, and schema validation for observations and commands.
- **[BuildSim API documentation](../buildingsim/docs/)** — the versioned source for the actual BuildSim endpoints and payloads.

An interface contract should state identity, fields and types, units, timestamps, quality flags, allowed operations, timeout behaviour, error responses, delivery assumptions, and versioning. An arrow labelled “REST” does not provide this information.
