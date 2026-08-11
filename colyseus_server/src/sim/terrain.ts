import fs from "node:fs";
import path from "node:path";

export interface TerrainAuthority {
  readonly source: string;
  sampleHeight(x: number, z: number): number;
  hasHeight?(x: number, z: number): boolean;
}

export interface LineOfSightOptions {
  eyeHeight?: number;
  targetHeight?: number;
  sampleSpacing?: number;
  terrainClearance?: number;
}

const VISUAL_CELLS = 8;
const HEIGHT_CELLS = 4;
const HEIGHT_SCALE = 16.0;
const PATCH_BYTES = 188;
const MATERIAL_BLOCK_BYTES = 256 * 512;
const HEADER_BYTES = 16;
const A3D_MAGIC = "AS3D";

interface A3DPatch {
  x: number;
  y: number;
  heights: number[];
}

export const FLAT_TERRAIN: TerrainAuthority = {
  source: "flat",
  sampleHeight: () => 0,
  hasHeight: () => true,
};

export const DEFAULT_LOS_EYE_HEIGHT = 1.6;
export const DEFAULT_LOS_TARGET_HEIGHT = 0.8;
export const DEFAULT_LOS_SAMPLE_SPACING = 0.5;
export const DEFAULT_LOS_TERRAIN_CLEARANCE = 0.05;

export function loadDefaultTerrain(): TerrainAuthority {
  const explicitPath = process.env.ASCIICKER_COLYSEUS_A3D_MAP;
  const defaultPath = path.resolve(process.cwd(), "../godot_project/assets/maps/game_map_y8.a3d");
  const sourcePath = explicitPath && explicitPath !== "" ? explicitPath : defaultPath;
  if (!fs.existsSync(sourcePath)) {
    if (allowExplicitFlatTerrain()) {
      return FLAT_TERRAIN;
    }
    throw new Error(`A3D terrain missing: ${sourcePath}`);
  }
  const terrain = loadA3DTerrain(sourcePath);
  if (terrain.source === FLAT_TERRAIN.source && !allowExplicitFlatTerrain()) {
    throw new Error(`A3D terrain empty: ${sourcePath}`);
  }
  return terrain;
}

function allowExplicitFlatTerrain(): boolean {
  return process.env.ASCIICKER_COLYSEUS_ALLOW_FLAT_TERRAIN === "1";
}

function maybeFlatTerrain(sourcePath: string): TerrainAuthority {
  if (allowExplicitFlatTerrain()) {
    return FLAT_TERRAIN;
  }
  throw new Error(`A3D terrain empty: ${sourcePath}`);
}

export function loadA3DTerrain(sourcePath: string): TerrainAuthority {
  const data = fs.readFileSync(sourcePath);
  if (data.length < HEADER_BYTES + MATERIAL_BLOCK_BYTES) {
    throw new Error(`A3D terrain truncated: ${sourcePath}`);
  }
  if (data.subarray(0, 4).toString("ascii") !== A3D_MAGIC) {
    throw new Error(`A3D terrain bad magic: ${sourcePath}`);
  }
  const headerSize = data.readUInt32LE(4);
  if (headerSize !== HEADER_BYTES) {
    throw new Error(`A3D terrain header size mismatch: ${headerSize}`);
  }
  const patchCount = data.readUInt32LE(8);
  const requiredBytes = HEADER_BYTES + patchCount * PATCH_BYTES + MATERIAL_BLOCK_BYTES;
  if (data.length < requiredBytes) {
    throw new Error(`A3D terrain patch block truncated: ${sourcePath}`);
  }

  const patches: A3DPatch[] = [];
  const byCoord = new Map<string, A3DPatch>();
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  let offset = HEADER_BYTES;
  for (let i = 0; i < patchCount; i += 1) {
    const patchX = data.readInt32LE(offset);
    const patchY = data.readInt32LE(offset + 4);
    const heights: number[] = [];
    for (let h = 0; h < (HEIGHT_CELLS + 1) * (HEIGHT_CELLS + 1); h += 1) {
      heights.push(data.readUInt16LE(offset + 136 + h * 2));
    }
    const patch = { x: patchX, y: patchY, heights };
    patches.push(patch);
    byCoord.set(coordKey(patchX, patchY), patch);
    minX = Math.min(minX, patchX);
    minY = Math.min(minY, patchY);
    maxX = Math.max(maxX, patchX);
    maxY = Math.max(maxY, patchY);
    offset += PATCH_BYTES;
  }

  if (patches.length === 0) {
    return maybeFlatTerrain(sourcePath);
  }
  const centerOffsetX = (minX + ((maxX - minX + 1) / 2.0)) * VISUAL_CELLS;
  const centerOffsetY = (minY + ((maxY - minY + 1) / 2.0)) * VISUAL_CELLS;

  return {
    source: sourcePath,
    hasHeight(x: number, z: number): boolean {
      return findPatch(x, z) !== undefined;
    },
    sampleHeight(x: number, z: number): number {
      const patchSample = findPatch(x, z);
      const patch = patchSample?.patch;
      if (!patch) {
        return 0;
      }
      return heightAt(patch, patchSample.localX, patchSample.localY);
    },
  };

  function findPatch(x: number, z: number): { patch: A3DPatch; localX: number; localY: number } | undefined {
    const sourceX = x + centerOffsetX;
    const sourceY = z + centerOffsetY;
    const patchX = Math.floor(sourceX / VISUAL_CELLS);
    const patchY = Math.floor(sourceY / VISUAL_CELLS);
    const patch = byCoord.get(coordKey(patchX, patchY));
    if (!patch) {
      return undefined;
    }
    return {
      patch,
      localX: sourceX - patch.x * VISUAL_CELLS,
      localY: sourceY - patch.y * VISUAL_CELLS,
    };
  }
}

