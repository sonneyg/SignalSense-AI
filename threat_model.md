# STRIDE Threat Model Assessment: SignalSense AI Agent Graph

This document details the systematic security threat modeling assessment conducted on the **SignalSense AI agent graph** codebase and microservice architecture.

---

## 1. System Boundaries & Data Flows

The application follows a decoupled microservice architecture:
1. **Frontend Portals**:
   - **Member Ambassador App** (Port 8083): FastAPI web app handling member sessions, OOS reporting, and upvoting candidate products.
   - **Operations Dashboard** (Port 8084): FastAPI web app handling associate workflows (verification, replenishment, and merchant decisions).
2. **Backend ADK Agent** (Port 8080): Runs a FastAPI router wrapping a Google ADK `Workflow` (`root_agent`) via `fast_api_app.py` or `agent_runtime_app.py`.
3. **Data Layer**: A local SQLite database (`enterprise_db/enterprise.db`) shared by both services.
4. **Vertex AI/Gemini**: The backend agent calls the Vertex AI API (Gemini LLM model) for text and voice classifications.

```
+---------------------------+                 +-------------------------------+
|  Member Ambassador App    |                 |   SignalSense Enterprise      |
|  (Frontend on Port 8083)  | --- HTTP POST ->|   (Backend ADK on Port 8080)  |
+---------------------------+                 +-------------------------------+
              |                                               |
              +---------- Reads / Writes SQLite DB <----------+
                            (enterprise_db/enterprise.db)
```

---

## 2. STRIDE Evaluation

### 1. Spoofing (Identity Theft)
* **Risk Description**: A malicious client could send request payloads directly to the backend API (`http://127.0.0.1:8080/run`) spoofing a legitimate `member_id` or role.
* **Current Status / Controls**:
  - `fast_api_app.py` implements a `JWTVerificationMiddleware` that validates a signed JWT token in the `Authorization` header.
* **Security Gaps**:
  - 🛑 **Bypassed Entry Point**: The Vertex AI Reasoning Engine deployment entry point `agent_runtime_app.py` exposes the ADK `AgentEngineApp` but does **not** load `fast_api_app.py` or apply the security middlewares. This means authentication is completely bypassed in production/runtime deployments.
  - 🛑 **Hardcoded Secret Key**: The `SECRET_KEY` used for signing and verifying tokens is hardcoded in `jwt_helper.py` as a static string `"signalsense-super-secure-key-change-in-production"`. Any attacker with repository access can sign valid tokens for any role.
  - 🛑 **Weak Session Generation**: Frontend portals automatically generate valid session cookies on page load if they are missing or invalid, bypassing actual credential verification.
* **Severity**: **Critical**
* **Recommended Mitigation**: 
  - Standardize on a single secure entry point that enforces JWT token validation.
  - Load the JWT `SECRET_KEY` securely from environment variables (e.g. via GCP Secret Manager) instead of hardcoding.
  - Require actual authentication (e.g., username/password or single sign-on) before generating session tokens.

