#pragma once

// =============================================================================
// Matrix & Vector Math Utilities — Geometry Kernel for 3D Pipeline
// =============================================================================
//
// PURPOSE:
// Provides templated linear-algebra primitives for the 3D engine: 4x4 matrix
// inversion, matrix-matrix and matrix-vector multiplication, axis-angle
// rotation, ray-triangle intersection (Moller-Trumbore), sphere-triangle
// collision, and basic vector operations (cross, dot, plane extraction).
//
// All matrices are stored in COLUMN-MAJOR ORDER as flat 16-element arrays,
// matching OpenGL convention: m[col*4 + row]. This means m[12..14] holds
// the translation component, and columns are contiguous in memory.
//
// KEY TYPES / FUNCTIONS:
// - Invert()                  : 4x4 matrix inverse via cofactor expansion
// - MatProduct()              : 4x4 * 4x4 matrix multiplication
// - Product() (mat*vec)       : 4x4 matrix * 4-vector (homogeneous transform)
// - TransposeProduct()        : transpose(4x4) * 4-vector (inverse-rotate)
// - Product() (dot)           : 4-element dot product
// - Rotation()                : Rodrigues axis-angle to 4x4 rotation matrix
// - CrossProduct(), DotProduct(): Standard 3D vector operations
// - RayIntersectsTriangle()   : Moller-Trumbore ray-triangle intersection
// - PlaneFromPoints()         : Plane equation from 3 vertices
// - SphereIntersectTriangle() : Sphere vs triangle collision test
//
// CONSUMERS:
// - render.cpp   : view/projection transforms, unproject (screen -> world)
// - terrain.cpp  : height sampling, normal computation, terrain patch transforms
// - physics.cpp  : collision detection (sphere-triangle, ray casting)
// - world.cpp    : object placement, bounding-box transforms
// - game.cpp     : camera matrix construction
// - game_app.cpp : input-to-world coordinate mapping
// - asciiid.cpp  : editor camera, picking rays
// - term.cpp     : coordinate transforms for terminal rendering
//
// MEMORY LAYOUT:
// Column-major 4x4 (OpenGL convention):
//   [ m[0]  m[4]  m[8]   m[12] ]     [ Xx  Yx  Zx  Tx ]
//   [ m[1]  m[5]  m[9]   m[13] ]  =  [ Xy  Yy  Zy  Ty ]
//   [ m[2]  m[6]  m[10]  m[14] ]     [ Xz  Yz  Zz  Tz ]
//   [ m[3]  m[7]  m[11]  m[15] ]     [ 0   0   0   1  ]
// =============================================================================

