# TravelSync / Ourney application progress checklist

Status date: **8 November 2026 (Asia/Seoul)**.

The application has an implemented path from accounts and group preferences to group recommendations, itinerary optimization, independent validation, saved plans and voting. Real-world itinerary generation remains blocked by the missing verified operating-data and routing provider. The AI agent and live group chat are still unfinished.

This checklist covers components identifiable in the current repository and its planning/integration documents. The original SRS/SDD files are not present in this checkout, so this is not an exhaustive requirement-ID traceability audit. Current code governs implementation status; older design references identify intended work only.

**Status key:** [x] implemented in the stated scope; [ ] partial, missing or awaiting verification. A checked implementation item does not mean production deployment or user acceptance has passed. “Recorded verification” below refers to existing reports, not tests rerun for this checklist.

## 1. Accounts and access

| Check | Component | Current progress / remaining work |
|---|---|---|
| [x] | Registration and login | Signup, verified-email login, password hashing and token authentication are implemented. |
| [x] | Email verification and recovery | Verification/resend codes, forgotten-password and password-reset flows exist. |
| [x] | Protected application access | Authenticated web routes and API trip-membership checks are implemented. |
| [x] | Session invalidation | Logout-all endpoint is implemented. |
| [ ] | Production email delivery | SMTP support exists; without configuration, messages go to development logs. Configure and verify real delivery. |

Evidence: `api/app/routes/auth.py`, `api/app/security.py`, `api/app/emailer.py`, `api/app/deps.py`, `web/src/auth.tsx`, login/signup/recovery pages.

## 2. Groups, preferences and trip setup

| Check | Component | Current progress / remaining work |
|---|---|---|
| [x] | Trip creation and membership | Create/list/view trips, invite links, invite preview and joining are implemented. Limits support up to 10 members and 1–3 days. |
| [x] | Group readiness | Trip room shows members, preference completion and outstanding responses; reminder endpoint exists. Real reminder delivery depends on SMTP. |
| [x] | Personal taste onboarding | Taste catalogue, saved user tastes and default exclusions are implemented. |
| [x] | Versioned trip preferences | Ten-feature 1–5 preferences, preference text and personal exclusions feed captured recommendation inputs. |
| [x] | Date availability | Members submit confirmed dates; generation requires confirmation for every requested trip date. |
| [ ] | Individual hourly availability | Current app captures dates only. The optimizer supports windows, but personal hourly entry is not integrated. |
| [x] | Explicit planning constraints | Planning form supports daily windows, start/end locations, budget, mode and additional hard constraints. |

Evidence: `api/app/routes/{trips,tastes,preferences}.py`, `api/app/optimization.py`, `web/src/pages/{TripRoom,Preferences,Tastes}.tsx`, `web/src/components/OptimizationForm.tsx`.

## 3. POI data and persistence

| Check | Component | Current progress / remaining work |
|---|---|---|
| [x] | Collection and processing tools | TourAPI/Kakao collection, cleaning, deduplication, feature mapping and fixed-input replay tooling exist. |
| [x] | Packaged enriched POI catalogue | Current documented v3 catalogue contains 3,570 POIs and 72 columns, including metadata/provenance. Metadata coverage remains incomplete. |
| [x] | Versioned import and activation | CSV validation, dataset versions, explicit activation and metadata imports are implemented. |
| [x] | Reproducible recommendation records | Dataset/schema/preference versions and individual/group outputs are captured for runs. |
| [x] | Optimization audit persistence | Separate success/failure audits capture planning and optimizer snapshots; private matrices are excluded from public results. |
| [ ] | Verified scheduling facts | Raw metadata and hours import are available, but verified dated openings, closures, holidays, events, fees, dwell and accessibility still require provider integration. Unknown facts must remain UNKNOWN. |
| [ ] | Production database rollout | PostgreSQL schema/Compose support exists. Verify the current app and new audit table on disposable PostgreSQL before deployment. The CLI/package and API database paths need separate verification. |

