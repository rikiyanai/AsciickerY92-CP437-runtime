#ifndef LEXER_H
#define LEXER_H

// ============================================================================
// lexer.h -- Single-character streaming lexer / tokenizer for JavaScript-like
//            syntax highlighting in the Asciicker in-game terminal.
//
// ARCHITECTURE:
//   This is a hand-rolled state-machine lexer. It processes one character at a
//   time via Get(c) and returns a token type (low 8 bits) plus an optional
//   "recolor backwards" count (bits 8+). The state machine has ~40 states
//   covering: strings (single/double/template), escape sequences (octal, hex,
//   hex-group \u{...}), comments (line/block), numbers (dec, oct, hex, bin,
//   float with exponent), identifiers, and JS keyword matching.
//
// TOKEN TYPES (enum Token):
//   white_space, string_delimiter, string_escape, string_error, string_char,
//   number_char, error_char, operator_char, identifier, keyword,
//   line_comment, block_comment, bracket_rnd/sqr/crl, template_delimiter
//
// API USAGE PATTERN:
//   Lexer lex = {};               // zero-init puts state = Pure
//   for each char c in source:
//       uint32_t tok = lex.Get(c);
//       uint8_t  token_type   = tok & 0xFF;
//       uint8_t  recolor_back = tok >> 8;   // chars to recolor retroactively
//
// KEYWORD MATCHING:
//   Uses a sorted-table binary prefix matcher (Matcher::find) that tracks
//   partial matches incrementally. Covers JS reserved words, built-in
//   constructors, and two custom "ak" / "akPrint" engine keywords.
//
// CONSUMERS:
//   game.cpp -- terminal text rendering uses Lexer to colorize script cells
//
// [FLOW:WORLD] Indirect -- script cells embedded in world are highlighted
//              during terminal rendering (game.cpp PaintProc).
// ============================================================================

#include <assert.h>
#include <string.h>
#include <algorithm>

struct Lexer
{
    // WHY: State machine enumeration. Each value represents a distinct parser
    // position within the grammar. States are grouped by feature: string
    // variants (double/single/template) x sub-states (base/escape/oct/hex/hexgrp),
    // comment variants, number formats, and identifier/keyword tracking.
    enum State
    {
        Pure,                    // top-level / no context
        StringDouble,            // inside "..."
        StringDoubleEsc,         // after \ inside "..."
        StringDoubleOct,         // \0-\377 octal escape inside "..."
        StringDoubleHex,         // \xNN or \uNNNN hex escape inside "..."
        StringSingle,            // inside '...'
        StringSingleEsc,         // after \ inside '...'
        StringSingleOct,         // \0-\377 octal escape inside '...'
        StringSingleHex,         // \xNN or \uNNNN hex escape inside '...'
        SlashCommentOrDiv,       // WHY: '/' is ambiguous -- could start //, /*, or be division
        BlockComment,            // inside /* ... */
        LineComment,             // inside // ... \n
        BlockCommentAlmostEnd,   // saw '*' inside block comment, next '/' ends it
        Identifier,              // accumulating [a-zA-Z_$0-9] that does NOT match a keyword
        Keyword,                 // accumulating [a-zA-Z_$0-9] that currently matches a keyword prefix
        FloatOrMember,           // WHY: '.' alone is ambiguous -- could be .5 float or obj.member
        DecimalQuotient,         // decimal digits (1-9 lead)
        NumberLeadingZero,       // WHY: '0' prefix is ambiguous -- could be 0x, 0b, 0NNN octal, or just 0
        FloatOrOctal,            // WHY: digits 0-7 after leading zero -- still ambiguous octal vs float
        HexNumber,               // 0x... hex integer
        BinNumber,               // 0b... binary integer
        FloatQuotient,           // WHY: digits 8-9 seen after leading-zero -- no longer valid octal, must be float
        FloatFraction,           // fractional part after '.'
        FloatExponent,           // 'e'/'E' seen, awaiting optional sign or digits
        FloatSignedExponent,     // sign consumed, accumulating exponent digits

        StringTemplate,          // inside `...` template literal
        StringTemplateEsc,       // after \ inside `...`
        StringTemplateOct,       // octal escape inside `...`
        StringTemplateHex,       // \xNN or \uNNNN hex escape inside `...`
        StringTemplateDol,       // $ awaits {