// WHY cofactor expansion: Computes the full 4x4 inverse using the classical
// adjugate method (matrix of cofactors / determinant). This is the most
// general approach -- works for any invertible matrix, not just orthonormal
// rotation matrices where transpose would suffice. Used by the render pipeline
// to invert view-projection for unprojection (screen coords -> world ray).
// Returns false if the matrix is singular (det == 0).
// [FLOW:RENDER] render.cpp calls this to invert the view-projection matrix
// [FLOW:PHYSICS] physics.cpp uses inverse transforms for collision response
template <typename M>
bool Invert(const M m[16], M invOut[16])
{
	M inv[16], det;
	int i;

	inv[0] = m[5] * m[10] * m[15] -
		m[5] * m[11] * m[14] -
		m[9] * m[6] * m[15] +
		m[9] * m[7] * m[14] +
		m[13] * m[6] * m[11] -
		m[13] * m[7] * m[10];

	inv[4] = -m[4] * m[10] * m[15] +
		m[4] * m[11] * m[14] +
		m[8] * m[6] * m[15] -
		m[8] * m[7] * m[14] -
		m[12] * m[6] * m[11] +
		m[12] * m[7] * m[10];

	inv[8] = m[4] * m[9] * m[15] -
		m[4] * m[11] * m[13] -
		m[8] * m[5] * m[15] +
		m[8] * m[7] * m[13] +
		m[12] * m[5] * m[11] -
		m[12] * m[7] * m[9];

	inv[12] = -m[4] * m[9] * m[14] +
		m[4] * m[10] * m[13] +
		m[8] * m[5] * m[14] -
		m[8] * m[6] * m[13] -
		m[12] * m[5] * m[10] +
		m[12] * m[6] * m[9];

	inv[1] = -m[1] * m[10] * m[15] +
		m[1] * m[11] * m[14] +
		m[9] * m[2] * m[15] -
		m[9] * m[3] * m[14] -
		m[13] * m[2] * m[11] +
		m[13] * m[3] * m[10];

	inv[5] = m[0] * m[10] * m[15] -
		m[0] * m[11] * m[14] -
		m[8] * m[2] * m[15] +
		m[8] * m[3] * m[14] +
		m[12] * m[2] * m[11] -
		m[12] * m[3] * m[10];

	inv[9] = -m[0] * m[9] * m[15] +
		m[0] * m[11] * m[13] +
		m[8] * m[1] * m[15] -
		m[8] * m[3] * m[13] -
		m[12] * m[1] * m[11] +
		m[12] * m[3] * m[9];

	inv[13] = m[0] * m[9] * m[14] -
		m[0] * m[10] * m[13] -
		m[8] * m[1] * m[14] +
		m[8] * m[2] * m[13] +
		m[12] * m[1] * m[10] -
		m[12] * m[2] * m[9];

	inv[2] = m[1] * m[6] * m[15] -
		m[1] * m[7] * m[14] -
		m[5] * m[2] * m[15] +
		m[5] * m[3] * m[14] +
		m[13] * m[2] * m[7] -
		m[13] * m[3] * m[6];

	inv[6] = -m[0] * m[6] * m[15] +
		m[0] * m[7] * m[14] +
		m[4] * m[2] * m[15] -
		m[4] * m[3] * m[14] -
		m[12] * m[2] * m[7] +
		m[12] * m[3] * m[6];

	inv[10] = m[0] * m[5] * m[15] -
		m[0] * m[7] * m[13] -
		m[4] * m[1] * m[15] +
		m[4] * m[3] * m[13] +
		m[12] * m[1] * m[7] -
		m[12] * m[3] * m[5];

	inv[14] = -m[0] * m[5] * m[14] +
		m[0] * m[6] * m[13] +
		m[4] * m[1] * m[14] -
		m[4] * m[2] * m[13] -
		m[12] * m[1] * m[6] +
		m[12] * m[2] * m[5];

	inv[3] = -m[1] * m[6] * m[11] +
		m[1] * m[7] * m[10] +
		m[5] * m[2] * m[11] -
		m[5] * m[3] * m[10] -
		m[9] * m[2] * m[7] +
		m[9] * m[3] * m[6];

	inv[7] = m[0] * m[6] * m[11] -
		m[0] * m[7] * m[10] -
		m[4] * m[2] * m[11] +
		m[4] * m[3] * m[10] +
		m[8] * m[2] * m[7] -
		m[8] * m[3] * m[6];

	inv[11] = -m[0] * m[5] * m[11] +
		m[0] * m[7] * m[9] +
		m[4] * m[1] * m[11] -
		m[4] * m[3] * m[9] -
		m[8] * m[1] * m[7] +
		m[8] * m[3] * m[5];

	inv[15] = m[0] * m[5] * m[10] -
		m[0] * m[6] * m[9] -
		m[4] * m[1] * m[10] +
		m[4] * m[2] * m[9] +
		m[8] * m[1] * m[6] -
		m[8] * m[2] * m[5];

	// WHY this specific expansion: det is computed by dotting the first ROW
	// of the original matrix with the first COLUMN of the cofactor matrix.
	// This reuses inv[0,4,8,12] which were already computed above, avoiding
	// a separate determinant calculation.
	det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];

	if (det == 0)
		return false;

	// WHY 1/det multiply: dividing each cofactor by det is equivalent to
	// multiplying by the scalar inverse -- one division + 16 multiplies
	// is cheaper than 16 divisions.
	det = 1.0 / det;

	for (i = 0; i < 16; i++)
		invOut[i] = inv[i] * det;

	return true;
}