Evidence: `src/ourney_travelsync/ingestion/`, `src/ourney_travelsync/db/`, `database/`, `api/app/{import_places,import_metadata,import_hours,models}.py`, [metadata guide](poi_metadata.md), [data workflows](data_workflows.md).

## 4. Recommendation engine

| Check | Component | Current progress / remaining work |
|---|---|---|
| [x] | Individual relevance scoring | E5 hybrid is the configured default, combining feature and semantic percentiles. Explicit feature fallback exists when the model/cache is unavailable. |
| [ ] | Operational E5 model/cache | Cache tooling exists; verify the real checkpoint and effective scoring method in the deployment environment. Controlled synthetic embeddings do not verify checkpoint operation. |
| [x] | Group aggregation | GFAR is integrated; AWM/feature and legacy scoring paths remain available for comparison. |
| [x] | Candidates, ties and exclusions | Per-member candidate union, seeded identity-based tie handling and exclusions are implemented. |
| [x] | API and results UI | Authenticated generation, latest stored results and enriched place details are connected to the real recommender service. |
| [x] | Score semantics | Relative hybrid relevance, GFAR gain and optimization utility remain separate; hybrid relevance is not presented as satisfaction or group-fit percentages. |
| [ ] | Human relevance and satisfaction validation | Current integration checks do not establish recommendation quality, fairness improvements or satisfaction. Independent human evidence is still needed. |
| [ ] | AGREE extension | Explicitly unimplemented in the active package; future research rather than a completed application dependency. |

Evidence: `src/ourney_travelsync/recommenders/`, `services/recommender/main.py`, `api/app/routes/recommendations.py`, `web/src/pages/Places.tsx`, [E5 guide](e5_hybrid.md), [AGREE scope](agree_design.md).

## 5. Availability, routing and itinerary optimization

| Check | Component | Current progress / remaining work |
|---|---|---|
| [x] | E2 evidence-based eligibility | Excludes candidates lacking required scheduling evidence, with explicit reasons; supports exclusion/access/event checks. |
| [ ] | Real planning-data adapter | Strict `/snapshot` request/response boundary exists. A real operating/enrichment provider is not included. Without configuration, generation returns `planning_data_not_configured`. |
| [ ] | Directed real travel routes | Integrate evidence-backed durations, fares and walking components for the selected mode, including start and return legs. Place-search metadata is insufficient. |
| [ ] | Freshness and route refresh | Implement provider freshness/cache policy and departure-time handling. Current solver consumes pinned daily route matrices. |
| [x] | CP-SAT itinerary solver | Implements 1–3 days, optional visits, no repeated POIs, opening windows, waiting, return deadlines, group budget and hard constraints. |
| [x] | Objective stages and solver states | Group utility, minimum-member relevance and travel objectives; records status, bounds and timing. FEASIBLE does not imply optimality. |
| [x] | Independent E3 validation | Reconstructs the itinerary from source snapshots; API rechecks results before publishing. A service-supplied PASSED claim alone is insufficient. |
| [x] | Authenticated app integration | Generation calls the real optimizer, pins inputs and rejects stale state or invalid results. Failures preserve the previous plan and votes. |
| [ ] | Automatic candidate expansion | Solver coordinator supports a supplied expanded snapshot. Production orchestration for fetching/enriching an expanded pool is unfinished; app currently submits one pool. |
| [ ] | Real-provider end-to-end acceptance | Recorded HTTP verification uses a synthetic planning adapter. Validate actual Busan operating facts/routes through publication before declaring real-world generation complete. |

Evidence: `src/ourney_travelsync/optimizer/`, `services/optimizer/main.py`, `api/app/optimization.py`, `api/app/routes/itineraries.py`, [current app integration](optimizer_app_integration.md), [optimizer formulation](optimizer.md).

## 6. Itinerary presentation and group decisions