### 2. Tampering (Data Modification)
* **Risk Description**: A malicious user could alter the parameters of a payload (e.g. inflating reward points, changing a signal's status directly, or modifying database records).
* **Current Status / Controls**:
  - **Strong Controls**: All calculations for reward points (`Points`) and trust score adjustments (`TrustIncrease`) are computed internally inside python database logic within the workflow nodes (`process_oos`, `handle_suggestion_analysis`, `process_upvote`) rather than trusting values in the request payload.
  - Input parameters are sanitized and type-checked via a helper (`clean`) and Pydantic schemas (`SignalInput`).
  - **SQL Injection (SQLi) Defense**: A comprehensive review of `agent.py` shows that all SQL executions are parameterized using `?` placeholders. There are no raw string formatting concatenations in SQL executions.
* **Security Gaps**:
  - 🛑 **Local SQLite File Access**: The database is a local file (`enterprise.db`). Any compromised process or user on the system can modify the database file directly, bypassing all application-level logic.
* **Severity**: **Medium**
* **Recommended Mitigation**: 
  - Restrict write permissions on the SQLite database file at the OS level.
  - For production deployments, migrate from a local SQLite file to a managed, authenticated cloud database (e.g. Cloud SQL) with IAM access controls.

### 3. Repudiation (Audit Trail Erasure)
* **Risk Description**: A user or associate could perform a critical action (like altering inventory stock counts or deleting suggestions) without leaving an immutable audit trail.
* **Current Status / Controls**:
  - Critical actions are logged in SQLite tables (`signals`, `candidate_products`, `checkout_sessions`) with timestamps.
* **Security Gaps**:
  - 🛑 **Logs Stored in Local DB**: Since the transaction logs are stored in standard SQLite tables in the same file as the application data, anyone with write access to the database file can easily delete or manipulate the audit logs.
  - 🛑 **Lack of Immutable Audit Trails**: The application does not stream transaction logs to an external, tamper-proof logging system.
* **Severity**: **Medium**
* **Recommended Mitigation**: 
  - Configure the application to stream audit logs to a secure external service, such as Google Cloud Logging, using a write-once read-many (WORM) storage model.
  - Log all authentication and authorization failures.

### 4. Information Disclosure (Data Leakage)
* **Risk Description**: Guessable sequential IDs allow users to scrape private customer information, or system errors leak internal implementation details.
* **Current Status / Controls**:
  - Frontend and backend promise rejections catch and hide raw python traceback logs to prevent exposing database structures or tokens.
* **Security Gaps**:
  - 🛑 **Sequential Member & Candidate IDs**: Member IDs (`M1001`, `M1002`) and Candidate IDs (`P1001`, `P1002`) are sequential and easily guessable. This permits Insecure Direct Object Reference (IDOR) attacks, enabling bulk profile scraping.
  - 🛑 **PII Transmission to LLMs**: Voice transcripts and text descriptions are sent directly to the Vertex AI Gemini models without sanitization, risking exposure of PII if users type or speak sensitive data.
* **Severity**: **High**
  - Replace sequential integer-based IDs with cryptographically secure random identifiers (e.g., UUIDv4) for database primary keys.
  - Implement a prompt sanitization utility to scrub PII (such as phone numbers, emails, and names) before sending payloads to LLM APIs.

### 5. Denial of Service (DoS)
* **Risk Description**: An attacker could flood the API with requests, locking the database, or exhausting expensive Vertex AI LLM API billing quotas.
* **Current Status / Controls**:
  - `RateLimitingMiddleware` is defined in `rate_limiter.py` to limit request rates to 60 requests per minute per IP.
* **Security Gaps**:
  - 🛑 **In-Memory Rate Limiting**: The rate-limiter tracks IP request rates in an in-memory `defaultdict`. If the application is deployed behind a load balancer with multiple instances, rate limits are not shared, rendering them ineffective.
  - 🛑 **No LLM Output Token Limits**: The LlmAgents in `agent.py` (`suggestion_analyzer`, `voice_classifier`) do not set output token limits or request timeouts, leaving the system vulnerable to prompt injection attacks that force long generative loops, escalating costs.
  - 🛑 **SQLite Concurrency Limits**: SQLite locks the database file on writes, making it highly susceptible to DoS under concurrent write load.
* **Severity**: **High**
* **Recommended Mitigation**: 
  - Migrate the rate limiter to use a shared backend cache (such as Redis) to synchronize request counts across instances.
  - Set explicit `max_output_tokens` and timeouts on all Gemini LLM API calls.
  - Migrate to a highly concurrent database engine (such as PostgreSQL or Cloud SQL) for production workloads.

### 6. Elevation of Privilege (Access Bypass)
* **Risk Description**: A regular member could craft a payload with a signal type of `AssociateAction` or `MerchantAction` and submit it directly to the API, bypassing user role constraints.
* **Current Status / Controls**:
  - `fast_api_app.py` checks the role in the JWT claims.
* **Security Gaps**:
  - 🛑 **Lack of Node-Level Authorization**: The ADK agent graph (`agent.py`) itself does **not** check the calling identity's role inside its workflow nodes. Once a request bypasses the HTTP middleware layer (e.g. via `agent_runtime_app.py` or fallback in-process runners), the workflow executes any node purely based on the input payload's `signal_type` parameter (e.g., `signal_type="MerchantAction"`).
* **Severity**: **Critical**
* **Recommended Mitigation**: 
  - Pass user claims/roles through the ADK `Context` object into the workflow nodes.
  - Implement role-based access checks (RBAC) directly inside sensitive workflow nodes (e.g. `process_associate_action`, `process_merchant_action`) to verify that the executing identity has the required privileges.

---

## 3. Prioritized Action Items

| Priority | Threat Category | Risk Area | Recommended Action |
|---|---|---|---|
| **1** | Spoofing / Elevation | Entry Point Bypass | Standardize on `fast_api_app.py` or integrate JWT middleware directly in `agent_runtime_app.py`. |
| **2** | Elevation of Privilege | Lack of Node-Level RBAC | Implement role check assertions inside individual ADK workflow nodes using `Context`. |
| **3** | Spoofing | Hardcoded Secret Key | Move `SECRET_KEY` to environment variables loaded from a secure vault (e.g. Secret Manager). |
| **4** | Denial of Service | Vertex AI Cost & SQLite locking | Set strict output token limits/timeouts on LLMs and migrate database to PostgreSQL/Cloud SQL. |
| **5** | Information Disclosure | Enumerable Member/Product IDs | Migrate from sequential IDs to random UUIDv4 identifiers. |
| **6** | Repudiation | Local SQLite audit logs | Set up cloud log streaming (e.g. Cloud Logging) for immutable security audit trails. |
