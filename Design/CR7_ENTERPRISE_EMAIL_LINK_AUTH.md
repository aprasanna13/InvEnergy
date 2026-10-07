# Change Request CR-7: Enterprise Passwordless Email Link Authentication & Domain-Gated Access Control

* **CR ID:** `CR-007` (Enterprise Passwordless Email Link Authentication & Domain-Gated Access Control — `v0.8.0`)
* **Status:** Reviewed & Fortified via `/egm-review` (Goldfish Comprehension, Critic & Readiness Passed) — Ready for `/design-implement`
* **Parent Architecture:** [DESIGN_DOC.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/DESIGN_DOC.md), [CR1_LANDOWNER_SPECIAL_CONDITIONS.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR1_LANDOWNER_SPECIAL_CONDITIONS.md), [CR2_PROJECT_PORTFOLIO_HIERARCHY.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR2_PROJECT_PORTFOLIO_HIERARCHY.md), [CR3_SUBCONTRACTOR_DND_CHECKLIST.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR3_SUBCONTRACTOR_DND_CHECKLIST.md), [CR4_BIGQUERY_DATA_AGENT_CHAT.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR4_BIGQUERY_DATA_AGENT_CHAT.md), [CR5_SEMANTIC_ZONE_MULTI_PASS_EXTRACTION.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR5_SEMANTIC_ZONE_MULTI_PASS_EXTRACTION.md), [CR6_UNIVERSAL_CLIENT_SERVER_PROGRESS_SYSTEM.md](file:///usr/local/google/home/prasannaankem/Code/Invenergy/Design/CR6_UNIVERSAL_CLIENT_SERVER_PROGRESS_SYSTEM.md)
* **Confirmed Architectural Decisions (Fortified via EGM Review):**
  1. **Google Identity Toolkit Direct REST Dispatch Gate (`accounts:sendOobCode`):**  
     Resolves the Firebase Admin SDK dispatch limitation (where Python Admin SDK only generates a link string without sending an email). The Cloud Run server dispatch gate validates the user's domain and invokes Google's Identity Toolkit REST API (`POST https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode`) using the project's Web API Key with `requestType: "EMAIL_SIGNIN"`. Google's managed email infrastructure delivers the branded sign-in email directly to the recipient's corporate inbox, eliminating third-party SMTP servers or SendGrid dependencies.
  2. **Dual-Key Sliding-Window Rate Limiting with GFE `X-Forwarded-For` Extraction:**  
     To prevent email bombing and resource exhaustion without adding external Redis infrastructure, the server gate enforces a sliding-window in-memory rate limiter with dual keys:
     - **Client IP Limit:** Maximum 5 link dispatch requests per 10 minutes per client IP. The client IP is extracted from the leftmost non-private IP in `X-Forwarded-For` (falling back to `request.client.host`) to avoid throttling all users behind Google Front End (GFE) reverse proxies.
     - **Recipient Email Limit:** Maximum 3 link dispatch requests per 15 minutes per normalized email address, preventing targeted inbox spamming across rotating IPs.
  3. **PDF.js Explicit `httpHeaders` Authorization Injection:**  
     Recognizes that `window.pdfjsLib.getDocument()` runs in a separate Web Worker context and bypasses `window.fetch`. The viewer explicitly passes custom HTTP headers (`httpHeaders: { Authorization: "Bearer " + token }`) into `pdfjsLib.getDocument()`, ensuring `/api/v1/documents/{document_id}/pdf` successfully streams without 401 Unauthorized errors.
  4. **Authenticated Blob-Stream Download Workflow for CSV Exports:**  
     Replaces insecure `window.open()` invocations (which cannot send HTTP Authorization headers) with an authenticated fetch-and-blob download pipeline: `fetch()` retrieves the CSV using the universal Bearer token, converts the stream to an in-memory `Blob`, and programmatically triggers download via an ephemeral `<a download>` anchor.
  5. **Asynchronous Dynamic Token Renewal in Universal Fetch Interceptor:**  
     Replaces static string token caching with dynamic evaluation via `firebase.auth().currentUser.getIdToken(false)`. If an ID token approaches its 60-minute expiration, the Firebase client SDK refreshes it transparently prior to request dispatch, preventing mid-session 401 disconnects. If an API call receives a 401 (e.g. token revoked), the interceptor automatically clears the session and returns to the login screen.
  6. **100% Comprehensive Zero-Trust API Surface Protection:**  
     Closes the security perimeter by attaching FastAPI authentication dependencies across **all 14 confidential endpoints** (including document listings, raw PDF streaming, landowner registries, CSV exports, clause reviews, and GCS batch ingestion). Only two endpoints remain public: `POST /api/v1/auth/request-link` and `GET /api/v1/auth/config`.
  7. **Granular Role-Based Access Control (RBAC):**  
     Decodes the verified JWT email claim to enforce strict role boundaries:
     - **Admin Role:** Granted to all `@google.com` accounts and explicitly designated `@invenergy.com` addresses listed in `ADMIN_EMAIL_WHITELIST`. Admins possess full read, write, upload, GCS batch ingestion, and Human-In-The-Loop (HITL) review permissions.
     - **Viewer Role:** Granted to all other verified `@invenergy.com` accounts. Viewers have read-only access to projects, documents, search, PDF streaming, CSV exports, field DND signoffs, and BigQuery Data Agent chat, but are blocked (HTTP 403) from contract uploads, GCS ingestion, and clause overrides.
  8. **Address Bar Sanitization (`window.history.replaceState`) & Consumed Code Resilience:**  
     Upon successful execution of `signInWithEmailLink()`, the client script immediately cleans the URL (`?apiKey=...&oobCode=...`) from the browser address bar using `window.history.replaceState()`. If a user refreshes the page or clicks an already-consumed link, the UI checks for an existing valid session; if absent, it displays an informative "Link expired or already used" recovery card with a one-click resend button.
  9. **Decoupled Frontend Configuration Delivery (`GET /api/v1/auth/config`):**  
     The server exposes a public endpoint returning the non-secret Firebase client configuration (`apiKey`, `authDomain`, `projectId`) sourced from environment variables, eliminating hardcoded project identifiers in static HTML.
  10. **Hermetic Development, CI/CD, and Local Dev Bypass Switch (`AUTH_ENABLED`):**  
      Provides an `AUTH_ENABLED: bool` flag in `PipelineConfig` (defaulting to `True`). When set to `False` (for offline local development or pytest suites without external Google credentials), the auth dependency automatically injects a mock `UserContext(email="dev@invenergy.com", role=UserRole.ADMIN)`, preserving 100% automated test coverage.
  11. **Cryptographic Identity Binding for Legal Review & DND Field Audit Logs:**  
      Automatically stamps `reviewed_by` in clause reviews and `foreman_email` in DND signoffs with the cryptographically verified `user_context.email` from the JWT, guaranteeing non-repudiation across BigQuery and SQLite audit tables.

---

## 1. The Business Problem & Operational Security Context

The Invenergy Portfolio Contract Intelligence platform processes confidential energy infrastructure land leases, landowner covenants, financial penalties, and construction field restrictions across multiple clean energy assets. 

The current system relies on a client-side JavaScript authentication check (`google123` and `invtest123` stored in `sessionStorage`). While this provides a visual barrier on the browser DOM, it introduces critical business and enterprise security vulnerabilities:

1. **Unprotected API Endpoints:** Any user who inspects network traffic or queries the Cloud Run service URL directly can access `/api/v1/projects`, `/api/v1/portfolio/search`, `/api/v1/documents/{doc_id}/pdf`, and `/api/v1/agent/chat` without providing any credentials.
2. **Shared Static Credentials:** Using hardcoded demo credentials prevents individual user accountability, makes access revocation impossible without a full redeployment, and fails enterprise client security audits.
3. **The External Customer Barrier:** External evaluators (such as Invenergy executives, land agents, and legal counsel) do not possess Google Cloud IAM accounts or access to the host Argolis project. They use Microsoft 365, Outlook, or corporate email. Requiring them to register GCP accounts creates massive onboarding friction and blocks pilots.
4. **Risk of Open Self-Service:** If self-service registration is enabled without strict identity boundaries, unauthorized third parties on the public internet could enter personal email addresses and access proprietary project agreements.

### Business Objective
Implement a secure, zero-friction, passwordless authentication gateway that:
- Allows external customers to self-authenticate seamlessly using their corporate email.
- Mathematically restricts all system entry and email link dispatch strictly to authorized corporate domains: `@invenergy.com` and `@google.com`.
- Operates natively on Cloud Run **without requiring an expensive or complex Cloud Load Balancer**.
- Cryptographically secures both the frontend UI and 100% of backend REST API routes via short-lived JSON Web Tokens (JWTs).
- Preserves full audit accountability by binding legal review overrides and field checklist signoffs to verified corporate identities.

---

## 2. Fortified Technical Architecture

```mermaid
flowchart TD
    subgraph Browser["1. Frontend Client Layer (contract_parser/static/index.html)"]
        A["User enters corporate email\n(e.g. user@invenergy.com)"] --> B{"Client Validation:\nDomain in allowed list?"}
        B -->|Invalid| C["Show error message:\n'Only @invenergy.com and @google.com allowed'"]
        B -->|Valid| D["Store email in localStorage\nPOST /api/v1/auth/request-link"]
        
        K["User clicks link in Outlook/Gmail\nRedirects to /?apiKey=...&oobCode=..."] --> L{"localStorage has\nemailForSignIn?"}
        L -->|Yes| M["Execute signInWithEmailLink(email, url)"]
        L -->|No (Cross-device / Incognito)| N["Prompt: 'Confirm email address'\nExecute signInWithEmailLink(inputEmail, url)"]
        
        M & N --> O["Clean URL via window.history.replaceState()"]
        O --> P["Retrieve signed JWT ID token"]
        P --> Q["Universal Fetch & PDF.js Interceptors:\nInject Authorization: Bearer <ID_TOKEN>\nDynamic token refresh via getIdToken(false)"]
    end

    subgraph ServerGate["2. Cloud Run Server Dispatch Gate (contract_parser/auth.py & app.py)"]
        D --> E{"Dual-Key Rate Limiter:\nIP < 5 req / 10m (X-Forwarded-For)\nEmail < 3 req / 15m"}
        E -->|Rate Limited| F["HTTP 429 Too Many Requests"]
        E -->|Allowed| G{"Domain Gate:\nEnds with @invenergy.com\nor @google.com?"}
        G -->|Unauthorized| H["HTTP 403 Forbidden\n(Zero email dispatched)"]
        G -->|Authorized| I["Call Google Identity Toolkit REST API:\nPOST accounts:sendOobCode\nrequestType: EMAIL_SIGNIN"]
        I --> J["Google Managed Infrastructure\ndelivers branded email to inbox"]
    end

    subgraph APIProtection["3. Zero-Trust API Protection & Granular RBAC (FastAPI)"]
        Q --> R["Incoming API Call:\nDepends(get_current_user)"]
        R --> S["Verify Firebase JWT Signature,\nAudience, Expiration & Project ID"]
        S --> T{"Role Resolution"}
        T -->|@google.com or ADMIN_EMAIL_WHITELIST| U["Admin Role Context\n(Upload, Ingest, Review, Signoff, Chat, Read)"]
        T -->|@invenergy.com Standard| V["Viewer Role Context\n(Search, Read, PDF Stream, Export, Signoff, Chat)"]
        U --> W[("BigQuery Lakehouse & SQLite Store")]
        V --> W
        V -.->|Blocked on Mutations| X["HTTP 403 Forbidden\n(Upload, GCS Ingest, Clause Edit)"]
    end
```

### Core Architecture Components:

1. **Client-Side Domain Validation & Pre-Flight Feedback:**  
   When a user inputs their email address on the login screen, client-side regex immediately enforces that the domain matches `@invenergy.com` or `@google.com`. Invalid domains are rejected instantly on the DOM, saving server round-trips.

2. **Server-Side Identity Toolkit REST Dispatch Gate:**  
   The browser calls `POST /api/v1/auth/request-link` with `{ "email": "user@invenergy.com" }`. The server enforces:
   - Client IP extraction via `X-Forwarded-For` with sliding-window rate limiting.
   - Exact email normalization and domain whitelisting.
   - Dispatch to Google Identity Toolkit REST API (`POST https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key=[API_KEY]`) with payload:
     ```json
     {
       "requestType": "EMAIL_SIGNIN",
       "email": "user@invenergy.com",
       "continueUrl": "https://contract-parser-255093976233.us-central1.run.app"
     }
     ```
   Google Cloud Identity Platform formats the email template and sends the secure magic link to the recipient inbox.

3. **Cross-Device Handshake & Address Bar Sanitization:**  
   - When the user opens the link, the browser executes `firebase.auth().isSignInWithEmailLink(window.location.href)`.
   - If the link is opened on the same browser, the email is retrieved from `localStorage.getItem("emailForSignIn")`.
   - If opened on a different device or in incognito mode, the UI renders a streamlined modal asking the user to confirm their corporate email address before completing sign-in.
   - Upon successful verification, `window.history.replaceState({}, document.title, window.location.pathname)` immediately strips `apiKey` and `oobCode` from the URL, preventing re-consumption crashes on page reloads.

4. **Dynamic Token Life Cycle & Asynchronous Header Injection:**  
   The universal `window.fetch` interceptor calls `await firebase.auth().currentUser.getIdToken(false)` on every `/api/v1/` request, guaranteeing that tokens nearing their 1-hour expiration are silently and automatically refreshed.

5. **PDF.js Web Worker Authorization Injection:**  
   PDF rendering invokes `window.pdfjsLib.getDocument({ url: pdfUrl, httpHeaders: { Authorization: "Bearer " + token } })`, allowing full cryptographic protection of proprietary contract PDFs without breaking document rendering.

6. **Authenticated Fetch-Blob Export Mechanism:**  
   CSV exports execute via `fetch()`, inheriting the Bearer token, convert the response into a `Blob`, and trigger a clean client download.

7. **Cryptographic Identity Binding for Audit Trails:**  
   Whenever a user approves a clause or signs off on a field DND checklist, the backend assigns `reviewed_by = user_context.email` directly from the validated token, establishing non-repudiation in BigQuery.

---

## 3. Comprehensive Role-Based Access Control (RBAC) Matrix

| Endpoint | Method | Required Role | Viewer Behavior (`@invenergy.com`) | Admin Behavior (`@google.com` or Whitelist) |
| :--- | :--- | :--- | :--- | :--- |
| `/api/v1/auth/request-link` | `POST` | Public | Allowed (subject to domain & rate limit) | Allowed (subject to domain & rate limit) |
| `/api/v1/auth/config` | `GET` | Public | Returns public Firebase config | Returns public Firebase config |
| `/api/v1/auth/me` | `GET` | Authenticated | Returns `{ email, role: "viewer", domain }` | Returns `{ email, role: "admin", domain }` |
| `/api/v1/projects` | `GET` | Authenticated | Allowed | Allowed |
| `/api/v1/projects` | `POST` | Admin | **HTTP 403 Forbidden** | Allowed (creates new project) |
| `/api/v1/projects/{id}/landowners` | `GET` | Authenticated | Allowed | Allowed |
| `/api/v1/landowners` | `GET` | Authenticated | Allowed | Allowed |
| `/api/v1/portfolio/search` | `GET` | Authenticated | Allowed | Allowed |
| `/api/v1/documents` | `GET` | Authenticated | Allowed | Allowed |
| `/api/v1/documents/{id}` | `GET` | Authenticated | Allowed | Allowed |
| `/api/v1/documents/{id}/pdf` | `GET` | Authenticated | Allowed (renders in PDF.js with headers) | Allowed |
| `/api/v1/documents/{id}/export/{csv}` | `GET` | Authenticated | Allowed (downloaded via auth fetch blob) | Allowed |
| `/api/v1/documents/upload` | `POST` | Admin | **HTTP 403 Forbidden** (DOM input disabled) | Allowed (triggers multi-pass extraction) |
| `/api/v1/documents:ingest` | `POST` | Admin | **HTTP 403 Forbidden** | Allowed (triggers extraction) |
| `/api/v1/documents/ingest-gcs` | `POST` | Admin | **HTTP 403 Forbidden** | Allowed (triggers GCS batch) |
| `/api/v1/documents/{id}/clauses/{node}` | `PATCH` | Admin | **HTTP 403 Forbidden** (review form locked) | Allowed (stamps `reviewed_by=user.email`) |
| `/api/v1/documents/{id}/dnd-checklist` | `GET` | Authenticated | Allowed | Allowed |
| `/api/v1/documents/{id}/dnd-checklist:signoff` | `POST` | Authenticated | Allowed (field crews can sign off tailgates) | Allowed |
| `/api/v1/agent/info` | `GET` | Authenticated | Allowed | Allowed |
| `/api/v1/agent/chat` | `POST` | Authenticated | Allowed (can ask BigQuery questions) | Allowed |

---

## 4. Alternatives Considered & Ruled Out

| Alternative | Mechanism | Why It Was Ruled Out |
| :--- | :--- | :--- |
| **Direct Cloud Run IAM (`--no-allow-unauthenticated`)** | Uses `roles/run.invoker` on Cloud Run. | **Broken for human web browsers.** Standard browsers do not send Bearer tokens on initial navigation, resulting in an unbranded HTTP 403 Forbidden screen instead of a branded login page. |
| **Cloud Load Balancer + Identity-Aware Proxy (IAP)** | HTTPS ALB + Serverless NEG + IAP + Identity Platform. | **Excessive overhead for pilot/demo.** Requires provisioning external load balancers, reserved IP addresses, custom domain DNS records, and SSL certificates, introducing weeks of provisioning delay in Argolis sandboxes. |
| **Self-Service Passwords (Username + Password DB)** | Store password hashes in SQLite / BigQuery. | **High friction and vulnerability.** External executives forget passwords, require password reset mechanisms, and are vulnerable to weak password selection and credential leaks. |
| **Client-Only Firebase Link Generation** | Call `sendSignInLinkToEmail` directly from frontend JS. | **Spam & abuse vulnerability.** Allows anyone with browser developer tools to send arbitrary emails through Google's infrastructure to unapproved external addresses. |
| **Firebase Admin SDK Python Link Generation (`auth.generate_sign_in_with_email_link`)** | Python Admin SDK returns link string. | **Does not send email.** Python Admin SDK only generates a raw link string and requires an external mail transport (SendGrid/SMTP), which is not configured in this Cloud Run project. |

---

## 5. Detailed Implementation Plan

### Enumeration of Affected Files

```
contract_parser/
├── auth.py                          [NEW] Server-side Firebase token validation, rate limiter, Identity Toolkit REST client
├── config.py                        [MODIFY] Add AUTH_ENABLED, Firebase API key, and admin whitelist configuration
├── app.py                           [MODIFY] Register auth routes, config endpoint, and secure all /api/v1/ endpoints
├── static/
│   └── index.html                   [MODIFY] Modern passwordless UI, dynamic token fetch interceptor, PDF.js auth headers
pyproject.toml                       [MODIFY] Add firebase-admin dependency
tests/
└── test_cr7_auth.py                 [NEW] Unit & integration tests for token, domain, rate limit, and RBAC validation
Changelog.md                         [MODIFY] Append version [0.8.0] release notes
```

---

### File 1: `contract_parser/config.py` (Modifications)
**Purpose:** Centralize authentication and identity configuration.

**Additions to `PipelineConfig`:**
```python
auth_enabled: bool = field(
    default_factory=lambda: os.getenv("AUTH_ENABLED", "true").lower() in ("true", "1", "yes")
)
firebase_api_key: str = field(
    default_factory=lambda: os.getenv("FIREBASE_API_KEY", "")
)
firebase_auth_domain: str = field(
    default_factory=lambda: os.getenv("FIREBASE_AUTH_DOMAIN", "pr-tftest.firebaseapp.com")
)
admin_email_whitelist: set[str] = field(
    default_factory=lambda: {
        e.strip().lower()
        for e in os.getenv("ADMIN_EMAIL_WHITELIST", "lead.evaluator@invenergy.com").split(",")
        if e.strip()
    }
)
```

---

### File 2: `contract_parser/auth.py` (New File)
**Purpose:** Encapsulates rate limiting, Google Identity Toolkit REST dispatch, Firebase ID token verification, and role resolution.

**Key Components:**
1. **Data Models:**
   ```python
   class UserRole(str, Enum):
       ADMIN = "admin"
       VIEWER = "viewer"

   class UserContext(BaseModel):
       email: str
       role: UserRole
       domain: str
       uid: str
   ```
2. **In-Memory Sliding-Window Rate Limiter:**
   - Tracks timestamps per client IP (limit: 5 per 10m) and per normalized email (limit: 3 per 15m).
   - Helper `get_client_ip(request: Request) -> str` parses `X-Forwarded-For` taking the leftmost non-private IP.
3. **Dispatch Function `dispatch_email_sign_in_link(email: str, continue_url: str, config: PipelineConfig)`:**
   - Checks allowed domains: `ALLOWED_DOMAINS = {"invenergy.com", "google.com"}`.
   - Dispatches HTTP POST to `https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={config.firebase_api_key}`.
4. **Token Verification Dependency `get_current_user(request: Request, config: PipelineConfig) -> UserContext`:**
   - If `config.auth_enabled` is `False`, returns mock Admin `UserContext(email="dev@invenergy.com", role=UserRole.ADMIN, domain="invenergy.com", uid="dev_uid")`.
   - Extracts `Bearer <token>` from `Authorization` header.
   - Verifies token via `firebase_admin.auth.verify_id_token(token)`.
   - Checks decoded email domain against `ALLOWED_DOMAINS`.
   - Resolves role:
     - `role = UserRole.ADMIN` if domain == `google.com` or email in `config.admin_email_whitelist`.
     - `role = UserRole.VIEWER` otherwise.
5. **Role Guard `require_admin(user: UserContext = Depends(get_current_user)) -> UserContext`:**
   - Raises `HTTPException(status_code=403, detail="Admin role required for this action")` if `user.role != UserRole.ADMIN`.

---

### File 3: `contract_parser/app.py` (Modifications)
**Purpose:** Expose authentication API routes and enforce token authentication across all portfolio, extraction, and audit endpoints.

**Modifications:**
1. **New Public Endpoint `GET /api/v1/auth/config`:**
   - Returns `{ "apiKey": config.firebase_api_key, "authDomain": config.firebase_auth_domain, "projectId": config.google_cloud_project }`.
2. **New Public Endpoint `POST /api/v1/auth/request-link`:**
   - Validates rate limits and email domain.
   - Invokes `dispatch_email_sign_in_link()`.
   - Returns `{ "status": "sent", "email": email }`.
3. **New Authenticated Endpoint `GET /api/v1/auth/me`:**
   - Returns the authenticated `UserContext` payload.
4. **Securing Endpoints via Dependencies:**
   - Attach `Depends(get_current_user)` to:
     - `GET /api/v1/projects`
     - `GET /api/v1/projects/{id}/landowners`
     - `GET /api/v1/landowners`
     - `GET /api/v1/portfolio/search`
     - `GET /api/v1/documents`
     - `GET /api/v1/documents/{id}`
     - `GET /api/v1/documents/{id}/pdf`
     - `GET /api/v1/documents/{id}/export/{csv_name}`
     - `GET /api/v1/documents/{id}/dnd-checklist`
     - `POST /api/v1/documents/{id}/dnd-checklist:signoff`
     - `GET /api/v1/agent/info`
     - `POST /api/v1/agent/chat`
   - Attach `Depends(require_admin)` to:
     - `POST /api/v1/projects`
     - `POST /api/v1/documents/upload`
     - `POST /api/v1/documents:ingest`
     - `POST /api/v1/documents/ingest-gcs`
     - `PATCH /api/v1/documents/{id}/clauses/{node_id}`
5. **Identity Binding:**
   - `review_clause_endpoint`: Sets `review.reviewed_by = current_user.email`.
   - `record_document_dnd_signoff_endpoint`: Automatically records `current_user.email` in the sign-off record.

---

### File 4: `contract_parser/static/index.html` (Modifications)
**Purpose:** Integrate Firebase Auth Client SDK, modern passwordless UI, dynamic token fetch interceptor, and PDF.js/CSV auth headers.

**Modifications:**
1. **Firebase SDK Initialization:**
   - Include `firebase-app-compat.js` and `firebase-auth-compat.js`.
   - On page load, fetch `/api/v1/auth/config` and call `firebase.initializeApp(config)`.
2. **Modern Passwordless Login Overlay:**
   - Replace old username/password inputs with a single corporate work email input:
     ```html
     <div class="login-field">
       <label for="login-email">Corporate Work Email</label>
       <input type="email" id="login-email" placeholder="name@invenergy.com or name@google.com" />
     </div>
     <button type="button" class="login-btn" onclick="submitEmailLinkRequest()">Send Secure Sign-In Link</button>
     ```
   - Add inline notification card for sent status: *"Sign-in link sent! Please check your Outlook or Gmail inbox and click the link to enter."*
3. **Email Link Callback Handler:**
   - Check `firebase.auth().isSignInWithEmailLink(window.location.href)`.
   - Retrieve stored email from `localStorage.getItem("emailForSignIn")` or display cross-device prompt.
   - Execute `await firebase.auth().signInWithEmailLink(email, window.location.href)`.
   - Call `window.history.replaceState({}, document.title, window.location.pathname)` immediately after successful login.
4. **Universal Fetch Interceptor (`window.fetch`):**
   - Intercepts all `/api/v1/` calls and injects `Authorization: Bearer <ID_TOKEN>` by dynamically calling `await firebase.auth().currentUser.getIdToken(false)`.
   - On 401 response, clears state and displays login overlay.
5. **PDF.js Loading Task Integration:**
   - Update `window.pdfjsLib.getDocument` in `renderPdf()` to pass `httpHeaders: { Authorization: "Bearer " + token }`.
6. **CSV Download Interceptor:**
   - Update `downloadCsv()` to use `fetch()` with `blob()` conversion and `<a download>` trigger.
7. **Role-Based UI Adjustments:**
   - If `user.role === "viewer"`, disable upload inputs, hide GCS ingest controls, disable clause review edit buttons, and render a high-visibility `👤 [email] (Viewer)` status chip in the top navigation bar.

---

### File 5: `pyproject.toml` (Modifications)
**Purpose:** Add required Google identity dependencies.

**Modifications:**
- Add `firebase-admin>=6.5.0` to `[project.dependencies]`.

---

### File 6: `tests/test_cr7_auth.py` (New File)
**Purpose:** Hermetic automated testing covering domain filtering, rate limiting, token verification, and role-based access control.

**Test Cases:**
1. `test_allowed_domains`: Validates `@google.com` and `@invenergy.com` are accepted.
2. `test_rejected_domains`: Validates `@gmail.com`, `@yahoo.com`, and `@competitor.com` return HTTP 403.
3. `test_rate_limiter_ip_threshold`: Validates that more than 5 link requests from the same IP within 10 minutes return HTTP 429.
4. `test_rate_limiter_email_threshold`: Validates that more than 3 link requests for the same email within 15 minutes return HTTP 429.
5. `test_unauthenticated_api_rejection`: Confirms `/api/v1/projects`, `/api/v1/documents`, and `/api/v1/documents/{id}/pdf` return HTTP 401 when no token is supplied.
6. `test_viewer_role_upload_guard`: Confirms a verified `@invenergy.com` viewer is blocked (HTTP 403) from calling `/api/v1/documents/upload` and `/api/v1/documents/ingest-gcs`.
7. `test_admin_role_upload_allowed`: Confirms a verified `@google.com` or whitelisted admin can access mutation routes.
8. `test_clause_review_identity_binding`: Validates that `reviewed_by` is stamped with the caller's verified JWT email.
9. `test_auth_disabled_bypass_mode`: Confirms that when `AUTH_ENABLED=false`, endpoints operate in development mode without external token verification.

---

## 6. Comprehensive Security & Edge Case Matrix

| Edge Case | Failure Mode Without Guard | Guard Implemented |
| :--- | :--- | :--- |
| **Spam / Email Bombing** | Attacker calls API repeatedly to spam executive inboxes. | Dual-key in-memory rate limiter caps requests to 5 per IP / 10m and 3 per email / 15m. Returns HTTP 429. |
| **GFE Reverse Proxy IP Masking** | All Cloud Run users share same Google Front End IP and get throttled together. | Rate limiter extracts real client IP from leftmost non-private entry in `X-Forwarded-For`. |
| **Competitor Probing** | Unauthorized domain attempts to register. | Server rejects domain immediately with HTTP 403; zero email or token is generated. |
| **Corporate SafeLinks Detonation** | Microsoft Defender pre-clicks URL in email. | Initial HTTP GET request does not consume `oobCode`; code is only consumed when browser JavaScript executes `signInWithEmailLink()`. |
| **Re-clicking / Refreshing with Consumed Code** | User refreshes page or clicks email link a second time, triggering `auth/invalid-action-code`. | Client cleans URL via `window.history.replaceState()` immediately upon sign-in. If code is invalid, UI checks for existing session or presents clean "Link expired" prompt with 1-click resend. |
| **Cross-Device Handshake** | User opens email link on mobile phone or in incognito window where `localStorage` is empty. | Fallback modal prompts user to confirm their email address and completes `signInWithEmailLink(inputEmail, url)`. |
| **60-Minute Token Expiration** | Long review session causes static token to expire, breaking subsequent API calls with 401. | Universal fetch interceptor dynamically retrieves fresh token via `await user.getIdToken(false)` before every dispatch. |
| **PDF.js Web Worker Request Failure** | PDF viewer fails with blank canvas and 401 error because Web Worker bypasses `window.fetch`. | PDF viewer explicitly passes `httpHeaders: { Authorization: "Bearer " + token }` to `pdfjsLib.getDocument()`. |
| **CSV Download Failure** | Direct `window.open()` cannot attach Bearer headers, resulting in 401 error. | Refactored to authenticated fetch-and-blob workflow with programmatic anchor download. |
| **Audit Trail Repudiation** | User submits modified legal clause with falsified `reviewed_by` string. | Backend overrides client-supplied reviewer string with cryptographically validated `user_context.email` from JWT. |
| **Offline Dev / Hermetic Pytest Execution** | Tests fail without live GCP credentials to verify Google's public certificates. | `AUTH_ENABLED=false` config switch enables automatic mock `UserContext` injection for hermetic test execution. |

---

## 7. Definition of Done & Success Criteria

1. **Strict Domain Enforcement:** Attempting to request a link with any email other than `@google.com` or `@invenergy.com` returns an explicit HTTP 403 Forbidden error.
2. **Dual-Key Rate Limiting:** Exceeding IP (5 / 10m) or email (3 / 15m) thresholds returns an explicit HTTP 429 Too Many Requests error.
3. **Zero Direct API Leakage:** Querying any `/api/v1/` endpoint (including `/projects`, `/documents`, `/pdf`, `/export`, and `/agent/chat`) without a valid Firebase ID token returns HTTP 401 Unauthorized.
4. **Passwordless UX:** An authorized `@invenergy.com` user receives an email link, clicks it, and lands directly in the authenticated workspace with appropriate Viewer permissions.
5. **PDF Rendering & CSV Export Functionality:** PDF contracts stream and render cleanly in PDF.js, and CSV exports download successfully via authenticated Blob streams.
6. **Zero Load Balancer:** Complete lifecycle executes natively on the Cloud Run URL (`https://contract-parser-255093976233.us-central1.run.app`).
7. **Automated Test Coverage:** 100% of test cases in `tests/test_cr7_auth.py` pass under `pytest`.
