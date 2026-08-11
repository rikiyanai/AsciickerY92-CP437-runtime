#pragma once

#include "render.h"

struct HPBar
{
	static const int height = 4;

	void Paint(AnsiCell* ptr, int width, int height, float val, int xyw[3], bool flip) const
	{
		int pos[2] = { xyw[0], xyw[1] };
		int size = xyw[2];

		int dx = 1;
		uint8_t left = 221;
		uint8_t right = 222;
		uint8_t lt = lt_red;
		uint8_t dk = dk_red;
		uint8_t ul = yellow;
		uint8_t left_line = 218;
		uint8_t right_line = 191;

		if (flip)
		{
			dx = -1;
			pos[0] += size - 1;
			left = 222;
			right = 221;
			lt = lt_blue;
			dk = dk_blue;
			ul = lt_cyan;
			left_line = 191;
			right_line = 218;
		}

		AnsiCell* row[4]=
		{
			pos[1] >= 0 && pos[1] < height ? ptr + pos[1]*width : 0,
			pos[1] + 1 >= 0 && pos[1] + 1 < height ? ptr + (pos[1] + 1) * width : 0,
			pos[1] + 2 >= 0 && pos[1] + 2 < height ? ptr + (pos[1] + 2) * width : 0,
			pos[1] + 3 >= 0 && pos[1] + 3 < height ? ptr + (pos[1] + 3) * width : 0,
		};

		int cols[] = { 1,1,4,1,1,1,1 };

		int dw = (size < 10 ? 10 : size) - 10;
		int d = dw / 4;
		dw -= 4 * d;

		cols[2] += d;
		cols[3] += d;
		cols[4] += d;
		cols[5] += d;

		for (int c = 5; dw > 0; c--)
		{
			dw--;
			cols[c]++;
		}

		int x_thresh = pos[0] + dx * (1 + (int)(val * (size - 2) + 0.5f));
		int perc = (int)(val * 100 + 0.5f);
		char str[]="           xxxx"; // shut wrngz up

		if (perc<100)
			sprintf(str+1,"%d%%",perc);
		else
			sprintf(str,"%d%%",perc);

		int str_len = 4;

		if (flip)
		{
			for (int i = 0; i < str_len / 2; i++)
			{
				char swp = str[i];
				str[i] = str[str_len - 1 - i];
				str[str_len - 1 - i] = swp;
			}
		}

		// bottom
		if (row[0])
		{
			int x = pos[0] + dx; // cols[1]
			if (x >= 0 && x < width)
			{
				AnsiCell* ac = row[0] + x;
				ac->bk = AverageGlyph(ac, 0x3);
				ac->fg = black;
				ac->gl = 223;
			}
			x += dx;

			for (int i = 0; i < cols[2]; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[0] + x;
					ac->bk = x*dx < x_thresh*dx ? dk : dk_grey;
					ac->fg = black;
					ac->gl = 220;
				}
				x += dx;
			}

			for (int i = 0; i < cols[3]; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[0] + x;
					ac->bk = AverageGlyph(ac, 0x3);
					ac->fg = black;
					ac->gl = 223;
				}
				x += dx;
			}
		}

		if (row[1])
		{
			int x = pos[0];
			if (x >= 0 && x < width)
			{
				AnsiCell* ac = row[1] + x;
				ac->bk = AverageGlyph(ac, 0x5);
				ac->fg = black;
				ac->gl = right;
			}
			x += dx;

			if (x >= 0 && x < width)
			{
				AnsiCell* ac = row[1] + x;
				if (x*dx < x_thresh*dx)
				{
					ac->bk = dk;
					ac->fg = black;
					ac->gl = 32;
				}
				else
				{
					ac->bk = dk_grey;
					ac->fg = black;
					ac->gl = 177;
				}
			}
			x += dx;

			for (int i = 0; i < cols[2]; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[1] + x;
					if (x*dx < x_thresh*dx)
					{
						ac->bk = dk;
						ac->fg = white;
						ac->gl = i<str_len ? str[i] : 32;
					}
					else
					{
						if (i == 0)
						{
							if (perc >= 100)
							{
								ac->bk = dk_grey;
								ac->fg = white;
								ac->gl = str[i];
							}
							else
							{
								ac->bk = dk_grey;
								ac->fg = black;
								ac->gl = 176;
							}
						}
						else
						{
							ac->bk = dk_grey;
							ac->fg = white;
							ac->gl = i < str_len ? str[i] : 32;
						}
					}
				}
				x += dx;
			}

			for (int i = 0; i < cols[3]; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[1] + x;
					ac->bk = (x*dx < x_thresh*dx) ? dk : dk_grey;
					ac->fg = white;
					ac->gl = 32;
				}
				x += dx;
			}

			for (int i = 0; i < cols[4]; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[1] + x;
					ac->bk = (x*dx < x_thresh*dx) ? dk : dk_grey;
					ac->fg = black;
					ac->gl = 220;
				}
				x += dx;
			}

			for (int i = 0; i < cols[5]; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[1] + x;
					ac->bk = AverageGlyph(ac,0x3);
					ac->fg = black;
					ac->gl = 223;
				}
				x += dx;
			}
		}

		if (row[2])
		{
			int x = pos[0];
			if (x >= 0 && x < width)
			{
				AnsiCell* ac = row[2] + x;
				ac->bk = AverageGlyph(ac, 0x5);
				ac->fg = black;
				ac->gl = right;
			}
			x += dx;

			if (x >= 0 && x < width)
			{
				AnsiCell* ac = row[2] + x;
				if (x*dx < x_thresh*dx)
				{
					ac->bk = dk;
					if ((x + dx)*dx < x_thresh*dx)
					{
						ac->fg = flip ? lt : ul;
						ac->gl = left_line;
					}
					else
					{
						ac->fg = lt;
						ac->gl = 44;
					}
				}
				else
				{
					ac->bk = dk_grey;
					ac->fg = black;
					ac->gl = 178;
				}
			}
			x += dx;

			if (x >= 0 && x < width)
			{
				AnsiCell* ac = row[2] + x;
				if (x*dx < x_thresh*dx)
				{
					ac->bk = dk;
					if ((x + dx)*dx < x_thresh*dx)
					{
						ac->fg = lt;
						ac->gl = 196;
					}
					else
					{
						ac->fg = flip ? ul : lt;
						ac->gl = right_line;
					}
				}
				else
				{
					ac->bk = dk_grey;
					ac->fg = black;
					ac->gl = 177;
				}
			}
			x += dx;

			int j = cols[2] + cols[3] + cols[4];
			for (int i = 1; i < j; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[2] + x;
					if (x*dx < x_thresh*dx)
					{
						ac->bk = dk;
						if ((x + dx)*dx < x_thresh*dx)
						{
							ac->fg = lt;
							ac->gl = 196;
						}
						else
						{
							ac->fg = flip ? ul : lt;
							ac->gl = right_line;
						}
					}
					else
					{
						ac->bk = dk_grey;
						ac->fg = black;
						ac->gl = 176;
					}
				}
				x += dx;
			}

			for (int i = 0; i < cols[5]; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[2] + x;
					if (x*dx < x_thresh*dx)
					{
						ac->bk = dk;
						if ((x + dx)*dx < x_thresh*dx)
						{
							ac->fg = lt;
							ac->gl = 196;
						}
						else
						{
							ac->fg = flip ? ul : lt;
							ac->gl = right_line;
						}
					}
					else
					{
						ac->bk = dk_grey;
						ac->fg = black;
						if (i < cols[5] - 2)
							ac->gl = 176;
						else
						if (i < cols[5] - 1)
							ac->gl = 177;
						else
							ac->gl = 178;
					}
				}
				x += dx;
			}

			if (x >= 0 && x < width)
			{
				AnsiCell* ac = row[2] + x;
				ac->bk = AverageGlyph(ac, 0xA);
				ac->fg = black;
				ac->gl = left;
			}
			x += dx;
		}

		if (row[3])
		{
			int x = pos[0] + dx;
			int j = cols[1] + cols[2] + cols[3] + cols[4] + cols[5];
			for (int i = 0; i < j; i++)
			{
				if (x >= 0 && x < width)
				{
					AnsiCell* ac = row[3] + x;
					ac->bk = AverageGlyph(ac, 0xC);
					ac->fg = black;
					ac->gl = 220;
				}
				x += dx;
			}
		}
	}
};

