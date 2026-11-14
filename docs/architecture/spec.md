# Samanvaya 2 (S2): product specification

> Converted from `Samanvaya_2_Enterprise_Ontology_Platform_Spec.docx`. This is the product specification as written. For what is implemented, see [overview.md](overview.md). Two components named here were replaced for the reasons given in the decision records: MinIO ([ADR 0002](adr/0002-object-store.md)) and EMQX ([ADR 0003](adr/0003-mqtt-broker.md)).

*Technical Architecture & Product Specification — Civilian Enterprise Data Fabric, Operational Ontology, and Autonomous Fleet Digital Twin*

**Repository:** https://github.com/veerendra-saraswathi/S2\
**Domain Scope:** 100% Civilian Commercial Enterprise Operations (Energy & Utilities, Smart Manufacturing, Logistics & Supply Chain, Precision Agriculture, Civil Infrastructure Inspection)\
**Platform Tier:** Samanvaya 2 — Enterprise Data Operating System & Digital Twin Layer (built atop Samanvaya 0.5 / 1 fleet telemetry)

# 1. Executive Vision & Commercial Positioning

Samanvaya 2 (S2) is an open, cloud-native Enterprise Data Operating System and Operational Digital Twin platform designed for civilian industrial enterprises. While Samanvaya 0.5 and Samanvaya 1 orchestrate real-time field telemetry and edge autonomy across commercial UAVs, UGVs, and IoT sensors, Samanvaya 2 transforms raw operational streams and fragmented enterprise IT/OT records into a unified, semantically typed **Enterprise Operational Ontology**.

Comparable in architectural philosophy to modern enterprise data platforms (such as Palantir Foundry), S2 bridges the gap between physical field robotics, enterprise ERP/CMMS/SCADA systems, and human/AI decision-making—enabling industrial operators to model physical assets as living software objects, simulate operational scenarios, execute closed-loop automated workflows, and audit every action with full data lineage.

# 2. Core Architectural Pillars (7-Layer Enterprise Stack)

## 2.1 Multi-Source Data Ingestion & Connector Fabric (IT + OT + Field Robotics)

-   **Batch & Streaming Connectors:** Native ingestion from Samanvaya 0.5/1 telemetry buses (MQTT/Kafka/ROS 2 bridges), industrial SCADA/OPC-UA historians, ERP/EAM systems (SAP, Maximo, PostgreSQL, Snowflake), GIS/BIM spatial repositories, and drone inspection imagery/LiDAR object stores.

-   **Zero-Copy & Incremental Sync:** Change Data Capture (CDC), schema evolution tracking, and automated ingestion pipelines with data quality contracts and quarantine rules.

## 2.2 Open-Source Data Lakehouse & Lineage Engine

-   **Storage & Compute:** Open table formats (Apache Iceberg / Delta Lake on MinIO / S3) paired with distributed transformation engines (Apache Spark / DuckDB / dbt / Dagster) for petabyte-scale historical telemetry and inspection media.

