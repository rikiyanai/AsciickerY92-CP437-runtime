// akm_number.h — exact fast decimal-float parser for the AKM (PLY ascii) mesh loader.
//
// FL-4936 P3. Mesh::Update (engine/world_mesh.cpp) parsed every vertex
// coordinate with libc strtod. On wasm (musl __floatscan, software 128-bit long
// double) that was 3.2 s of the 3.9 s UpdateMesh loop on build_reg_64.
//
// Clinger's fast path is exact: when the decimal mantissa is at most 2^53 and
// the power of ten is at most 22, both are exactly representable doubles, so a
// single IEEE multiply or divide returns the correctly rounded result, which is
// what strtod returns. The parser reads '.' directly, so it does not depend on
// the C locale. Everything else (hex floats, inf/nan, more than 19 significant
// digits, exponents beyond +/-22, a malformed exponent, no digits) is handed to
// strtod unchanged, so the value and the end pointer match strtod for every
// input. scripts/tests/fl4936_akm_number_harness.cpp checks that bit for bit.

#ifndef AKM_NUMBER_H
#define AKM_NUMBER_H

#include <stdint.h>
#include <stdlib.h>

static inline double AkmParseDouble(const char* s, char** end)
{
	static const double kPow10[23] = {
		1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11,
		1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19, 1e20, 1e21, 1e22 };

	const char* p = s;
	bool neg = false;
	if (*p == '-' || *p == '+')
	{
		neg = (*p == '-');
		p++;
	}
	const char* first_digit = p;

	uint64_t mant = 0;
	int digits = 0;      // significant digits accumulated (leading zeros skipped)
	int any_digits = 0;  // any digit seen, including leading zeros
	int frac_digits = 0;

	while (*p >= '0' && *p <= '9')
	{
		any_digits++;
		if (mant || *p != '0')
		{
			if (++digits > 19)
				return strtod(s, end);
			mant = mant * 10 + (uint64_t)(*p - '0');
		}
		p++;
	}
	if (*p == '.')
	{
		p++;
		while (*p >= '0' && *p <= '9')
		{
			any_digits++;
			if (mant || *p != '0')
			{
				if (++digits > 19)
					return strtod(s, end);
				mant = mant * 10 + (uint64_t)(*p - '0');
			}
			frac_digits++;
			p++;
		}
	}
	if (!any_digits)
		return strtod(s, end);
	// "0x..." is a hex float to strtod.
	if (any_digits == 1 && *first_digit == '0' && (*p == 'x' || *p == 'X'))
		return strtod(s, end);

	int exp10 = 0;
	if (*p == 'e' || *p == 'E')
	{
		const char* q = p + 1;
		bool eneg = false;
		if (*q == '-' || *q == '+')
		{
			eneg = (*q == '-');
			q++;
		}
		if (!(*q >= '0' && *q <= '9'))
			return strtod(s, end);
		int ev = 0;
		while (*q >= '0' && *q <= '9')
		{
			if (ev < 10000)
				ev = ev * 10 + (*q - '0');
			q++;
		}
		exp10 = eneg ? -ev : ev;
		p = q;
	}
	exp10 -= frac_digits;

	double v;
	if (mant == 0)
		v = 0.0;
	else if (mant <= (1ull << 53) && exp10 >= -22 && exp10 <= 22)
		v = exp10 < 0 ? (double)mant / kPow10[-exp10] : (double)mant * kPow10[exp10];
	else
		return strtod(s, end);

	if (end)
		*end = (char*)p;
	return neg ? -v : v;
}

#endif // AKM_NUMBER_H