        // WHY: \u{NNNN...} ES6 Unicode code-point escapes allow variable-length
        // hex inside braces. These three states track that for each string type.
        StringTemplateHexGrp,    // \u{...} inside `...`
        StringDoubleHexGrp,      // \u{...} inside "..."
        StringSingleHexGrp,      // \u{...} inside '...'
    };

    // Token types returned in low 8 bits of Get(). The consumer (game.cpp)
    // maps each token type to a foreground color via a lookup table.
    enum Token
    {
        white_space,            // spaces, tabs, newlines
        string_delimiter,       // opening/closing quote characters
        string_escape,          // escape sequence characters (\n, \xNN, \u{...}, etc.)
        string_error,           // unterminated string (newline inside non-template string)
        string_char,            // normal string content
        number_char,            // digits and number-format prefixes (0x, 0b, e+, etc.)
        error_char,             // unrecognized or invalid characters
        operator_char,          // symbols like + - * / = < > etc.
        identifier,             // names that do not match a keyword
        keyword,                // JS reserved words or built-in type names
        line_comment,           // // comment content
        block_comment,          // /* comment content */
        bracket_rnd,            // ( and ) -- upper bits encode call length for coloring
        bracket_sqr,            // [ and ] -- upper bits encode call length for coloring
        bracket_crl,            // { and }
        template_delimiter,     // '${' and '}' but interior is colorized as regular code!
    };

    // WHY: Packed into 8 bytes total so the struct can be stored per-cell
    // in the terminal grid without bloating memory (one Lexer per script cell).
    uint8_t  state;  // main state 8 bits
    uint8_t  depth;  // string template expression recursion depth (0-255)
    uint16_t idxlen; // WHY: dual-purpose field -- holds keyword matcher (idx|len) during
                     // identifier scanning, OR escape digit count during string escapes.
                     // TODO(PIPELINE-FIX): overloading this field means a keyword inside
                     // a string escape could corrupt matcher state (not reachable in
                     // practice because string states never enter Identifier/Keyword).
    uint32_t call;   // optional, shifted<<8 length of identifier+whitespace before '('
                     // WHY: enables retroactive recoloring of function-call identifiers
                     // when '(' is finally encountered (game.cpp uses upper bits).

    // WHY: keyword matcher packs index and match-length into a single uint16_t.
    // idx_bits=9 supports up to 512 keywords; len_bits=6 supports keyword
    // names up to 63 chars. Bit 15 is the "partial match" flag.
    static const int idx_bits = 9; // 512 keywords (adjustable)
    static const int len_bits = 15-idx_bits;
    static const int max_len  = (1<<len_bits)-1;
    static const int max_idx  = (1<<idx_bits)-1;