-   **End-to-End Provenance & Data Lineage:** DAG-based dataset versioning, column-level lineage tracking, reproducible pipeline builds, and branch-based sandbox environments (\"Data Branching\") allowing engineers to test schema or model changes safely before merging to production.

## 2.3 Semantic Enterprise Operational Ontology (The Digital Twin Core)

-   **Object Types & Properties:** Strongly typed domain entities (e.g., CommercialDrone, GroundRobot, WindTurbine, Substation, WarehouseZone, InspectionMission, WorkOrder, MaintenanceCrew) mapped dynamically to underlying datasets and live telemetry streams.

-   **Links & Graph Relationships:** Typed directional relationships (e.g., Drone -\> inspects -\> Substation; SensorReading -\> triggers -\> MaintenanceTicket; BatteryPack -\> installedOn -\> Drone) enabling multi-hop graph traversal and spatial queries.

-   **Action Types & Write-Back Engine:** Governed, transactional state mutations (\"Actions\") that validate business rules, update Ontology state, and write back commands to enterprise systems (e.g., dispatching a drone inspection mission, creating an SAP/Maximo repair work order, or grounding a UAV with degraded actuator health).

## 2.4 Time-Series, Geospatial & Media Fusion Engine

-   **High-Frequency Telemetry Store:** TimescaleDB / QuestDB integration linked directly to Ontology object properties for sub-second playback of historical and live fleet metrics.

-   **Spatial & 3D Digital Twin Indexing:** PostGIS, GeoTIFF/3D Tiles, and H3 hexagonal indexing supporting corridor inspection (powerlines, pipelines, railways), geofencing, and 3D facility models.

-   **Unstructured Inspection Media Indexing:** Automated linkage of high-resolution RGB, thermal/LWIR, and LiDAR point clouds to specific asset components and defect bounding boxes.

## 2.5 AI, Predictive Maintenance & Simulation Workbench

-   **Feature Store & Model Registry:** MLflow / Feast integration binding ML models (e.g., actuator fault detection, thermal anomaly classifiers, battery remaining-useful-life predictors) directly to Ontology Object Types.

-   **What-If Scenario & Digital Twin Simulation:** Sandboxed simulation engine allowing planners to test fleet dispatch schedules, weather disruptions, predictive maintenance windows, and supply-chain bottlenecks against a live clone of the Enterprise Ontology.

-   **Governed Enterprise AI Agents (AIP-Equivalent):** Tool-calling LLM/agent workflows grounded strictly in the Ontology---enforcing RBAC permissions so AI assistants can query fleet health, draft inspection summaries, and propose governed Actions with human-in-the-loop approval.

## 2.6 Low-Code Operational Application Builder (\"Workshop\")

-   **Composable UI Canvas:** Drag-and-drop operational workspace builder allowing domain teams (grid operators, warehouse managers, agronomy leads) to assemble custom dashboards, 2D/3D map views, Gantt dispatch boards, and work-order triage queues directly over Ontology Objects and Actions without rebuilding backend APIs.

## 2.7 Enterprise Security, RBAC/ABAC & Compliance Governance

-   **Granular Access Control:** Role-Based and Attribute-Based Access Control (RBAC/ABAC) enforced down to Object Type, property, and row/geographical-region levels.

-   **Immutable Audit Trail:** Cryptographically verifiable audit logs of all data transformations, user queries, AI agent tool invocations, and executed Ontology Actions for ISO, OSHA, and civil aviation (DGCA/FAA Part 107) compliance.

# 3. Cloud-Native Open-Source Reference Stack

To avoid proprietary vendor lock-in and support both cloud and on-premise enterprise deployments on Kubernetes, Samanvaya 2 standardizes on CNCF and open-source data/AI tooling:

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Architectural Layer**      **Primary Open-Source Stack**                                              **Key Responsibilities**
  ---------------------------- -------------------------------------------------------------------------- ---------------------------------------------------------------------------------
  **Ingestion & Streaming**    Apache Kafka, MQTT (EMQX), Airbyte, Debezium                               Real-time UAV/UGV telemetry ingestion, ERP/SCADA CDC, batch connectors

  **Lakehouse & Pipelines**    Apache Iceberg, MinIO, Dagster, dbt, DuckDB / Spark                        Versioned datasets, data branching, lineage DAGs, quality checks

  **Operational Ontology**     Python (FastAPI), GraphQL/REST, PostgreSQL + PostGIS, Neo4j / Apache AGE   Object/Link/Action definitions, transactional write-back, graph traversal

  **Time-Series & Spatial**    TimescaleDB, GeoServer, CesiumJS / Deck.gl, H3                             Sub-second sensor histories, 3D terrain/facility digital twins, geofencing

  **AI & Simulation Engine**   PyTorch, MLflow, Ray, vLLM / Ollama, LangGraph                             Predictive maintenance models, what-if twin simulations, RBAC-governed agents

  **Operational UI Builder**   React, TypeScript, TailwindCSS, React Flow                                 Low-code Workshop dashboards, visual pipeline builder, ontology explorer

  **Infra & Governance**       Kubernetes, Cilium, Keycloak, Open Policy Agent (OPA), OpenTelemetry       Multi-tenant isolation, fine-grained ABAC/RBAC, immutable compliance audit logs
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# 4. Target Civilian Commercial Verticals & Workflows

-   **Energy & Utility Grid Inspection:** Integrate drone thermal/LiDAR corridor scans with substation SCADA telemetry and SAP PM work orders; automatically detect insulator hotspots and dispatch repair crews via Ontology Actions.

-   **Smart Manufacturing & Warehouse Logistics:** Unify indoor autonomous mobile robots (AMRs/UGVs), inventory drones, WMS inventory tables, and conveyor PLC logs into a live facility digital twin to eliminate stockouts and optimize routing.

-   **Civil Infrastructure & Construction Monitoring:** Track bridges, highways, solar farms, and wind turbines over time using photogrammetry digital twins, structural defect progression models, and automated regulatory inspection reports.

-   **Precision Agriculture & Forestry:** Fuse multispectral UAV surveys, soil IoT sensors, irrigation controllers, and weather forecasts to trigger targeted variable-rate spraying and harvest scheduling.

# 5. Phased Implementation Roadmap (Repository: veerendra-saraswathi/S2)

-   **Phase 1 (Months 1--3) — Foundation & Ontology Kernel:** Monorepo scaffolding, Kubernetes/Docker Compose local dev stack, Iceberg/PostgreSQL/TimescaleDB storage core, Ontology schema registry (Object Types, Links, Properties), and Samanvaya 0.5 telemetry connector.

-   **Phase 2 (Months 4--6) — Data Lineage, Actions & Write-Back Engine:** Dagster/dbt pipeline orchestrator with dataset branching, transactional Action execution engine with OPA policy checks, and ERP/CMMS bi-directional connectors.

-   **Phase 3 (Months 7--9) — Low-Code Workshop UI & Spatial Digital Twin:** Web-based Ontology Explorer, visual pipeline graph editor, composable dashboard builder with CesiumJS/MapLibre 3D asset views, and time-series replay.

-   **Phase 4 (Months 10--12) — Predictive AI, What-If Simulation & Governed Agents:** MLflow predictive maintenance integration, digital twin scenario simulator, RBAC-scoped enterprise AI copilot, and full SOC2/ISO audit trail hardening.
