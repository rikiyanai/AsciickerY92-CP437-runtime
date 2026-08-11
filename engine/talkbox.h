#pragma once
#include "lexer.h"
#include "render.h"
#include <stdint.h>

// note: outside 16c pal !!!!
static const uint8_t xd_bck = 16 + 1 * 1 + 1 * 6 + 1 * 36;
static const uint8_t xd_err = 16 + 1 * 1 + 1 * 6 + 5 * 36;
static const uint8_t xd_wsp = 16 + 5 * 1 + 5 * 6 + 5 * 36;
static const uint8_t xd_key = 16 + 5 * 1 + 4 * 6 + 2 * 36;
static const uint8_t xd_idn = 16 + 5 * 1 + 5 * 6 + 4 * 36;
static const uint8_t xd_opr = 16 + 5 * 1 + 3 * 6 + 5 * 36;
static const uint8_t xd_com = 16 + 3 * 1 + 3 * 6 + 3 * 36;
static const uint8_t xd_str = 16 + 3 * 1 + 4 * 6 + 5 * 36;
static const uint8_t xd_esc = 16 + 0 * 1 + 3 * 6 + 5 * 36;
static const uint8_t xd_qmk = 16 + 2 * 1 + 3 * 6 + 5 * 36;
static const uint8_t xd_rnd = 16 + 1 * 1 + 5 * 6 + 5 * 36;
static const uint8_t xd_sqr = 16 + 2 * 1 + 5 * 6 + 2 * 36;
static const uint8_t xd_crl = 16 + 5 * 1 + 5 * 6 + 1 * 36;
static const uint8_t xd_num = 16 + 4 * 1 + 5 * 6 + 1 * 36;
static const uint8_t xd_tem = 16 + 4 * 1 + 2 * 6 + 4 * 36;
static const uint8_t xd_fnc = 16 + 1 * 1 + 4 * 6 + 5 * 36;
static const uint8_t xd_arr = 16 + 3 * 1 + 5 * 6 + 3 * 36;

static const uint8_t black = 16;
static const uint8_t white =   16 + 5 * 1 + 5 * 6 + 5 * 36;
static const uint8_t lt_grey = 16 + 3 * 1 + 3 * 6 + 3 * 36;
static const uint8_t dk_grey = 16 + 2 * 1 + 2 * 6 + 2 * 36;
static const uint8_t lt_red = 16 + 1 * 1 + 1 * 6 + 5 * 36;
static const uint8_t dk_red = 16 + 0 * 1 + 0 * 6 + 3 * 36;
static const uint8_t lt_cyan = 16 + 5 * 1 + 5 * 6 + 1 * 36;
static const uint8_t dk_cyan = 16 + 3 * 1 + 3 * 6 + 0 * 36;
static const uint8_t yellow = 16 + 1 * 1 + 5 * 6 + 5 * 36;
static const uint8_t lt_blue = 16 + 5 * 1 + 1 * 6 + 1 * 36;
static const uint8_t dk_blue = 16 + 3 * 1 + 0 * 6 + 0 * 36;
static const uint8_t brown = 16 + 0 * 1 + 2 * 6 + 3 * 36;
static const uint8_t lt_green = 16 + 1 * 1 + 5 * 6 + 1 * 36;
static const uint8_t dk_green = 16 + 0 * 1 + 3 * 6 + 0 * 36;
static const uint8_t dk_magenta = 16 + 3 * 1 + 0 * 6 + 3 * 36;
static const uint8_t lt_magenta = 16 + 5 * 1 + 1 * 6 + 5 * 36;

struct TalkBox
{
	int max_width, max_height;
	int size[2];
	int cursor_xy[2];
	int cursor_pos;
	int len;

