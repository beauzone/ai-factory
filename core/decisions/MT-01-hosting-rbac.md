# Decision Record: MT-01 Hosting Model & RBAC

**Issue:** BEA-116
**Status:** Approved
**Date:** 2026-06-21

## 1. Hosting Model: Bridge-Hybrid
We will implement a **Bridge-Hybrid** architecture. 
- **Shared Logic:** The application layer and orchestration engine will remain shared across tenants to ensure operational efficiency and ease of updates.
- **Siloed Sensitive Data:** Encryption keys, credential stores, and tenant-specific secrets will be stored in isolated logical silos.
- **Reasoning:** This balances the cost-efficiency of a pooled model with the security requirements of a launderette system handling sensitive credentials.

## 2. RBAC: Fixed Role Model
We will implement a **Fixed Role** access control system.
- **Roles:**
    - `Global Admin`: Full system access across all tenants.
    - `Tenant Admin`: Administrative control over their specific tenant environment.
    - `Tenant User`: Standard operational access within the tenant.
- **Constraint:** No custom role definitions will be supported in V1 to minimize complexity and speed up delivery.

## 3. BYOC (Bring Your Own Cloud)
**Deferred to Phase 2.**
- The initial deployment will use a hosted SaaS model. 
- The architecture must remain "cloud-agnostic" at the data layer to allow the future injection of external cloud credentials without a full rewrite.

---
**Approval:** Beau Roberts (Product)
**Implementation Lead:** AI Software Factory