// 4x4 * 4x4 matrix multiplication (column-major).
// WHY manual unroll: the compiler can vectorize these 4-wide dot products
// into SIMD, and avoiding a loop makes the column-major access pattern explicit.
// Each output column ab[col*4..col*4+3] = a * b_column.
// [FLOW:RENDER] chaining model -> view -> projection transforms
template <typename M>
void MatProduct(const M a[16], const M b[4], M ab[16])
{
	ab[0] = a[0] * b[0] + a[4] * b[1] + a[8] * b[2] + a[12] * b[3];
	ab[1] = a[1] * b[0] + a[5] * b[1] + a[9] * b[2] + a[13] * b[3];
	ab[2] = a[2] * b[0] + a[6] * b[1] + a[10] * b[2] + a[14] * b[3];
	ab[3] = a[3] * b[0] + a[7] * b[1] + a[11] * b[2] + a[15] * b[3];

	ab[4] = a[0] * b[4] + a[4] * b[5] + a[8] * b[6] + a[12] * b[7];
	ab[5] = a[1] * b[4] + a[5] * b[5] + a[9] * b[6] + a[13] * b[7];
	ab[6] = a[2] * b[4] + a[6] * b[5] + a[10] * b[6] + a[14] * b[7];
	ab[7] = a[3] * b[4] + a[7] * b[5] + a[11] * b[6] + a[15] * b[7];

	ab[8] = a[0] * b[8] + a[4] * b[9] + a[8] * b[10] + a[12] * b[11];
	ab[9] = a[1] * b[8] + a[5] * b[9] + a[9] * b[10] + a[13] * b[11];
	ab[10] = a[2] * b[8] + a[6] * b[9] + a[10] * b[10] + a[14] * b[11];
	ab[11] = a[3] * b[8] + a[7] * b[9] + a[11] * b[10] + a[15] * b[11];

	ab[12] = a[0] * b[12] + a[4] * b[13] + a[8] * b[14] + a[12] * b[15];
	ab[13] = a[1] * b[12] + a[5] * b[13] + a[9] * b[14] + a[13] * b[15];
	ab[14] = a[2] * b[12] + a[6] * b[13] + a[10] * b[14] + a[14] * b[15];
	ab[15] = a[3] * b[12] + a[7] * b[13] + a[11] * b[14] + a[15] * b[15];
}

// 4x4 matrix * 4-vector product (column-major).
// WHY three template params: M (matrix element), V (input vector), MV (output)
// allows mixed-precision transforms, e.g., double matrix * float vertex -> float result.
// The explicit (MV) cast on each component prevents implicit narrowing warnings.
// [FLOW:RENDER] transforms world-space vertices into clip-space
template <typename M, typename V, typename MV>
void Product(const M m[16], const V v[4], MV mv[4])
{
	mv[0] = (MV)(m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12] * v[3]);
	mv[1] = (MV)(m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13] * v[3]);
	mv[2] = (MV)(m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14] * v[3]);
	mv[3] = (MV)(m[3] * v[0] + m[7] * v[1] + m[11] * v[2] + m[15] * v[3]);
}

// Transpose(4x4) * 4-vector: multiplies by the transpose without actually
// transposing the matrix in memory.
// WHY: for orthonormal rotation matrices, transpose == inverse. This avoids
// the cost of Invert() when only undoing a rotation (no scale/shear).
// Used in the render pipeline for inverse-rotating normals and directions.
// [FLOW:RENDER] inverse-rotate directions without full matrix inversion
template <typename M, typename V, typename MV>
void TransposeProduct(const M m[16], const V v[4], MV mv[4])
{
	mv[0] = (MV)(m[0] * v[0] + m[1] * v[1] + m[2] * v[2] + m[3] * v[3]);
	mv[1] = (MV)(m[4] * v[0] + m[5] * v[1] + m[6] * v[2] + m[7] * v[3]);
	mv[2] = (MV)(m[8] * v[0] + m[9] * v[1] + m[10] * v[2] + m[11] * v[3]);
	mv[3] = (MV)(m[12] * v[0] + m[13] * v[1] + m[14] * v[2] + m[15] * v[3]);
}