	void Paint(AnsiCell* ptr, int width, int height, int x, int y, bool cursor, const char* name=0) const
	{
		// x,y is at smoke spot, box will be centered above it

		struct Cookie
		{
			const TalkBox* box;
			AnsiCell* ptr;
			int width, height, x, y;
			int span;
			int rows;
			bool script;
			Lexer lex;

			AnsiCell* back[256];
			int back_pos;			

			static void Print(int dx, int dy, const char* str, int len, void* cookie, bool synth)
			{
				Cookie* c = (Cookie*)cookie;
				if (c->y - dy < 0 || c->y - dy >= c->height)
					return;

				AnsiCell* ar = c->ptr + c->x + c->width * (c->y - dy);

				uint8_t fg = white;
				bool script = c->script;

				static const uint8_t color[]=
				{
					xd_wsp,   // white_space,
					xd_qmk,  // string_delimiter, '' "" ``
					xd_esc,  // string_escape,
					xd_err,  // string_error, // \n inside string
					xd_str,  // string_char, 
					xd_num,  // number_char,
					xd_err,  // error_char, // \ outside of string!
					xd_opr,   // operator_char,
					xd_idn,   // identifier,
					xd_key,  // keyword
					xd_com,  // line_comment,
					xd_com,  // block_comment,		
					xd_rnd,  // parenthesis ()
					xd_sqr,  // parenthesis []
					xd_crl,  // parenthesis {}
					xd_tem,  // ${ } in a backtick string (template)
				};

				for (int i=0; i<len; i++)
				{
					if (str[i] == '\n')
					{
						for (int x = dx + i; x < c->span; x++)
						{
							if (script && !synth)
							{
								int mode = c->lex.Get(str[i]);
								fg = color[mode&0xFF];

								int bk_len = mode>>8;
								
								uint8_t back_fg = fg;
								bool all = true;
								if ((mode&0xFF)==Lexer::bracket_rnd && bk_len)
								{
									all = false;
									back_fg = xd_fnc;
								}
								if ((mode&0xFF)==Lexer::bracket_sqr && bk_len)
								{
									all = false;
									back_fg = xd_arr;
								}

								for (int bk = 0; bk<bk_len; bk++)
									if (c->back[(c->back_pos-bk)&0xFF])
										if (all || c->back[(c->back_pos-bk)&0xFF]->fg == xd_idn)
											c->back[(c->back_pos-bk)&0xFF]->fg = back_fg;
							}

							if (x + c->x < 0 || x + c->x >= c->width)
							{
								if (script && !synth)
									c->back[(++c->back_pos)&0xFF]=0;
								continue;
							}

							AnsiCell* ac = ar + x;
							ac->fg = fg;							
							ac->bk = xd_bck;
							ac->gl = ' ';
							ac->spare = 0;

							if (script && !synth)
								c->back[(++c->back_pos)&0xFF]=ac;
						}
						c->rows++;
						break;
					}

					if (script && !synth)
					{
						int mode = c->lex.Get(str[i]);
						fg = color[mode&0xFF];

						int bk_len = mode>>8;
						
						uint8_t back_fg = fg;
						bool all = true;
						if ((mode&0xFF)==Lexer::bracket_rnd && bk_len)
						{
							all = false;
							back_fg = xd_fnc;
						}
						if ((mode&0xFF)==Lexer::bracket_sqr && bk_len)
						{
							all = false;
							back_fg = xd_arr;
						}

						for (int bk = 0; bk<bk_len; bk++)
							if (c->back[(c->back_pos-bk)&0xFF])
								if (all || c->back[(c->back_pos-bk)&0xFF]->fg == xd_idn)
									c->back[(c->back_pos-bk)&0xFF]->fg = back_fg;
					}

					if (c->x + dx + i < 0 || c->x + dx + i >= c->width)
					{
						if (script && !synth)
							c->back[(++c->back_pos)&0xFF]=0;
						continue;
					}

					AnsiCell* ac = ar + i + dx;
					ac->fg = fg;
					ac->bk = xd_bck;
					ac->gl = str[i];
					ac->spare = 0;

					if (script && !synth)
						c->back[(++c->back_pos)&0xFF]=ac;
				}
			}
		};

		int w = size[0]+3;
		int left = x - w/2;
		int right = left + w -1;
		int center = left+w/2;

		int bottom = y;
		int lower = y + 1;
		int upper = y + 4 + size[1];


		int escape = 0;
		if (len>0 && buf[0]=='\\')
		{
			escape++;
			x--;
			if (len>1 && buf[1]=='\\')
				escape++;
		}

		bool script = escape == 1;
		Cookie cookie = { this, ptr, width, height, left+2, y + size[1]+2, size[0], 0, script, {/*lexer*/0}, {/*backbuf*/0}, /*backidx*/-1};
		int bl = Reflow(0, 0, Cookie::Print, &cookie);
		// assert(bl >= 0); // TODO: [Backlog Ref] assert(bl >= 0);

		AnsiCell* ll = ptr + left + lower * width;
		AnsiCell* bc = ptr + center + bottom * width;
		AnsiCell* lr = ptr + right + lower * width;
		AnsiCell* ul = ptr + left + upper * width;
		AnsiCell* ur = ptr + right + upper * width;

		if (center >= 0 && center < width)
		{
			if (bottom >= 0 && bottom < height)
			{
				bc->bk = black;
				bc->fg = lt_grey;
				bc->gl = 179;
			}

			bc += width;

			if (lower >= 0 && lower < height)
			{
				bc->bk = black;
				bc->fg = lt_grey;
				bc->gl = 194;
			}
		}

		if (lower >= 0 && lower < height)
		{
			if (left >= 0 && left < width)
			{
				ll->bk = black;
				ll->fg = lt_grey;
				ll->gl = 192;
			}

			if (right >= 0 && right < width)
			{
				lr->bk = black;
				lr->fg = lt_grey;
				lr->gl = 217;
			}
		}

		if (upper >= 0 && upper < height)
		{
			if (left >= 0 && left < width)
			{
				ul->bk = black;
				ul->fg = lt_grey;
				ul->gl = 218;
			}

			if (right >= 0 && right < width)
			{
				ur->bk = black;
				ur->fg = lt_grey;
				ur->gl = 191;
			}
		}

		if (lower >= 0 && lower < height)
		{
			AnsiCell* row = ptr + lower * width;
			for (int i = left + 1; i < right; i++)
			{
				if (i >= 0 && i < width && i != center)
				{
					row[i].bk = black;
					row[i].fg = lt_grey;
					row[i].gl = 196;
				}
			}
		}

		if (upper >= 0 && upper < height)
		{
			AnsiCell* row = ptr + upper * width;
			int i = left + 1;

			if (name)
			{
				for (int j=0; i < right; i++,j++)
				{
					if (!name[j])
						break;

					if (i >= 0 && i < width)
					{
						row[i].bk = black;
						row[i].fg = white;
						row[i].gl = name[j];
					}
				}
			}

			for (; i < right; i++)
			{
				if (i >= 0 && i < width)
				{
					row[i].bk = black;
					row[i].fg = lt_grey;
					row[i].gl = 196;
				}
			}
		}

		if (lower + 1 >= 0 && lower + 1 < height)
		{
			AnsiCell* row = ptr + (lower + 1) * width;

			for (int i = left + 2; i < right; i++)
			{
				if (i >= 0 && i < width)
				{
					row[i].bk = xd_bck;
					row[i].fg = black;
					row[i].gl = ' ';
				}
			}
		}

		for (int i = lower+1; i <= upper-1; i++)
		{
			if (i >= 0 && i < height)
			{
				AnsiCell* row = ptr + i * width;
				if (left >= 0 && left < width)
				{
					row[left].bk = black;
					row[left].fg = lt_grey;
					row[left].gl = 179;
				}

				if (left + 1 >= 0 && left + 1 < width)
				{
					row[left + 1].bk = xd_bck;
					row[left + 1].fg = black;
					row[left + 1].gl = ' ';
				}

				if (right >= 0 && right < width)
				{
					row[right].bk = black;
					row[right].fg = lt_grey;
					row[right].gl = 179;
				}
			}
		}

		if (upper - 1 >= 0 && upper - 1 < height)
		{
			AnsiCell* row = ptr + (upper-1) * width;

			for (int i = left + 2; i < right; i++)
			{
				if (i >= 0 && i < width)
				{
					row[i].bk = xd_bck;
					row[i].fg = black;
					row[i].gl = ' ';
				}
			}
		}

		if (len>0 && buf[0]=='\\')
		{
			int qx = left+2 - 1;
			int qy = y + size[1]+2;
			if (qx>=0 && qx<width && qy>=0 && qy<height)
			{
				AnsiCell* ac = ptr + left+2 - 1 + width * (y + size[1]+2);
				ac->fg = dk_red;
				ac->gl = '\\';
			}
		}

		if (cursor)
		{
			int cx = left + 2 + cursor_xy[0];
			int cy = upper - 2 - cursor_xy[1];
			if (cx >= 0 && cx < width && cy >= 0 && cy < height)
			{
				AnsiCell* row = ptr + cx + cy * width;
				uint8_t swap = row->fg;
				row->fg = row->bk;
				row->bk = swap;
			}
		}
	}

