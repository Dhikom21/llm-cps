# Decision Architectures and Optional Agentic AI

## Start here for every project

1. **[Feedback Systems — Åström and Murray](https://fbsbook.org/)**
   This free textbook is the main reading for feedback, physical dynamics, stability, robustness, and control. Begin with Chapters 1–3. Even a rule-based team should understand why closed-loop behaviour must be evaluated as a system rather than as an isolated function.

2. **[Artificial Intelligence: A Modern Approach — Russell and Norvig](https://www.pearson.com/en-us/subject-catalog/p/artificial-intelligence-a-modern-approach/P200000003500/9780134610993)**
   Use the chapters on intelligent agents, search, planning, uncertainty, and learning for the classical vocabulary. An agent observes and acts within an environment; it is not defined by whether it uses an LLM.

## Select one main decision route

The project needs one defensible route, not every technique in this table.

| Route | Reading | Suitable project question | What must be evaluated |
|---|---|---|---|
| Rule or state machine | [Statecharts — Harel](https://doi.org/10.1016/0167-6423%2887%2990035-9) and the course [diagram tutorial](../tutorials/diagrams-as-code.md) | Which explicit state and transition logic closes the loop? | Boundaries, hysteresis, stale input, conflicting rules, and degraded states |
| Feedback control | [Feedback Systems](https://fbsbook.org/) | How should an actuator respond to error over time? | Delay, gain, saturation, disturbance, stability, and recovery |
| Optimisation | [Convex Optimization — Boyd and Vandenberghe](https://web.stanford.edu/~boyd/cvxbook/) | Which action minimises an objective while satisfying constraints? | Model, horizon, weights, feasibility, solver time, and constraint violations |
| Forecast or classifier | [Metrics and scoring — scikit-learn](https://scikit-learn.org/stable/modules/model_evaluation.html) | Can recent observations predict a useful future value or class? | Baseline, split strategy, relevant metric, calibration, shift, and decision impact |
| Anomaly detection | [Novelty and outlier detection — scikit-learn](https://scikit-learn.org/stable/modules/outlier_detection.html) | Does current behaviour differ from defined normal data? | False alarms, missed events, detection delay, contamination assumptions, and drift |
| Reinforcement learning | [Reinforcement Learning: An Introduction — Sutton and Barto](http://incompleteideas.net/book/the-book-2nd.html) | Can a policy be learned from interaction with the simulator? | State, action, reward, constraints, training conditions, seeds, and unsafe exploration |
| Optional LLM workflow | [ReAct — Yao et al.](https://arxiv.org/abs/2210.03629) | Does the task genuinely require text interpretation or bounded tool selection? | Variability, invalid output, injection, latency, model outage, authority, and fallback |

A single threshold is a useful baseline but usually not a complete component. Add freshness and quality checks, persistence or hysteresis where needed, command validation, a degraded state, and verification of the later physical effect.

## Safety boundaries and runtime assurance: reading list

The literature uses more precise terms than *guardrails*: **safety constraints**, **runtime monitors**, **safety filters**, **Simplex safety controllers**, and **shields**. Read the first two items; select the remaining material when it matches the project architecture.

1. **[STPA Handbook — Leveson and Thomas](https://psas.scripts.mit.edu/home/books-and-handbooks/)**
   Start with the control structure, unsafe control actions, and safety constraints. This is the main systems-engineering reading for deciding which commands must be provided, prohibited, timed, or stopped under particular conditions.

2. **[Run Time Assurance for Safety-Critical Systems — Hobbs et al.](https://arxiv.org/abs/2110.03506)**
   Read for the runtime-assurance pattern: a monitor or safety filter checks a complex controller and invokes a simpler fallback when required. The primary controller may be rule-based, optimisation-based, learned, or human-operated.

3. **[An Architectural Description of the Simplex Architecture — Rivera et al.](https://www.sei.cmu.edu/library/an-architectural-description-of-the-simplex-architecture/)**
   Read when separating an advanced controller from a safety controller and decision module. This is the architecture source behind many runtime-assurance patterns.

4. **[A Brief Account of Runtime Verification — Leucker and Schallhart](https://doi.org/10.1016/j.jlap.2008.08.004)**
   Optional background on checking an execution against a specification while the system is running, and on the relationship between runtime verification, testing, and model checking.

5. **[Safe Reinforcement Learning via Shielding — Alshiekh et al.](https://doi.org/10.1609/AAAI.V32I1.11797)**
   Read only for a learning-based controller. The paper places a formally derived shield between an RL policy and its environment so that unsafe proposed actions can be blocked or replaced.

6. **[Language Models Don't Always Say What They Think — Turpin et al.](https://proceedings.neurips.cc/paper_files/paper/2023/hash/ed3fea9033a80fea1376299fa7863f4a-Abstract.html)**
   Read when using an LLM to propose decisions or explanations. It motivates retaining structured observations, proposals, validation results, commands, and outcomes instead of treating generated explanations as faithful decision records.

## Evaluation reading

- **[Metrics and scoring — scikit-learn](https://scikit-learn.org/stable/modules/model_evaluation.html)** — choose a metric from the prediction and decision goal, not from a library default.
- **[AI Risk Management Framework 1.0 — NIST](https://doi.org/10.6028/NIST.AI.100-1)** — use Map and Measure to connect context, limitations, and evidence.
- **[Getting started with fuzzing — Go](https://go.dev/doc/tutorial/fuzz)** — useful for parsers, input validators, and command contracts.
- **[Monitoring Distributed Systems — Google SRE](https://sre.google/sre-book/monitoring-distributed-systems/)** — background for latency, failures, and operational signals.

Evaluate a more complex technique against a deterministic baseline using the same simulator versions, scenarios, seeds, and disturbances. Depending on the use case, measure decision latency, time within the configured range, actuator work, oscillation, precision and recall, forecasting error at the useful horizon, false-alarm rate, recovery time, and resource use.

## Optional LLM and agent route

Skip this section unless the project independently chooses an LLM extension.

- **[ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)** — one pattern for alternating model proposals with tool observations. It is not a safety or correctness mechanism.
- **[Model Context Protocol specification](https://modelcontextprotocol.io/specification)** — an optional protocol for connecting a host to tools and resources. BuildSim exposes REST and WebSocket APIs, not MCP; an adapter would be student-owned.
- **[Generative AI Profile — NIST](https://doi.org/10.6028/NIST.AI.600-1)** — risks and actions concerning confabulation, validity, privacy, security, monitoring, and human oversight.
- **[OWASP GenAI LLM Top 10 2026](https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/)** — current application-security reading on injection, unsafe output, excessive authority, sensitive information, and supply-chain risks.
- **[Building Effective Agents — Anthropic](https://www.anthropic.com/engineering/building-effective-agents)** — accessible practitioner guidance on workflows and agents. Treat it as vendor guidance and compare it with the research and standards above.

An optional LLM route needs an allowlisted tool set, validated arguments, step and time limits, idempotency for side effects, untrusted-input handling, and deterministic fallback. The required course loop should still run without network or model access.

## Human responsibility for AI-generated code

- **[Secure Software Development Framework — NIST SP 800-218](https://csrc.nist.gov/pubs/sp/800/218/final)** — a technology-neutral structure for requirements, protected source, dependencies, verification, and vulnerability response.
- **[Asleep at the Keyboard? Assessing the Security of GitHub Copilot's Code Contributions](https://doi.org/10.1109/SP46214.2022.9833571)** — an empirical study showing why generated code requires security review and testing. Do not generalise its measured rates beyond the systems and tasks studied.
- **[ACM Code of Ethics and Professional Conduct](https://www.acm.org/code-of-ethics)** — professional responsibility, competence, quality, risk evaluation, and honest claims.
- **[Go testing package](https://pkg.go.dev/testing)** and **[Go fuzzing tutorial](https://go.dev/doc/tutorial/fuzz)** — primary implementation references for tests, benchmarks, and generated input.

Use AI coding tools to implement bounded tasks only after defining the requirement, interface, and acceptance conditions. Review the complete diff and new dependencies, run tests derived from the specification, inspect the integrated system, and remove secrets or unrelated material. Generated code and generated tests can share the same mistaken assumption; passing tests do not transfer responsibility to the tool.
