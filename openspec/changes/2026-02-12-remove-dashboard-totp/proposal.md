# Proposal: Remove dashboard TOTP access control

## Problem

The dashboard includes a TOTP setup + enforcement flow (setup token, settings contract, middleware,
frontend UI, and tests). This feature is unused and adds maintenance cost and complexity.

## Goals

- Remove dashboard TOTP setup and verification endpoints.
- Remove dashboard settings fields related to TOTP.
- Remove dashboard TOTP enforcement middleware.
- Remove dashboard UI related to TOTP.

## Non-goals

- Introduce a replacement dashboard authentication mechanism.
- Drop legacy DB columns/migrations (they can remain as historical/unused fields).