| Check | Component | Current progress / remaining work |
|---|---|---|
| [x] | Saved itinerary display | Dated visits, travel/return times, group costs, solver/validation status and source-version information are exposed. |
| [x] | Place information | Place details, metadata, photos where available and external map links are implemented. |
| [x] | Voting and selected plan | Vote storage, member checks and selected-plan calculation exist. Only audited published plans are eligible. |
| [ ] | Multiple alternative itineraries | Current optimizer publishes one itinerary. Legacy themed alternatives are not a completed optimized-alternatives feature; confirm whether retained product scope requires them. |
| [ ] | Final usability acceptance | Run real browser/mobile review of setup, errors, missing data, itinerary explanations and the single-plan voting experience. TypeScript/build success does not establish usability. |

Evidence: `api/app/routes/{itineraries,pois}.py`, `web/src/pages/Plan.tsx`, `web/src/components/{PlaceSheet,PlaceAbout,OptimizationForm}.tsx`.

## 7. Agent and collaborative extensions

| Check | Component | Current progress / remaining work |
|---|---|---|
| [ ] | Live group chat | Identified as a next milestone; no integrated chat/WebSocket route or screen found in the current application. |
| [ ] | LLM agent | Agent contracts and service-call helper exist; Compose points to a keyword-based mock. Real model/tool execution is unfinished. |
| [ ] | Agent proposals and replanning | Contract-level scaffolding exists. Implement authenticated proposals, user acceptance and validated optimization before publishing changes. |
| [ ] | Feedback collection | Listed as a next milestone; no integrated application feedback workflow found. |
| [ ] | POI submissions and admin review | Callable package/database submission and review functions exist; public submission/admin API and UI are not integrated. Treat as an extension. |

Evidence: `contracts/agent.*.json`, `api/app/services.py`, `api/app/main.py`, `services/mocks/main.py`, `src/ourney_travelsync/db/submissions.py`.

## 8. Verification and delivery

| Check | Component | Current progress / remaining work |
|---|---|---|
| [x] | Automated test coverage | Root, API and recommender suites cover contracts, auth, scoring, optimizer/E3, stale state and failure preservation. CI workflows exist. |
| [x] | Controlled HTTP integration | Recorded four-process test covers signup → recommendations → optimization → publication → vote, plus failure preservation, using synthetic planning data. |
| [x] | Web compilation/build | TypeScript and production Vite build passed in the latest recorded review. |
| [ ] | Docker deployment | Compose definitions and Dockerfiles exist; current deployment/build acceptance remains unverified. |
| [ ] | PostgreSQL integration | Latest recorded review did not exercise PostgreSQL integration. Run against an explicitly disposable database. |
| [ ] | Performance and scale | Production-size concurrent flow and prescribed repeated p95 benchmark remain unverified. A prior synthetic optimizer smoke run retained a large objective gap. |
| [ ] | Production operations/security acceptance | Complete deployment-specific secret/configuration review, access checks, backup/restore, monitoring and failure recovery verification. Existing auth tests cover only part of this gate. |

Latest recorded verification in [the 8 November integration review](optimizer_app_integration.md): **187 root tests plus 21 subtests, 73 API tests and 18 recommender-service tests passed**, along with TypeScript/Vite checks. These are reported historical results and were not rerun for this document. Real routing/operating providers, Docker deployment, PostgreSQL integration and recommendation satisfaction were not verified by those checks.

## Next completion gates

1. Implement and configure the verified operating-data/routing adapter; preserve UNKNOWN when evidence is missing.
2. Exercise a real-provider trip through E2 → CP-SAT → local E3 → publication, including failures that preserve saved plans.
3. Verify the deployment stack on disposable PostgreSQL and Docker, including SMTP and the effective E5 model/fallback.
4. Complete performance, browser/mobile usability and independent human recommendation evaluation.
5. Confirm retained scope for chat, agent proposals, feedback, optimized alternatives and POI administration, then finish the required extensions.

Do not report an overall completion percentage from this checklist: component sizes differ, and missing real planning data blocks a central user outcome despite substantial implemented code.