    // Main entry point: feed one character, get back token | (recolor_count << 8).
    // WHY: The recolor_count in upper bits lets the consumer retroactively change
    // the color of already-rendered characters (e.g., '/' that turns out to be
    // the start of '//' comment, or an identifier that becomes a keyword).
    uint32_t Get(char c) /*returns token | (back_num<<8)*/
    {
        // WHY: Matcher is a nested struct (not a free function) so the static
        // keyword table and index are lazily initialized exactly once, on first
        // call. The sorted table enables binary prefix matching: each call
        // advances the match position by one character and returns partial (bit 15
        // set), exact (bit 15 clear), or no-match (0xFFFF).
        struct Matcher
        {
            static uint16_t find(uint16_t state, char c)
            {
                // don't attack me, i already said there's no match!
                assert(state != 0xffff);
                // WHY: keyword table includes JS reserved words, built-in
                // constructors/globals, AND two engine-specific names ("ak",
                // "akPrint") that get highlighted differently in the terminal.
                // The table is sorted on first use (see init block below).
                // TODO(PIPELINE-FIX): "ak" and "akPrint" are mixed in with
                // JS keywords but the comment says "should have another color"
                // -- currently they all get the same keyword color in game.cpp.
                static const char* match[] =
                {
                    /*"await",*/ "break", "case", "catch", "class",
                    "const", "continue", "default", "delete",
                    "do", "else", "enum", "export", "extends",
                    "false", "finally", "for", "function", "if",
                    "implements", "import", "in", "instanceof", "interface",
                    "let", "new", "null", "package", "private",
                    "protected", "public", "return", "super", "switch",
                    "static", "this", "throw", "try", "true",
                    "typeof", "var", "void", "while", "with", /*"yield",*/

                    "ak", "akPrint", // these should have another color
                    "Object","Function","Array","Number","Boolean",
                    "String","Symbol","Date","Promise","RegExp","ArrayBuffer",
                    "Uint8Array","Int8Array","Uint16Array","Int16Array",
                    "Uint32Array","Int32Array","Float32Array","Float64Array",
                    "Uint8ClampedArray","BigUint64Array","BigInt64Array",
                    "DataView","Map","BigInt","Set","WeakMap","WeakSet",
                    "Proxy","Reflect","FinalizationRegistry","WeakRef",
                    "Error","AggregateError","EvalError","RangeError",
                    "ReferenceError","SyntaxError","TypeError","URIError",
                    "JSON","Math","Intl","decodeURI","decodeURIComponent",
                    "encodeURI","encodeURIComponent","escape","unescape",
                    "eval","isFinite","isNaN","parseFloat","parseInt",
                    "Infinity","NaN","undefined","globalThis"

                    //#endif
                };

                // WHY: lazy one-time initialization sorts the keyword table
                // alphabetically and builds a 256-entry first-char index for
                // O(1) coarse lookup. The sorted order also enables early
                // rejection during linear scan (if match[idx][len] > c, stop).
                // TODO(PIPELINE-FIX): memset only covers size*sizeof(uint16_t)
                // bytes but index[] is 256 entries -- if size < 256, the tail
                // entries stay uninitialized. Safe in practice because the
                // find() entry path checks state==0 and coarse-looks up via
                // index[c], but entries for chars with no keywords (e.g. 'z')
                // may read uninitialized 0xFF values from the stack/BSS.
                static bool init = true;
                const size_t size = sizeof(match)/sizeof(match[0]);
                /*statc_*/assert(size <= max_idx+1);
                static uint16_t index[256];

                if (init)
                {
                    init = false;
                    std::sort(match,match+size,[](const char* a, const char* b){ return strcmp(a,b)<0; });
                    memset(index,0xFF,size*sizeof(uint16_t));
                    for (uint16_t i=0; i<(uint16_t)size; i++)
                    {
                        assert(strlen(match[i])<=max_len);
                        if (index[match[i][0]] == 0xFFFF)
                            index[match[i][0]] = i;
                    }
                }

                if (!state)
                {
                    // WHY: first char uses direct index[] lookup to jump
                    // straight to the first keyword starting with that letter.
                    state = index[c];
                    if (state >= size)
                        return 0xFFFF;
                }

                // WHY: unpack current position -- idx is the keyword table
                // row, len is how many chars have matched so far.
                int idx = state & max_idx;
                int len = (state >> idx_bits) & max_len;
                const char* org = match[idx];

                do
                {
                    if (match[idx][len] == c)
                    {
                        len++;
                        // WHY: bit 15 = 1 means partial match (more chars
                        // expected), bit 15 = 0 means exact/full keyword match.
                        return idx | (len<<idx_bits) | (match[idx][len] ? 1<<15 : 0);
                    }

                    if (match[idx][len] > c)
                    {
                        // WHY: alphabetical order guarantees no later entry
                        // can match either -- early exit.
                        break;
                    }

                    idx++;
                } while (idx < size && (!len || !strncmp(org,match[idx],len)));

                // no match
                return 0xFFFF;
            }
        };

        // WHY: outer switch dispatches on the current state. Each state
        // handles one character and either stays, transitions, or "rescans"
        // by recursing into Get(c) after resetting to Pure.
        switch (state)
        {
            case Pure:
            {
                // WHY: Pure is the top-level dispatch. Every unrecognized or
                // terminated sub-expression returns here. The inner switch on
                // the character determines what new state (if any) to enter.
                switch (c)
                {
                    case '[':
                    {
                        // call is already shifted!
                        uint32_t ret = bracket_sqr | call;
                        call=0;
                        return ret;
                    }

                    case ']':
                    {
                        call=0;
                        return bracket_sqr;
                    }

                    case '(':
                    {
                        // call is already shifted!
                        uint32_t ret = bracket_rnd | call;
                        call=0;
                        return ret;
                    }

                    case ')':
                    {
                        call=0;
                        return bracket_rnd;
                    }

                    case '{':
                    {
                        call=0;
                        // WHY: if depth>0 we are inside a template expression
                        // ${...} and nested { is not allowed (error).
                        if (depth)
                            return error_char;
                        return bracket_crl;
                    }

                    case '}':
                    {
                        call=0;
                        // WHY: if depth>0, this '}' closes a template expression
                        // and we return to template-string scanning mode.
                        if (depth)
                        {
                            depth--;
                            state = StringTemplate;
                            return template_delimiter;
                        }
                        return bracket_crl;
                    }

                    case '`':
                        call=0;
                        state = StringTemplate;
                        return string_delimiter;

                    case '\"': 
                        call=0;
                        state = StringDouble;
                        return string_delimiter;

                    case '\'': 
                        call=0;
                        state = StringSingle;
                        return string_delimiter;

                    case '/':
                        call=0;
                        state = SlashCommentOrDiv;
                        // WHY: '/' is initially classified as operator_char.
                        // If the next char is '/' or '*', the SlashCommentOrDiv
                        // state recolors this char retroactively as comment.
                        return operator_char;

                    case '0':
                        call=0;
                        // WHY: leading zero is ambiguous: could be 0x (hex),
                        // 0b (bin), 0NNN (octal), 0.N (float), or just 0.
                        state = NumberLeadingZero;
                        return number_char;

                    case '\\':
                        call=0;
                        return error_char;

                    case '.':
                        call=0;
                        state = FloatOrMember;
                        return operator_char; // may be float or member op !!!!

                    default:
                    if (c>='1' && c<='9')
                    {
                        call=0;
                        state = DecimalQuotient;
                        return number_char;
                    }
                    else
                    if (strchr("!%^&*-+=:;,.?<>|~",c))
                    {
                        call=0;
                        return operator_char;
                    }
                    else
                    if (c>='a' && c<='z' || c>='A' && c<='Z' || c=='_' || c=='$')
                    {
                        call=0x100;
                        idxlen = Matcher::find(0,c);
                        if (idxlen>>15)
                        {
                            // unmatched 0xffff or partially matched 1<<15 | ...
                            state = Identifier;
                            return identifier;
                        }
                        else
                        {
                            // exact match
                            state = Keyword;
                            return keyword;
                        }
                    }
                    else
                    {
                        if (strchr(" \n\r\v\f\t",c))
                        {
                            if (call)
                                call+=0x100;
                            return white_space;
                        }

                        // probably \ or # or @ 
                        // or a special char 0-31 or anything above 126
                        call=0;
                        return error_char;
                    }
                }						
                break;
            }

            case StringTemplate:
            {
                switch (c)
                {
                    case '`': 
                        state = Pure;
                        return string_delimiter;
                    case '\\':
                        state = StringTemplateEsc;
                        return string_escape;
                    case '$':
                        state = StringTemplateDol;
                        return template_delimiter;
                    default:
                        return string_char;
                }
            }

            case StringTemplateDol:
            {
                if (c=='{')
                {
                    depth++;
                    state = Pure;
                    return template_delimiter;
                }

                // recolor $ back
                state = StringTemplate;
                return string_char | (1<<8);
            }

            case StringTemplateEsc:
            {
                switch (c)
                {
                    case 'u':
                    {
                        state = StringTemplateHex;
                        idxlen = 0;
                        return string_escape;
                    }

                    case 'x':
                    {
                        state = StringTemplateHex;
                        idxlen = 2;
                        return string_escape;
                    }

                    default:
                    if (c>='0' && c<='3')
                    {
                        idxlen = 1;
                        state = StringTemplateOct;
                        return string_escape;
                    }
                    state = StringTemplate;
                    return string_escape;
                }
            }

            case StringTemplateOct:
            {
                if (c=='\\')
                {
                    state = StringTemplateEsc;
                    return string_escape;
                }
                else
                if (c=='`')
                {
                    state = Pure;
                    return string_delimiter;
                }
                else
                if (c>='0' && c<='7')
                {
                    idxlen++;
                    if (idxlen==3)
                        state = StringTemplate;
                    return string_escape;
                }
                
                state = StringTemplate;
                return string_char;
            }

            case StringTemplateHex:
            {
                if (idxlen==0 && c=='{')
                {
                    state = StringTemplateHexGrp;
                    return string_escape;
                }

                if (c=='\\')
                {
                    state = StringTemplateEsc;
                    return string_escape;
                }
                else
                if (c=='`')
                {
                    state = Pure;
                    return string_delimiter;
                }
                else
                if (c>='0' && c<='9' || 
                    c>='a' && c<='f' ||
                    c>='A' && c<='F')
                {
                    idxlen++;
                    if (idxlen==4)
                        state = StringTemplate;
                    return string_escape;
                }
                
                state = StringTemplate;
                return string_char;
            }

            case StringDouble:
            {
                switch (c)
                {
                    case '\"': 
                        state = Pure;
                        return string_delimiter;
                    case '\\':
                        state = StringDoubleEsc;
                        return string_escape;
                    case '\n':
                        state = Pure;
                        return string_error;
                    default:
                        return string_char;
                }
                break;
            }

            case StringSingle:
            {
                switch (c)
                {
                    case '\'': 
                        state = Pure;
                        return string_delimiter;
                    case '\\':
                        state = StringSingleEsc;
                        return string_escape;
                    case '\n':
                        state = Pure;
                        return string_error;
                    default:
                        return string_char;
                }
                break;
            }

            case StringDoubleEsc:
            {
                switch (c)
                {
                    case '\n':
                    {
                        state = Pure;
                        return string_error;							
                    }

                    case 'u':
                    {
                        state = StringDoubleHex;
                        idxlen = 0;
                        return string_escape;
                    }

                    case 'x':
                    {
                        state = StringDoubleHex;
                        idxlen = 2;
                        return string_escape;
                    }

                    default:
                    if (c>='0' && c<='3')
                    {
                        idxlen = 1;
                        state = StringDoubleOct;
                        return string_escape;
                    }
                    state = StringDouble;
                    return string_escape;
                }
                break;
            }

            case StringSingleEsc:
            {
                switch (c)
                {
                    case '\n':
                    {
                        state = Pure;
                        return string_error;							
                    }

                    case 'x':
                    {
                        state = StringSingleHex;
                        idxlen = 2;
                        return string_escape;
                    }

                    case 'u':
                    {
                        state = StringSingleHex;
                        idxlen = 0;
                        return string_escape;
                    }

                    default:
                    if (c>='0' && c<='3')
                    {
                        idxlen = 1;
                        state = StringSingleOct;
                        return string_escape;
                    }
                    state = StringSingle;
                    return string_escape;
                }
                break;
            }

            case StringDoubleOct:
            {
                if (c=='\\')
                {
                    state = StringDoubleEsc;
                    return string_escape;
                }
                else
                if (c=='\"')
                {
                    state = Pure;
                    return string_delimiter;
                }
                else
                if (c=='\n')
                {
                    state = Pure;
                    return string_error;
                }
                else
                if (c>='0' && c<='7')
                {
                    idxlen++;
                    if (idxlen==3)
                        state = StringDouble;
                    return string_escape;
                }
                
                state = StringDouble;
                return string_char;
            }

            case StringSingleOct:
            {
                if (c=='\\')
                {
                    state = StringSingleEsc;
                    return string_escape;
                }
                else
                if (c=='\'')
                {
                    state = Pure;
                    return string_delimiter;
                }
                else
                if (c=='\n')
                {
                    state = Pure;
                    return string_error;
                }
                else
                if (c>='0' && c<='7')
                {
                    idxlen++;
                    if (idxlen==3)
                        state = StringSingle;
                    return string_escape;
                }
                
                state = StringSingle;
                return string_char;
            }

            case StringDoubleHex:
            {
                if (idxlen==0 && c=='{')
                {
                    state = StringDoubleHexGrp;
                    return string_escape;
                }

                if (c=='\\')
                {
                    state = StringDoubleEsc;
                    return string_escape;
                }
                else
                if (c=='\"')
                {
                    state = Pure;
                    return string_delimiter;
                }
                else
                if (c=='\n')
                {
                    state = Pure;
                    return string_error;
                }
                else
                if (c>='0' && c<='9' || 
                    c>='a' && c<='f' ||
                    c>='A' && c<='F')
                {
                    idxlen++;
                    if (idxlen==4)
                        state = StringDouble;
                    return string_escape;
                }
                
                state = StringDouble;
                return string_char;
            }

            case StringSingleHex:
            {
                if (idxlen==0 && c=='{')
                {
                    state = StringSingleHexGrp;
                    return string_escape;
                }

                if (c=='\\')
                {
                    state = StringSingleEsc;
                    return string_escape;
                }
                else
                if (c=='\'')
                {
                    state = Pure;
                    return string_delimiter;
                }
                else
                if (c=='\n')
                {
                    state = Pure;
                    return string_error;
                }
                else
                if (c>='0' && c<='9' || 
                    c>='a' && c<='f' ||
                    c>='A' && c<='F')
                {
                    idxlen++;
                    if (idxlen==4)
                        state = StringSingle;
                    return string_escape;
                }
                
                state = StringSingle;
                return string_char;	
            }

            case SlashCommentOrDiv:
            {
                switch (c)
                {
                    case '*':
                        state = BlockComment;
                        return block_comment | (1<<8); // recolor prev char as well!
                    case '/':
                        state = LineComment;
                        return line_comment | (1<<8); // recolor prev char as well!
                    default:
                        // rescan with state 0
                        state = Pure;
                        return Get(c);
                }
                break;
            }
            
            case BlockComment:
            {
                switch (c)
                {
                    case '*':
                        state = BlockCommentAlmostEnd;
                        return block_comment;
                    default:
                        return block_comment;
                }
                break;
            }

            case LineComment:
            {
                switch (c)
                {
                    case '\n':
                        state = Pure;
                        return block_comment;
                    default:
                        return block_comment;
                }
                break;
            }

            case BlockCommentAlmostEnd:
            {
                switch (c)
                {
                    case '*':
                        return block_comment;
                    case '/':
                        state = Pure;
                        return block_comment;
                    default:
                        state = BlockComment;
                        return block_comment;
                }
                break;
            }

            case Identifier:
            {
                if (c>='a' && c<='z' || c>='A' && c<='Z' ||
                    c=='_' || c=='$' || c>='0' && c<='9')
                {
                    if (call)
                        call+=0x100;
                    if (idxlen != 0xffff)
                    {
                        int len = (idxlen >> idx_bits) & max_len;
                        idxlen = Matcher::find(idxlen, c);
                        if (idxlen>>15)
                            return identifier;

                        state = Keyword;
                        return keyword | (len<<8);
                    }
                    return identifier;
                }
                else
                {
                    // do not clear it, 
                    // it can be white-space or actual call '('
                    // Pure state will handle it
                    // call=0;

                    // rescan
                    state = Pure;
                    return Get(c);
                }
                break;
            }

            case Keyword:
            {
                if (c>='a' && c<='z' || c>='A' && c<='Z' ||
                    c=='_' || c=='$' || c>='0' && c<='9')
                {
                    if (call)
                        call+=0x100;
                    int len = (idxlen >> idx_bits) & max_len;
                    idxlen = Matcher::find(idxlen, c);
                    if (idxlen >> 15)
                    {
                        state = Identifier;
                        return identifier | (len<<8);
                    }
                    return keyword;
                }
                else
                {
                    // clear it! we don't want while() if() for() etc
                    // to look like a func call
                    call=0;

                    // rescan
                    state = Pure;
                    return Get(c);
                }
                break;
            }

            case FloatOrMember: // . (float literal without quotient or member operator)
            {
                if (c>='0' && c<='9')
                {
                    state = FloatFraction;
                    return number_char | (1<<8);
                }
                else
                {
                    // rescan
                    state = Pure;
                    return Get(c);
                }

                break;
            }

            case DecimalQuotient: // 765 (decimal or float quotient)
            {
                if (c>='0' && c<='9')
                    return number_char;
                if (c=='e' || c=='E')
                {
                    state = FloatExponent;
                    return number_char;
                }
                if (c=='.')
                {
                    state = FloatFraction;
                    return number_char;
                }
                state = Pure;
                return Get(c);
            }

            case NumberLeadingZero: // 0 (number leading)
            {
                if (c>='0' && c<='7')
                {
                    state = FloatOrOctal;
                    return number_char;
                }
                else
                if (c>='8' && c<='9')
                {
                    state = DecimalQuotient;
                    return number_char;
                }
                else
                if (c=='x' || c=='X')
                {
                    state = HexNumber;
                    return number_char;
                }
                else
                if (c=='b' || c=='B')
                {
                    state = BinNumber;
                    return number_char;
                }
                else
                if (c=='e' || c=='E')
                {
                    state = FloatExponent;
                    return number_char;
                }
                else
                if (c=='.')
                {
                    state = FloatFraction;
                    return number_char;
                }
                state = Pure;
                return Get(c);
            }
            
            case FloatOrOctal: // 234
            {
                if (c>='0' && c<='7')
                {
                    return number_char;
                }					
                if (c>='8' && c<='9')
                {
                    state = FloatQuotient;
                    return number_char;
                }
                if (c=='e' || c=='E')
                {
                    state = FloatExponent;
                    return number_char;
                }
                if (c=='.')
                {
                    state = FloatFraction;
                    return number_char;
                }
                state = Pure;
                return Get(c);
            }

            case HexNumber: // 0x (hex integer)
            {
                if (c>='0' && c<='9' || c>='a' && c<='f' || c>='A' && c<='F')
                    return number_char;
                state = Pure;
                return Get(c);
            }

            case BinNumber: // 0b (bin integer)
            {
                if (c=='0' || c=='1')
                    return number_char;
                state = Pure;
                return Get(c);
            }

            case FloatQuotient:
            {
                if (c>='0' && c<='9')
                    return number_char;
                else
                if (c=='e' || c=='E')
                {
                    state = FloatExponent;
                    return number_char;
                }
                else
                if (c=='.')
                {
                    state = FloatFraction;
                    return number_char;
                }
                state = Pure;
                return Get(c);
            }

            case FloatFraction: // .42 fraction
            {
                if (c>='0' && c<='9')
                    return number_char;
                if (c=='e' || c=='E')
                {
                    state = FloatExponent;
                    return number_char;
                }
                state = Pure;
                return Get(c);
            }

            case FloatExponent: // exponent, awaits sign
            {
                if (c>='0' && c<='9' || c=='-' || c=='+')
                {
                    state = FloatSignedExponent;
                    return number_char;
                }
                state = Pure;
                return Get(c);
            }

            case FloatSignedExponent: // exponent sign, await decimal digits
            {
                if (c>='0' && c<='9')
                    return number_char;
                state = Pure;
                return Get(c);
            }

            case StringTemplateHexGrp:
            {
                switch (c)
                {
                    case '$':
                    {
                        state = StringTemplateDol;
                        return string_escape;
                    }

                    case '}':
                    {
                        state = StringTemplate;
                        return string_escape;
                    }

                    case '\\':
                    {
                        state = StringTemplateEsc;
                        return string_escape;
                    }

                    case '`':
                    {
                        state = Pure;
                        return string_delimiter;
                    }

                    default:
                    //if (idxlen<5)
                    {
                        if (c>='0' && c<='9' || 
                            c>='a' && c<='f' ||
                            c>='A' && c<='F')
                        {
                            if (idxlen<0xffff)
                                idxlen++;
                            return string_escape;
                        }
                    }
                }
                
                state = StringTemplate;
                return string_char;
            }

            case StringDoubleHexGrp:
            {
                switch (c)
                {
                    case '}':
                    {
                        state = StringDouble;
                        return string_escape;
                    }

                    case '\\':
                    {
                        state = StringDoubleEsc;
                        return string_escape;
                    }

                    case '\"':
                    {
                        state = Pure;
                        return string_delimiter;
                    }

                    case '\n':
                    {
                        state = Pure;
                        return string_error;
                    }

                    default:
                    // if (idxlen<5)
                    {
                        if (c>='0' && c<='9' || 
                            c>='a' && c<='f' ||
                            c>='A' && c<='F')
                        {
                            if (idxlen<0xffff)
                                idxlen++;
                            return string_escape;
                        }
                    }
                }
                
                state = StringDouble;
                return string_char;
            }

            case StringSingleHexGrp:
            {
                switch (c)
                {
                    case '}':
                    {
                        state = StringSingle;
                        return string_escape;
                    }

                    case '\\':
                    {
                        state = StringSingleEsc;
                        return string_escape;
                    }

                    case '\'':
                    {
                        state = Pure;
                        return string_delimiter;
                    }

                    case '\n':
                    {
                        state = Pure;
                        return string_error;
                    }

                    default:
                    //if (idxlen<5)
                    {
                        if (c>='0' && c<='9' || 
                            c>='a' && c<='f' ||
                            c>='A' && c<='F')
                        {
                            if (idxlen<0xffff)
                                idxlen++;
                            return string_escape;
                        }
                    }
                }
                
                state = StringSingle;
                return string_char;
            }

            default:
                state = Pure;
                return Get(c);
        }

        state = Pure;
        return Get(c);
    }
};

#endif