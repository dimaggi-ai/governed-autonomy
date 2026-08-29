# References

Every timescale in the [latency hierarchy](docs/latency-hierarchy.md) and every standards claim in [autonomous-networks](docs/autonomous-networks.md) traces to a source below, verified against primary sources in August 2026. Vendor latency figures and standards levels drift across releases; re-check against the version you deploy.

## The latency hierarchy

- **[1]** White Rabbit / PTP High-Accuracy (CERN): sub-nanosecond synchronization, <10 ps over a 5 km link; incorporated into IEEE 1588-2019. https://white-rabbit.web.cern.ch/
- **[2]** NVIDIA InfiniBand switch port-to-port latency — QM8790 product brief (sub-90 ns), Quantum-X800 (<100 ns). Note: the QM9700 datasheet does not publish a latency figure; ~100 ns is the class figure for adjacent generations. https://network.nvidia.com/files/doc-2020/pb-qm8790.pdf
- **[3]** NVIDIA InfiniBand Adaptive Routing whitepaper (WP-10326-001): the Subnet Manager configures AR groups; the switch ASIC selects the least-loaded egress per packet. Spectrum-X per-packet load balancing. In-network computing / P4: line-rate match-action ("a few nanoseconds per packet"), measured in-network inference <450 ns/packet (Flowrest; ETH Zürich advanced-networking notes). https://developer.nvidia.com/blog/
- **[4]** RS-FEC "KP4" (RS(544,514)) inline in the PCS: ~100–200 ns added latency ("100+ Gb/s Ethernet FEC Analysis," Signal Integrity Journal; IEEE 802.3 study-group materials).
- **[5]** IEEE 802.1Qbb Priority Flow Control: pause quanta = 512 bit-times; the reaction loop (generate + wire + far-end action) is ~1–10 µs intra-DC, formalized in N. Finn, "Determining PFC Headroom," IEEE 802.1 contribution 2021.
- **[6]** BFD — RFC 5880/5881; recommended common interval set includes 3.3 ms (draft-ietf-bfd-intervals), giving a ~10 ms detection floor at ×3; typical carrier settings 100–300 ms ×3.
- **[7]** Polese et al., "Understanding O-RAN," arXiv:2202.01032 (IEEE Comms Surveys & Tutorials): three control loops — non-RT rApps (>1 s), near-RT RIC xApps (10 ms–1 s), real-time (<10 ms, in the O-DU/O-RU, not in a RIC; specs "lack a practical approach" below 10 ms).
- **[8]** Polese et al., "dApps: Distributed Applications for Real-time Inference and Control in O-RAN" — the proposed sub-10 ms tier running on the DU/CU (compiling control downward).
- **[9]** OCS reconfiguration: Poutievski et al., "Jupiter Evolving" (SIGCOMM 2022) and "Mission Apollo" (arXiv:2208.10041) — 3D-MEMS OCS, millisecond-scale switching, production loop = drain → reconfigure → BER-test → release; Jouppi et al., "TPU v4" (ISCA 2023, arXiv:2304.01433) — OCS <5% of system cost/power, scheduled topology shifts.
- **[10]** 50 ms protection lineage: ITU-T G.841 / RFC 5654 (MPLS-TP, restore within 50 ms up to 1,200 km); TI-LFA, RFC 9855 ("Topology Independent Fast Reroute Using Segment Routing") — sub-50 ms via pre-computed backup paths, not real-time computation.
- **[11]** IGP/BGP convergence: Francois et al., "Achieving sub-second IGP convergence in large IP networks," ACM SIGCOMM CCR (tuned IGP hundreds of ms); BGP node-failure convergence seconds–minutes at default MRAI.
- **[12]** gNMI: OpenConfig gNMI specification (sample_interval allows ns granularity) vs practice — vendor minimums 5–10 s, common deployments 30 s (gNMIc / OcNOS / Palo Alto telemetry docs). https://openconfig.net/docs/gnmi/gnmi-specification/
- **[13]** LLM-planner deliberation at seconds–minutes is consistent across the 2024–26 network-agent systems in [17]; placed as the cognition tier.
- **[14]** TMF921 Intent Management API (v5): intent expression, feasibility, negotiation, compliance reporting — OSS-timescale (minutes) assurance loops. TM Forum Intent Ontology (IG1253).

## Autonomous-network standards and field patterns

- **[15]** Ericsson, "Autonomous networks with multi-layer, intent-based operation" (Ericsson Technology Review, Aug 2023, Niemöller et al.): the "autonomous domain" with an intent manager controlling the domain assurance loop; utility-based conflict handling inside coordinating intent managers.
- **[16]** TM Forum Autonomous Networks: IG1218 (Business Requirements & Framework) and IG1252 (AN Levels Evaluation Methodology) — L0–L5 across Intent/Awareness/Analysis/Decision/Execution (People vs System). At L4, Intent is P/S-shared; industry survey places most operators at L1–L2 with L3 the near-term target; first single-scenario L4 certification 2025 (TDC NET + Ericsson, ANLAV).
- **[17]** LLM/agent systems in networks (2024–26): multi-agent optical OAM (IEEE ComMag 2025, arXiv:2510.05625; production million-link demo arXiv:2608.23145); ORION — MCP-based SMO + rApp + xApp closed loop (arXiv:2603.03667); AgentRAN (rApps/xApps/dApps decomposition); MCP-on-OLS demonstrated for IPoDWDM (arXiv:2607.05975).

## Series

- **[18]** DIMAGGI AI, Chaos Fidelity Standard (ai-cluster-chaos-fidelity) and Reliability Economics — the experiments this controller must pass, and their price. https://github.com/dimaggi-ai/ai-cluster-chaos-fidelity