	void MoveCursorHead()
	{
		cursor_pos = 0;
		int _pos[2];
		int bl = Reflow(0, _pos);

		assert(bl >= 0); // TODO: [Backlog Ref] assert(bl >= 0);

		cursor_xy[0] = _pos[0];
		cursor_xy[1] = _pos[1];
	}

	void MoveCursorTail()
	{
		cursor_pos = len;
		int _pos[2];
		int bl = Reflow(0, _pos);

		assert(bl >= 0); // TODO: [Backlog Ref] assert(bl >= 0);

		cursor_xy[0] = _pos[0];
		cursor_xy[1] = _pos[1];
	}

	void MoveCursorHome()
	{
		cursor_xy[0] = 0;
		int _pos[2];
		int bl = Reflow(0, _pos);
		assert(bl >= 0); // TODO: [Backlog Ref] assert(bl >= 0);
		cursor_xy[0] = (int8_t)(bl & 0xFF);
		cursor_pos = bl >> 8;
	}

	void MoveCursorEnd()
	{
		cursor_xy[0] = max_width;
		int _pos[2];
		int bl = Reflow(0, _pos);
		assert(bl >= 0); // TODO: [Backlog Ref] assert(bl >= 0);
		cursor_xy[0] = (int8_t)(bl & 0xFF);
		cursor_pos = bl >> 8;
	}