// 4-element dot product. Returns auto-deduced type from L*R arithmetic.
// WHY 4-element (not 3): operates on homogeneous coordinates so it can be
// used for plane-distance tests where the 4th component (w) matters.
template <typename L, typename R>
auto Product(const L l[4], const R r[4])
{
	return l[0] * r[0] + l[1] * r[1] + l[2] * r[2] + l[3] * r[3];
}

// Returns 1 if dot(l, r) > 0, else 0. Used for frustum culling half-space
// tests: vertex is on the positive side of a plane iff dot(plane, vertex) > 0.
// [FLOW:RENDER] frustum plane culling in the terrain/world render stages
template <typename L, typename R>
inline int PositiveProduct(L l[4], R r[4])
{
	return Product(l, r) > 0 ? 1 : 0;
}

// Axis-angle to 4x4 rotation matrix (Rodrigues' rotation formula).
// WHY Rodrigues: converts an arbitrary axis v[3] and angle a (radians) into
// a rotation matrix without going through quaternions or Euler angles.
// The formula: R = cos(a)*I + sin(a)*[v]x + (1-cos(a))*(v (x) v)
// where [v]x is the skew-symmetric cross-product matrix, (x) is outer product.
// Assumes v is UNIT LENGTH -- caller must normalize before calling.
// TODO(PIPELINE-FIX): no normalization check on v; a non-unit axis silently
// produces a non-orthonormal matrix, causing scale drift in chained transforms.
// [FLOW:RENDER] camera orbit rotation around terrain focus point
// [FLOW:PHYSICS] rotating collision geometry
template <typename V, typename A, typename M>
inline void Rotation(const V v[3], A a, M m[16])
{
	M c = (M)cos(a);
	M s = (M)sin(a);
	// WHY d = 1 - cos(a): this is the (1-cos) term in Rodrigues' formula,
	// factored out to avoid recomputing it for each matrix element.
	M d = 1 - c;

	m[0] = v[0] * v[0] * d + c;
	m[1] = v[1] * v[0] * d + v[2] * s;
	m[2] = v[2] * v[0] * d - v[1] * s;
	m[3] = 0;

	m[4] = v[0] * v[1] * d - v[2] * s;
	m[5] = v[1] * v[1] * d + c;
	m[6] = v[2] * v[1] * d + v[0] * s;
	m[7] = 0;

	m[8] = v[0] * v[2] * d + v[1] * s;
	m[9] = v[1] * v[2] * d - v[0] * s;
	m[10] = v[2] * v[2] * d + c;
	m[11] = 0;

	m[12] = 0;
	m[13] = 0;
	m[14] = 0;
	m[15] = 1;
}

// Generates a uniformly distributed random rotation matrix from 3 uniform
// random values r[0..2] in [0,1] (Arvo's method from "Fast Random Rotation
// Matrices", Graphics Gems III).
// WHY this method: standard Euler-angle randomization clusters near poles;
// Arvo's method produces truly uniform coverage of SO(3) from exactly 3
// random inputs by composing a random XY-plane rotation with a Householder
// reflection.
// NOTE: body is empty -- the algorithm comments describe the intended
// implementation but it was never filled in.
// TODO(PIPELINE-FIX): stub function -- no implementation. Callers relying
// on this will get an uninitialized output matrix m[16].
template <typename R, typename M>
inline void RandRot(R r[3], M m[16])
{
	// r must hold 3 random vals in range 0..1

	// M = -H R
	// H = I - 2vvT
	// R = random xy rot by 2Pi*r[0]
	// v = {cos(2Pi*x[1])*sqrt(x[2]), sin(2Pi*x[1])*sqrt(x[2]), sqrt(1-x[2])}
}

