---
description: "Use when creating, editing, or reviewing Mermaid diagrams in Markdown, especially architecture, ETL, DAG, and data-lineage diagrams. Covers clipping prevention, monospace styling, and rendered SVG validation."
applyTo: "**/*.md"
---

# Mermaid Diagram Rules

- Load and follow the `diagram-authoring` skill for every nontrivial Mermaid diagram.
- Keep node labels concise. Move implementation details, paths, equations, and provenance outside the diagram.
- Default to `flowchart TD` for documentation that may be read on narrow screens. Choose another orientation only when it materially improves the relationship layout.
- Enforce the shared monospace configuration from `.agents/skills/diagram-authoring/assets/mermaid-config.json` when rendering. Do not override it with proportional fonts, negative letter spacing, or a smaller font to force a label to fit.
- Treat inline `%%{init: ...}%%` or YAML-frontmatter directives as host-dependent hints, not proof. Render with Mermaid CLI and `-c .agents/skills/diagram-authoring/assets/mermaid-config.json` for deterministic validation.
- Keep labels ASCII when practical so glyph width is predictable with a monospace font.
- Use explicit line breaks to control long labels, but do not use line breaks to hide labels that are conceptually too long for a node.
- Do not declare a diagram finished from Mermaid source alone. Render it with the shared config in a browser, wait for fonts with `document.fonts.ready`, and run the skill's SVG bounds validator. Commit the rendered SVG when exact presentation must be preserved across hosts.
- If a rendered label overflows its node, shorten or rewrap the label, increase node/canvas dimensions, and rerender. Do not mask overflow with CSS clipping or `overflow: hidden`.
- Prefer D2 or Graphviz when Mermaid cannot accommodate the label density without an unstable or unreadable layout.