	void MoveCursorX(int dx)
	{
		if (dx < 0 && cursor_pos>0 || dx > 0 && cursor_pos < len)
		{
			cursor_pos += dx;
			if (cursor_pos < 0)
				cursor_pos = 0;
			if (cursor_pos > len)
				cursor_pos = len;

			int _pos[2];
			int bl = Reflow(0, _pos);

			assert(bl >= 0); // TODO: [Backlog Ref] assert(bl >= 0);

			cursor_xy[0] = _pos[0];
			cursor_xy[1] = _pos[1];
		}
	}

	void MoveCursorY(int dy)
	{
		if (dy < 0 && cursor_xy[1]>0 || dy > 0 && cursor_xy[1] < size[1] - 1)
		{
			if (cursor_xy[0]<0)
				cursor_xy[0]=0;

			cursor_xy[1] += dy;
			assert(cursor_xy[1]>=0 && cursor_xy[1] < size[1]);

			int bl = Reflow(0, 0);
			assert(bl>=0);
			cursor_pos = bl>>8;
			cursor_xy[0] = (int8_t)(bl&0xFF);
		}
	}
	
	bool Input(int ch)
	{
		// insert / delete char, update size and cursor pos
		if (ch == 127)
		{
			if (cursor_pos == len)
				return false;
			if (cursor_pos < len-1)
				memmove(buf + cursor_pos, buf + cursor_pos + 1, len - 1 - cursor_pos);
			len--;

			int _size[2], _pos[2];
			int bl = Reflow(_size, _pos);

			assert(bl >= 0 || bl == -2 && cursor_xy[1] == size[1] - 1 && _size[1] == cursor_xy[1]);

			size[0] = _size[0];
			size[1] = _size[1];
			cursor_xy[0] = _pos[0];
			cursor_xy[1] = _pos[1];
		}
		else
		if (ch == 8)
		{
			if (cursor_pos > 0)
			{
				if (cursor_pos < len)
					memmove(buf + cursor_pos - 1, buf + cursor_pos, len - cursor_pos);
				cursor_pos--;
				len--;

				int _size[2], _pos[2];
				int bl = Reflow(_size, _pos);

				// detect nasty case when deleting char in last line causes num of lines to decrease
				// resulting in original cursor_xy[1] is out of nuber of lines range (after modification)
				assert(bl >= 0 || bl==-2 && cursor_xy[1]==size[1]-1 && _size[1]==cursor_xy[1]);

				size[0] = _size[0];
				size[1] = _size[1];
				cursor_xy[0] = _pos[0];
				cursor_xy[1] = _pos[1];
			}
			else
				return false;
		}
		else
		{
			if (len < 256)
			{
				if (cursor_pos < len)
					memmove(buf + cursor_pos + 1, buf + cursor_pos, len - cursor_pos);
				buf[cursor_pos] = ch;
				cursor_pos++;
				len++;

				int _size[2],_pos[2];
				int bl = Reflow(_size, _pos);
				if (bl >= 0)
				{
					size[0] = _size[0];
					size[1] = _size[1];
					cursor_xy[0] = _pos[0];
					cursor_xy[1] = _pos[1];
				}
				else
				{
					// revert!!!
					if (cursor_pos < len)
						memmove(buf + cursor_pos - 1, buf + cursor_pos, len - cursor_pos);
					cursor_pos--;
					len--;

					return false;
				}
			}
			else
				return false;
		}

		return true;
	}