// Standard 3D cross product: ab = a x b.
// WHY separate from Product/DotProduct: cross product is only defined in 3D,
// returns a vector (not scalar), and is used for normals and plane equations.
template <typename V>
inline void CrossProduct(const V a[3], const V b[3], V ab[3])
{
	ab[0] = a[1] * b[2] - a[2] * b[1];
	ab[1] = a[2] * b[0] - a[0] * b[2];
	ab[2] = a[0] * b[1] - a[1] * b[0];
}

// Standard 3D dot product (3-element, unlike the 4-element Product() above).
template <typename V>
inline V DotProduct(const V a[3], const V b[3])
{
	return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

// Moller-Trumbore ray-triangle intersection test.
// WHY Moller-Trumbore: fastest known single-ray/single-triangle test -- computes
// barycentric coordinates (u, v) directly without precomputing the triangle's
// plane, requiring only 1 division, 2 cross products, and 3 dot products.
//
// RAY LAYOUT (ray[10]):
//   ray[0..2] : unused (reserved / padding)
//   ray[3..5] : ray direction vector (need not be normalized)
//   ray[6..8] : ray origin point
//   ray[9]    : current closest hit distance (acts as max-t clamp)
// WHY ray[10] layout: the padding at [0..2] and the packed origin at [6..8]
// allow the same array to be reused as a homogeneous point or plane in other
// parts of the pipeline. The t-clamp at [9] is read-write: on hit, it is
// updated to the new closer t, enabling progressive nearest-hit accumulation
// across multiple triangles without an external variable.
// TODO(PIPELINE-FIX): ray[0..2] is unused padding -- the non-obvious 10-element
// layout is a footgun for callers. Consider a named struct or at least a
// comment convention at every call site.
//
// OUTPUTS:
//   ret[3]   : hit point in world space (valid only if returns true)
//   out_u/v  : barycentric coordinates (optional, for texture/color interpolation)
//
// [FLOW:RENDER] picking: asciiid.cpp fires a ray from camera through mouse pixel
// [FLOW:PHYSICS] collision: physics.cpp tests movement rays against terrain mesh
inline bool RayIntersectsTriangle(double ray[10], double v0[3], double v1[3], double v2[3], double ret[3], bool positive_only = false, double* out_u = 0, double* out_v = 0)
{
	const double EPSILON = 0.0000001;

	double edge1[3] = { v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2] };
	double edge2[3] = { v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2] };

	double h[3] = // rayVector.crossProduct(edge2);
	{
		ray[4] * edge2[2] - ray[5] * edge2[1],
		ray[5] * edge2[0] - ray[3] * edge2[2],
		ray[3] * edge2[1] - ray[4] * edge2[0]
	};
	
	double a = // edge1.dotProduct(h);
		edge1[0] * h[0] + edge1[1] * h[1] + edge1[2] * h[2];

	if (a > -EPSILON && a < EPSILON)
		return false;    // This ray is parallel to this vangle.

	double f = 1.0 / a;
	double s[3] = // rayOrigin - vertex0;
	{
		ray[6] - v0[0],
		ray[7] - v0[1],
		ray[8] - v0[2],
	};

	double u = // f * (s.dotProduct(h));
		f * (s[0] * h[0] + s[1] * h[1] + s[2] * h[2]);

	if (u < 0.0 || u > 1.0)
		return false;

	double q[3] = //s.crossProduct(edge1);
	{
		s[1] * edge1[2] - s[2] * edge1[1],
		s[2] * edge1[0] - s[0] * edge1[2],
		s[0] * edge1[1] - s[1] * edge1[0]
	};

	double v = // f * rayVector.dotProduct(q);
		f * (ray[3] * q[0] + ray[4] * q[1] + ray[5] * q[2]);

	if (v < 0.0 || u + v > 1.0)
		return false;

	// At this stage we can compute t to find out where the intersection point is on the line.

	double t = //f * edge2.dotProduct(q);
		f * (edge2[0] * q[0] + edge2[1] * q[1] + edge2[2] * q[2]);

	if (positive_only && t < 0)
		  return false;

	if (t > ray[9])
		return false;

	// WHY write-back to ray[9]: progressive nearest-hit. Each successful
	// intersection tightens the max-t so subsequent triangle tests reject
	// anything farther away. Caller accumulates the closest hit across all
	// triangles in a single pass.
	ray[9] = t;

	ret[0] = ray[6] + ray[3] * t;
	ret[1] = ray[7] + ray[4] * t;
	ret[2] = ray[8] + ray[5] * t;
	if (out_u) *out_u = u;
	if (out_v) *out_v = v;
	return true;
}