export function hasTerrainLineOfSight(
  terrain: TerrainAuthority,
  viewer: { x: number; y: number; z: number },
  target: { x: number; y: number; z: number },
  options: LineOfSightOptions = {},
): boolean {
  const eyeHeight = options.eyeHeight ?? DEFAULT_LOS_EYE_HEIGHT;
  const targetHeight = options.targetHeight ?? DEFAULT_LOS_TARGET_HEIGHT;
  const sampleSpacing = options.sampleSpacing ?? DEFAULT_LOS_SAMPLE_SPACING;
  const terrainClearance = options.terrainClearance ?? DEFAULT_LOS_TERRAIN_CLEARANCE;
  if (sampleSpacing <= 0 || !Number.isFinite(sampleSpacing)) {
    return false;
  }
  if (!isKnownTerrainPoint(terrain, viewer.x, viewer.z) || !isKnownTerrainPoint(terrain, target.x, target.z)) {
    return false;
  }

  const dx = target.x - viewer.x;
  const dz = target.z - viewer.z;
  const distance = Math.hypot(dx, dz);
  if (distance === 0) {
    return true;
  }

  const startY = viewer.y + eyeHeight;
  const endY = target.y + targetHeight;
  const sampleCount = Math.max(1, Math.ceil(distance / sampleSpacing));
  for (let sample = 1; sample < sampleCount; sample += 1) {
    const t = sample / sampleCount;
    const x = viewer.x + dx * t;
    const z = viewer.z + dz * t;
    const terrainY = knownTerrainHeight(terrain, x, z);
    if (terrainY === null) {
      return false;
    }
    const rayY = lerp(startY, endY, t);
    if (terrainY > rayY - terrainClearance) {
      return false;
    }
  }
  return true;
}

function isKnownTerrainPoint(terrain: TerrainAuthority, x: number, z: number): boolean {
  return knownTerrainHeight(terrain, x, z) !== null;
}

function knownTerrainHeight(terrain: TerrainAuthority, x: number, z: number): number | null {
  if (terrain.hasHeight && !terrain.hasHeight(x, z)) {
    return null;
  }
  try {
    const height = terrain.sampleHeight(x, z);
    return Number.isFinite(height) ? height : null;
  } catch {
    return null;
  }
}

function heightAt(patch: A3DPatch, vx: number, vy: number): number {
  const hx = vx / 2.0;
  const hy = vy / 2.0;
  const x0 = Math.floor(hx);
  const y0 = Math.floor(hy);
  const x1 = Math.min(x0 + 1, HEIGHT_CELLS);
  const y1 = Math.min(y0 + 1, HEIGHT_CELLS);
  const tx = hx - x0;
  const ty = hy - y0;
  const h00 = patch.heights[y0 * (HEIGHT_CELLS + 1) + x0];
  const h10 = patch.heights[y0 * (HEIGHT_CELLS + 1) + x1];
  const h01 = patch.heights[y1 * (HEIGHT_CELLS + 1) + x0];
  const h11 = patch.heights[y1 * (HEIGHT_CELLS + 1) + x1];
  return lerp(lerp(h00, h10, tx), lerp(h01, h11, tx), ty) / HEIGHT_SCALE;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function coordKey(x: number, y: number): string {
  return `${x},${y}`;
}