	// returns -1 on overflow, otherwise (b<<8) | l // TODO: [Backlog Ref] returns -1 on overflow, otherwise (b<<8) | l
	// where l = 'current line' length and b = buffer offset at 'current line' begining
	// if _pos is null 'current line' is given directly by cursor_xy[1] otherwise indirectly by cursor_pos // TODO: [Backlog Ref] if _pos is null 'current line' is given directly by cursor_xy[1] otherwise indirectly by cursor_pos
	int Reflow(int _size[2], int _pos[2], void (*print)(int x, int y, const char* str, int len, void* cookie, bool synth)=0, void* cookie=0) const
	{
		// ALWAYS cursor_pos -> _xy={x,y} and _pos={prevline_pos,nextline_pos}

		int x = 0, y = 0;
		int cx = -1, cy = -1;
		int wordlen = 0;

		int ret = -2; // reflow ok but cursor_xy[1] too big

		int w = 2;

		int escape = 0;
		if (len>0 && buf[0]=='\\')
		{
			escape++;
			{
				// nasty hack
				// for shifting initial '\' // TODO: [Backlog Ref] for shifting initial '\'
				// over left margin
				x--;
				wordlen--;
			}
			if (len>1 && buf[1]=='\\')
				escape++;
		}

		int c_xy[2] = {cursor_xy[0],cursor_xy[1]};
		if (c_xy[0]<0 && !escape)
			c_xy[0]=0;

		// todo: // TODO: [Backlog Ref] todo
		// actually we need to call print() only on y++ and last line!

		for (int c = 0; c < len; c++)
		{
			assert(x < max_width);

			if (c == cursor_pos)
			{
				cx = x;
				cy = y;
			}

			if (y==c_xy[1])
			{
				if (x<=c_xy[0])
					ret = (c << 8) | (x&0xFF);
			}				

			if (buf[c] == ' ')
			{
				if (print)
					print(x - wordlen, y, buf + c - wordlen, wordlen+1, cookie, false); // +1 to include space char

				wordlen = 0;
				x++;

				if (x > w)
					w = x;

				if (x == max_width)
				{
					if (print)
						print(x, y, "\n", 1, cookie, true);

					x = 0;
					y++;

					if (y == max_height && max_height)
						return -1;
				}
			}
			else
			if (buf[c] == '\n')
			{
				if (print)
					print(x - wordlen, y, buf + c - wordlen, wordlen+1, cookie, false); // including '\n'

				if (x >= w) // moved
					w = x+1;

				wordlen = 0;
				x = 0;
				y++;
				if (y == max_height && max_height)
					return -1;
			}
			else
			{
				if (x == max_width - 1)
				{
					if (x == wordlen) // break the word! // TODO: [Backlog Ref] break the word!
					{
						if (y==c_xy[1])
						{
							// overwrite possibly bigger ret!
							if ((x-1)<=c_xy[0])
								ret = ((c-1) << 8) | ((x-1)&0xFF);
						}

						w = max_width;

						if (print)
						{
							print(0, y, buf+c-wordlen, wordlen, cookie, false);
							print(x, y, "\n", 1, cookie, true);
						}

						wordlen = 0;
						y++;
						x = 0;
						c--; // current char must be moved to the next line

						if (y == max_height && max_height)
							return -1;

						continue;
					}
					else // try wrapping the word
					{
						if (y==c_xy[1])
						{
							// overwrite possibly bigger ret!
							if ((x - wordlen - 1)<=c_xy[0])
								ret = ((c-wordlen-1) << 8) | ((x-wordlen-1)&0xFF);
						}

						if (print)
							print(x - wordlen, y, "\n", 1, cookie, true);

						c -= wordlen+1;
						wordlen = 0;
						x = 0;
						y++;

						if (y == max_height && max_height)
							return -1;

						continue;
					}
				}

				x++;
				wordlen++;
			}
		}

		if (y==c_xy[1])
		{
			if (x<=c_xy[0])
				ret = (len << 8) | (x&0xFF);		
		}

		if (print)
		{
			print(x - wordlen, y, buf + len - wordlen, wordlen, cookie, false);
			print(x, y, "\n", 1, cookie, true);
		}

		if (x >= w)
		{
			w = x + 1; // ensure extra space char at ending
		}

		// terminator handler
		{
			if (len == cursor_pos)
			{
				cx = x;
				cy = y;
			}

			if (y==c_xy[1])
			{
				if (x<=c_xy[0])
					ret = (len << 8) | (x&0xFF);
			}			
		}

		if (_size)
		{
			_size[0] = w;
			_size[1] = y + 1;
		}

		if (_pos)
		{
			if (cursor_pos == len)
			{
				_pos[0] = x;
				_pos[1] = y;
			}
			else
			{
				_pos[0] = cx;
				_pos[1] = cy;
			}
		}

		// this is possible that when pressing backspace
		// when x=0 and y>0 in last line, we will not reach current line (1)
		// fix it so caller won't blame us.

		assert(ret>=0 || y<c_xy[1]);

		return ret;
	}

	char buf[256];
};
