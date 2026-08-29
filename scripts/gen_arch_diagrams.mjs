#!/usr/bin/env node
/**
 * Generate ASCII architecture diagrams for Asciicker codebase documentation.
 * Uses beautiful-mermaid to render mermaid diagrams as ASCII art.
 *
 * Usage: node scripts/gen_arch_diagrams.mjs
 * Output: docs/ARCHITECTURE_DIAGRAMS.md
 */

import { renderMermaidAscii } from 'beautiful-mermaid';
import { writeFileSync } from 'fs';

// ============================================================================
// DIAGRAM DEFINITIONS
// ============================================================================

const diagrams = {
  // High-level system architecture
  systemOverview: {
    title: "System Overview",
    description: "High-level view of Asciicker components and their relationships",
    mermaid: `graph TD
      A[game.cpp] --> B[render.cpp]
      A --> C[physics.cpp]
      A --> D[world.cpp]
      A --> E[terrain.cpp]
      D --> F[.a3d Files]
      E --> F
      B --> G[sprite.cpp]
      G --> H[.xp Files]
      I[asciiid.cpp] --> D
      I --> E
      I --> J[Blender Addon]
      K[web/game_web.cpp] --> A
      L[game_app.cpp] --> A`
  },

  // Render pipeline
  renderPipeline: {
    title: "Rendering Pipeline",
    description: "6-stage software rasterization pipeline (render.cpp)",
    mermaid: `graph LR
      A[Clear] --> B[Terrain]
      B --> C[Sprites]
      C --> D[Shadow]
      D --> E[Reflection]
      E --> F[Resolve]
      F --> G[AnsiCell Output]`
  },

  // Asset loading flow
  assetPipeline: {
    title: "Asset Loading Pipeline",
    description: "How assets flow from files to rendering",
    mermaid: `graph TD
      A[.xp File] --> B[gzip decompress]
      B --> C[Parse layers]
      C --> D[Palette quantize]
      D --> E[Atlas assembly]
      E --> F[Sprite struct]
      F --> G[render.cpp]`
  },

  // World/terrain spatial indexing
  spatialIndexing: {
    title: "Spatial Indexing",
    description: "BSP tree (world) and Quadtree (terrain) structures",
    mermaid: `graph TD
      A[World Query] --> B{BSP Tree}
      B --> C[Left Child]
      B --> D[Right Child]
      C --> E[Mesh Instances]
      D --> E
      F[Terrain Query] --> G{Quadtree}
      G --> H[NW]
      G --> I[NE]
      G --> J[SW]
      G --> K[SE]
      H --> L[Height Patches]`
  },

  // Platform abstraction
  platformTargets: {
    title: "Platform Targets",
    description: "Build targets and platform entry points",
    mermaid: `graph TD
      A[Core Engine] --> B[game_app.cpp]
      A --> C[web/game_web.cpp]
      A --> D[game_svr.cpp]
      A --> E[asciiid.cpp]
      A --> F[terminal.cpp]
      B --> G[SDL/X11/Win]
      C --> H[Emscripten/WASM]
      D --> I[Server]
      E --> J[Editor/ImGui]
      F --> K[Pure Terminal]`
  },

  // Data flow
  gameLoop: {
    title: "Main Game Loop",
    description: "game.cpp orchestration flow",
    mermaid: `graph LR
      A[Input] --> B[Physics]
      B --> C[Combat]
      C --> D[Animation]
      D --> E[Render]
      E --> F[UI]
      F --> G[Network]
      G --> A`
  },

  // File format relationships
  fileFormats: {
    title: "Binary File Formats",
    description: "Custom binary formats and their consumers",
    mermaid: `graph TD
      A[.xp Sprite] --> B[sprite.cpp]
      C[.a3d World] --> D[world.cpp]
      C --> E[terrain.cpp]
      F[.akm Mesh] --> D
      G[Blender] --> F
      G --> H[io_asciicker]
      H --> F`
  },

  // Python pipeline
  pythonPipeline: {
    title: "Python Asset Pipeline",
    description: "AI-assisted asset generation flow",
    mermaid: `graph LR
      A[Prompt/Image] --> B[AI Gen]
      B --> C[PNG]
      C --> D[Quantize]
      D --> E[CP437 Match]
      E --> F[.xp Output]`
  }
};

// ============================================================================
// RENDER AND OUTPUT
// ============================================================================

let output = `# Asciicker Architecture Diagrams

ASCII art architecture diagrams generated from codebase documentation.
Generated with beautiful-mermaid.

---

`;

for (const [key, diagram] of Object.entries(diagrams)) {
  console.log(`Rendering: ${diagram.title}...`);

  try {
    const ascii = renderMermaidAscii(diagram.mermaid, {
      useAscii: false,  // Use Unicode box-drawing
      paddingX: 3,
      paddingY: 2,
      boxBorderPadding: 1
    });

    output += `## ${diagram.title}

${diagram.description}

\`\`\`
${ascii}
\`\`\`

---

`;
  } catch (err) {
    console.error(`Failed to render ${diagram.title}:`, err.message);
    output += `## ${diagram.title}

${diagram.description}

*Diagram rendering failed: ${err.message}*

---

`;
  }
}

output += `
## See Also

- \`docs/agent/claude.md\` — Claude-specific operating rules
- \`docs/agent/agents.md\` — Full agent protocol
- \`docs/plans/2026-03-22-multiplayer-canonical-spec.md\` — Current multiplayer contract/runbook

---
*Generated: ${new Date().toISOString().split('T')[0]}*
`;

writeFileSync('docs/ARCHITECTURE_DIAGRAMS.md', output);
console.log('\nWritten to: docs/ARCHITECTURE_DIAGRAMS.md');
