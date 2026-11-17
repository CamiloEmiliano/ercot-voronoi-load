# ADR-[00000]: [Title of Decision]

<!--
STRICT FILENAME & LINKING CONVENTIONS:
- Format: ADR-[5_DIGIT_ID]-[40_CHARACTER_SLUG].md
- Total Filename Length: Exactly 53 characters (including '.md')
- Slug Specification: Exactly 40 characters using lowercase alphanumeric and hyphens.
- Padding Rule: If the natural slug is under 40 characters, pad with trailing hyphens ('-').
- Zero Absolute Paths Invariant: All file, code, test, fixture, and commit references MUST be repository-relative (e.g. `../../<path>` or `<peer_adr>.md`).
  NEVER embed absolute filesystem paths or URI schemes (such as `file:///...`, `/home/...`, `/Users/...`, or drive letters) into ADR documents.
- Linked Commits Format (`template.md` & `index.md`): Implemented entries MUST display the 7-character git commit hash as link text targeting repository-relative files ([`41f9e61`](../../path/to/file)), keeping `index.md` compact. Use `Pending acceptance` while `PROPOSED`, commit the accepted implementation, then backfill the resulting short commit hash rather than displaying file paths as link text.
Example: ADR-00001-record-architecture-decisions-in-repo--.md
-->

- **ADR ID**: ADR-[00000]
- **Filename**: `ADR-[00000]-[exact-40-character-slug-padded-w-hyphen].md`
- **Date**: YYYY-MM-DD HH:MM UTC
- **Status**: [PROPOSED | ACCEPTED | SUPERSEDED BY ADR-XXXXX | DEPRECATED]
- **Target Subsystem**:
- **Linked Commits**: [`commit-hash`](../../path/to/file.py) (must use repository-relative path)

---

## 1. Context & Problem Statement
What was the specific operational, mathematical, or architectural dilemma? What prompted this decision?

## 2. Decision Drivers & Constraints
What were the non-negotiable constraints (e.g., ERCOT data availability, DST/timezone semantics, API/licensing terms, forecast-time leakage boundaries)?

## 3. Considered Alternatives & Rejection Rationale
- **Alternative A**: Description. Why was this rejected?
- **Alternative B**: Description. Why was this rejected?

## 4. Decision Outcome
What concrete architecture, pattern, or policy was chosen, and how is it implemented?

## 5. Invariants & Guardrails
What immutable rules or invariants are established by this decision that future code, pipelines, and tests must never violate?

## 6. Consequences & Downstream Impact
- **Positive / Guarantees**: What capabilities, safety guarantees, or architectural clarity are gained?
- **Negative / Trade-offs**: What operational costs, complexity, or performance trade-offs are accepted?