template <typename V>
inline void PlaneFromPoints(const V a[3], const V b[3], const V c[3], V p[4])
{
	V u[3] = { b[0]-a[0], b[1]-a[1], b[2]-a[2] };
	V v[3] = { c[0]-a[0], c[1]-a[1], c[2]-a[2] };
	CrossProduct(u,v,p);
	p[3] = -DotProduct(p,a);
}

inline bool SphereIntersectTriangle(float S[4]/*center,radius*/, float v0[3], float v1[3], float v2[3])
{
	float A[] = { v0[0] - S[0], v0[1] - S[1], v0[2] - S[2] };
	float B[] = { v1[0] - S[0], v1[1] - S[1], v1[2] - S[2] };
	float C[] = { v2[0] - S[0], v2[1] - S[1], v2[2] - S[2] };
	float rr = S[3] * S[3];
	
	float AB[] = { B[0] - A[0], B[1] - A[1], B[2] - A[2] };
	float AC[] = { C[0] - A[0], C[1] - A[1], C[2] - A[2] };

	float V[3];
	CrossProduct(AB, AC, V);

	float d = DotProduct(A, V);
	float e = DotProduct(V, V);

	if (d * d > rr * e)
		return false;

	float aa = DotProduct(A, A);
	float ab = DotProduct(A, B);
	float ac = DotProduct(A, C);
	if (aa > rr && ab > aa && ac > aa)
		return false;

	float bb = DotProduct(B, B);
	float bc = DotProduct(B, C);
	if (bb > rr && ab > bb && bc > bb)
		return false;

	float cc = DotProduct(C, C);
	if (cc > rr && ac > cc && bc > cc)
		return false;

	float d1 = ab - aa;
	float d2 = bc - bb;
	float d3 = ac - cc;

	float BC[] = { C[0] - B[0], C[1] - B[1], C[2] - B[2] };
	//float CA[] = { -AC }

	float e1 = DotProduct(AB, AB);
	float e2 = DotProduct(BC, BC);
	float e3 = DotProduct(AC, AC);

	float Q1[] = { A[0] * e1 - AB[0] * d1, A[1] * e1 - AB[1] * d1, A[2] * e1 - AB[2] * d1 };
	float QC[] = { C[0] * e1 - Q1[0], C[1] * e1 - Q1[1], C[2] * e1 - Q1[2] };
	if (DotProduct(Q1, Q1) > rr * e1 * e1 && DotProduct(Q1, QC) > 0)
		return false;

	float Q2[] = { B[0] * e2 - BC[0] * d2, B[1] * e2 - BC[1] * d2, B[2] * e2 - BC[2] * d2 };
	float QA[] = { A[0] * e2 - Q2[0], A[1] * e2 - Q2[1], A[2] * e2 - Q2[2] };
	if (DotProduct(Q2, Q2) > rr * e2 * e2 && DotProduct(Q2, QA) > 0)
		return false;

	float Q3[] = { C[0] * e3 + AC[0] * d3, C[1] * e3 + AC[1] * d3, C[2] * e3 + AC[2] * d3 };
	float QB[] = { B[0] * e3 - Q3[0], B[1] * e3 - Q3[1], B[2] * e3 - Q3[2] };
	if (DotProduct(Q3, Q3) > rr * e3 * e3 && DotProduct(Q3, QB) > 0)
		return false;

	return true;
}