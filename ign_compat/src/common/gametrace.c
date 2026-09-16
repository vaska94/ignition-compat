/* gametrace.c - a reference recording of the original simulation.
 *
 * Nothing in the rebuild is verifiable without this. Each replacement we write
 * has to produce the same numbers as the 1997 code, and the only way to know
 * is to record what the 1997 code actually produced.
 *
 * Addresses, all verified in Ign_win.exe (fixed base 0x00400000, no ASLR):
 *   0x00422680  fixed-step simulation driver. Its first instructions are
 *               `fld [0x54F948]; fadd [esp+4]; fcom [0x479A50]`, where that
 *               constant is exactly 0.5 - an accumulator against a fixed step.
 *   0x0054F948  the accumulator (double).
 *   0x005DAFFC  pointer to the car array (read at 0x004226BF).
 *   0x006192F0  car count; it is the loop bound tested at 0x004226AA.
 *   0x484C      per-car stride.
 *
 * The hook records state on entry, before the step runs, so the recording is
 * the sequence of states the simulation saw.
 *
 * Two things this code must not do. It must not disturb the x87 stack, since
 * the whole simulation is x87 double precision - so the accumulator is copied
 * as raw bytes, never loaded as a float. And it must not perturb the game's
 * registers, which is why the entry stub saves everything around the call.
 */
#include <windows.h>
#include <string.h>
#include "gametrace.h"
#include "detour.h"
#include "ignlog.h"

#define SIM_STEP_VA   0x00422680u
#define SIM_ACC       0x0054F948u
#define CAR_ARRAY_PTR 0x005DAFFCu
#define CAR_COUNT     0x006192F0u
#define CAR_STRIDE    0x484Cu
#define MAX_CARS      64

/* Render side. 0x0044F0E9 is the common tail of both submit wrappers, so it
 * sees every frame on every path. On entry esi holds the 0x20-byte block:
 *   +0x00 primitive list  +0x04 framebuffer  +0x08 second buffer
 *   +0x0C shade LUT       +0x10..+0x1C clip rect (integer pixels)
 * The list is a NULL-terminated array of pointers to primitive records, whose
 * vertices are ALREADY screen-space 24.8 fixed point - there is no z or w, the
 * game paints back to front out of depth buckets. */
#define PRIM_SUBMIT_VA 0x0044F0E9u
/* The 3D framebuffer. Capturing it lets a rebuilt renderer be compared against
 * the original pixel for pixel. Note the hook runs on submit *entry*, before
 * this frame is drawn, so the buffer still holds the PREVIOUS frame's result -
 * pair capture N of the framebuffer with primitive frame N-1. */
#define FB_BASE        0x00563DB0u
#define FB_BYTES       488000u
#define PRIM_BYTES     0x48u        /* largest record we care about so far */
#define UV_BYTES       24u
#define MAX_PRIMS      20000

/* Context the decoder cannot get from the primitive records themselves. */
#define TEX_SLOT_BASES 0x005530F0u      /* 10 dwords of texture-area slot bases */
#define TEX_SIZE_GLOBAL 0x0054F974u
#define LUT_PAN        0x005DFE64u      /* blend table, indexed [src<<8 | dst]  */
#define LUT_SHD        0x00527F74u      /* shade table                          */
#define LUT_TAB        0x00563BE4u
#define LUT_BYTES      0x10000u

/* Record sizes by type. A fixed-size copy is not enough: near the end of a
 * committed page a 0x48-byte read fails and the whole record would be captured
 * as zeros, silently losing it. Copy exactly what the type defines. Types not
 * listed are ones the game itself treats as invalid - it abandons the display
 * list rather than skipping them. */
static unsigned prim_size(unsigned type)
{
    switch (type) {
    case 0x00: return 0x0C;
    case 0x01: return 0x18;
    case 0x02: return 0x14;
    case 0x07: return 0x44;
    case 0x0B: return 0x24;
    case 0x0C: case 0x0D: return 0x28;
    case 0x0F: return 0x30;
    case 0x10: return 0x38;
    case 0x11: case 0x13: case 0x16: return 0x24;
    case 0x12: return 0x28;
    case 0x14: return 0x44;
    case 0x15: return 0x48;
    default:   return 0;
    }
}

/* Only these carry a UV-array pointer at +0x1C. For 0x13 that field is a shade
 * row index, and dereferencing it would be meaningless. */
static int has_uv_pointer(unsigned type)
{
    return type == 0x11 || type == 0x12 || type == 0x16;
}

/* fld qword ptr [0x54F948] - confirmed by _re/tools/hookspec.py as a safe
 * displacement: 6 bytes, instruction boundary, no relative operand, nothing
 * branches into it. */
static const BYTE sim_sig[] = { 0xDD, 0x05, 0x48, 0xF9, 0x54, 0x00 };
/* pushal; mov [0x49C9E4], esi - also hookspec-clean */
static const BYTE prim_sig[] = { 0x60, 0x89, 0x35, 0xE4, 0xC9, 0x49, 0x00 };

/* Not static: the assembly stub below refers to these by name. */
void *g_sim_original;
void *g_prim_original;
/* Provided by the ddraw shim: the palette in force at draw time, which the
 * game fades, so a captured framebuffer needs its own copy. */
extern const unsigned int *ign_current_palette(void);
void  trace_dump_step(void);
void  trace_dump_prims(const void *block);

/* Entry stub. It tail-jumps to the original rather than calling it, so the
 * stack, the arguments and the return value are exactly as the game expects -
 * the original returns straight to the game's own caller. */
__asm__(
    ".text\n"
    ".globl _ign_sim_stub\n"
    "_ign_sim_stub:\n"
    "  pushal\n"
    "  pushfl\n"
    "  call _trace_dump_step\n"
    "  popfl\n"
    "  popal\n"
    "  jmp *_g_sim_original\n"
);
extern void ign_sim_stub(void);

/* esi still holds the block here: pushal does not disturb it. */
__asm__(
    ".text\n"
    ".globl _ign_prim_stub\n"
    "_ign_prim_stub:\n"
    "  pushal\n"
    "  pushfl\n"
    "  push %esi\n"
    "  call _trace_dump_prims\n"
    "  add $4, %esp\n"
    "  popfl\n"
    "  popal\n"
    "  jmp *_g_prim_original\n"
);
extern void ign_prim_stub(void);

static HANDLE open_beside_exe(const char *leaf, const void *hdr, DWORD hdrlen);

static detour_t g_sim_detour, g_prim_detour;
static HANDLE   g_file = INVALID_HANDLE_VALUE;
static HANDLE   g_pfile = INVALID_HANDLE_VALUE;
static HANDLE   g_ffile = INVALID_HANDLE_VALUE;
static int      g_steps, g_max_steps, g_car_bytes;
static int      g_frames, g_max_frames;

static int readable_ptr(const void *p, unsigned n)
{
    MEMORY_BASIC_INFORMATION mbi;
    if (!p || !VirtualQuery(p, &mbi, sizeof mbi)) return 0;
    if (mbi.State != MEM_COMMIT || (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD))) return 0;
    return (UINT_PTR)mbi.BaseAddress + mbi.RegionSize >= (UINT_PTR)p + n;
}

/* Record one frame's primitive list. Everything here is pointer chasing into
 * the game's own memory, so each dereference is checked - a malformed list
 * must end the capture, not the game. */
/* Written once, on the first captured frame, when the level is fully loaded.
 * Texture pointers in the records are only resolvable to a (file, page) with
 * the slot bases, and the blend/shade tables are referenced by raw address. */
static void dump_context(const void *block)
{
    HANDLE h;
    DWORD wr;
    BYTE hdr[16];
    unsigned i;
    static const DWORD luts[3] = { LUT_PAN, LUT_SHD, LUT_TAB };

    memcpy(hdr, "IGNCTX1", 8);
    *(DWORD *)(hdr + 8)  = LUT_BYTES;
    *(DWORD *)(hdr + 12) = 3;
    h = open_beside_exe("ign_trace_ctx.bin", hdr, sizeof hdr);
    if (h == INVALID_HANDLE_VALUE) return;

    WriteFile(h, block, 0x20, &wr, NULL);                       /* submit block */
    if (readable_ptr((const void *)(UINT_PTR)TEX_SLOT_BASES, 40))
        WriteFile(h, (const void *)(UINT_PTR)TEX_SLOT_BASES, 40, &wr, NULL);
    if (readable_ptr((const void *)(UINT_PTR)TEX_SIZE_GLOBAL, 4))
        WriteFile(h, (const void *)(UINT_PTR)TEX_SIZE_GLOBAL, 4, &wr, NULL);

    for (i = 0; i < 3; i++) {
        const BYTE *lut = NULL;
        DWORD base = 0;
        if (readable_ptr((const void *)(UINT_PTR)luts[i], sizeof(void *)))
            lut = *(const BYTE *const *)(UINT_PTR)luts[i];
        base = (DWORD)(UINT_PTR)lut;
        WriteFile(h, &base, 4, &wr, NULL);
        if (lut && readable_ptr(lut, LUT_BYTES)) {
            WriteFile(h, lut, LUT_BYTES, &wr, NULL);
        } else {
            DWORD zero = 0, k;
            for (k = 0; k < LUT_BYTES / 4; k++) WriteFile(h, &zero, 4, &wr, NULL);
        }
    }
    CloseHandle(h);
    IGNLOG("trace: wrote texture slot bases and the 3 lookup tables");
}

void trace_dump_prims(const void *block)
{
    const BYTE *const *list;
    DWORD wr, count = 0;
    const BYTE *b = (const BYTE *)block;

    if (g_pfile == INVALID_HANDLE_VALUE || g_frames >= g_max_frames) return;
    /* Both submit wrappers funnel through here, so the menu renders through it
     * too. Wait for the simulation to tick at least once, so the frames we keep
     * are race frames rather than the frontend. */
    if (g_max_steps > 0 && g_steps == 0) return;
    if (!readable_ptr(b, 0x20)) return;
    if (g_frames == 0) dump_context(b);
    list = *(const BYTE *const *const *)(b + 0x00);
    if (!readable_ptr(list, sizeof(void *))) return;

    while (count < MAX_PRIMS && readable_ptr(list + count, sizeof(void *)) && list[count])
        count++;

    if (g_ffile != INVALID_HANDLE_VALUE) {
        const unsigned int *pal = ign_current_palette();
        BYTE zero[1024];
        BYTE fh[8];
        *(DWORD *)(fh + 0) = (DWORD)g_frames;
        *(DWORD *)(fh + 4) = FB_BYTES;
        WriteFile(g_ffile, fh, sizeof fh, &wr, NULL);
        if (pal) {
            WriteFile(g_ffile, pal, 1024, &wr, NULL);
        } else {
            memset(zero, 0, sizeof zero);
            WriteFile(g_ffile, zero, sizeof zero, &wr, NULL);
        }
        WriteFile(g_ffile, (const void *)(UINT_PTR)FB_BASE, FB_BYTES, &wr, NULL);
    }
    {
        BYTE hdr[8 + 0x20];
        *(DWORD *)(hdr + 0) = (DWORD)g_frames;
        *(DWORD *)(hdr + 4) = count;
        memcpy(hdr + 8, b, 0x20);
        WriteFile(g_pfile, hdr, sizeof hdr, &wr, NULL);
    }
    {
        DWORD i;
        BYTE rec[PRIM_BYTES + UV_BYTES];
        for (i = 0; i < count; i++) {
            const BYTE *p = list[i];
            memset(rec, 0, sizeof rec);
            if (readable_ptr(p, 4)) {
                unsigned type = *(const DWORD *)p;
                unsigned n = prim_size(type);
                if (n == 0 || n > PRIM_BYTES) n = 0x24;   /* unknown: best effort */
                if (readable_ptr(p, n)) {
                    memcpy(rec, p, n);
                    if (has_uv_pointer(type)) {
                        const BYTE *uv = *(const BYTE *const *)(p + 0x1C);
                        if (readable_ptr(uv, UV_BYTES))
                            memcpy(rec + PRIM_BYTES, uv, UV_BYTES);
                    }
                }
            }
            WriteFile(g_pfile, rec, sizeof rec, &wr, NULL);
        }
    }
    if (++g_frames >= g_max_frames) {
        FlushFileBuffers(g_pfile);
        IGNLOG("trace: recorded %d frames of primitives, stopping", g_frames);
    }
}

void trace_dump_step(void)
{
    const BYTE *cars;
    BYTE  rec[20];
    int   n, i;
    DWORD wr;

    if (g_file == INVALID_HANDLE_VALUE || g_steps >= g_max_steps) return;

    cars = *(const BYTE * const *)(UINT_PTR)CAR_ARRAY_PTR;
    n    = *(const int *)(UINT_PTR)CAR_COUNT;
    if (!cars || n <= 0 || n > MAX_CARS) return;   /* not in a race yet */

    *(DWORD *)(rec + 0) = (DWORD)g_steps;
    *(DWORD *)(rec + 4) = (DWORD)n;
    *(DWORD *)(rec + 8) = (DWORD)g_car_bytes;
    memcpy(rec + 12, (const void *)(UINT_PTR)SIM_ACC, 8);   /* bytes, not a load */
    WriteFile(g_file, rec, sizeof rec, &wr, NULL);
    for (i = 0; i < n; i++)
        WriteFile(g_file, cars + (SIZE_T)i * CAR_STRIDE, (DWORD)g_car_bytes, &wr, NULL);

    if (++g_steps >= g_max_steps) {
        FlushFileBuffers(g_file);
        IGNLOG("trace: recorded %d simulation steps, stopping", g_steps);
    }
}

static HANDLE open_beside_exe(const char *leaf, const void *hdr, DWORD hdrlen)
{
    char path[MAX_PATH], *p, *q;
    HANDLE h;
    DWORD wr;
    GetModuleFileNameA(NULL, path, MAX_PATH);
    p = path; q = path;
    while (*q) { if (*q == '\\' || *q == '/') p = q + 1; q++; }
    lstrcpynA(p, leaf, (int)(sizeof path - (p - path)));
    h = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        IGNLOG("trace: cannot create %s (error %lu)", path, (unsigned long)GetLastError());
        return h;
    }
    if (hdr && hdrlen) WriteFile(h, hdr, hdrlen, &wr, NULL);
    IGNLOG("trace: writing %s", path);
    return h;
}

int gametrace_install_prims(int max_frames)
{
    BYTE hdr[16];
    detour_status_t st;

    if (max_frames <= 0) return 0;
    g_max_frames = max_frames;
    memcpy(hdr, "IGNPRM1", 8);
    *(DWORD *)(hdr + 8)  = PRIM_BYTES;
    *(DWORD *)(hdr + 12) = UV_BYTES;
    g_pfile = open_beside_exe("ign_trace_prims.bin", hdr, sizeof hdr);
    if (g_pfile == INVALID_HANDLE_VALUE) return 0;
    {
        BYTE fh[16];
        memcpy(fh, "IGNFB1\0", 8);   /* 6 chars + 2 NULs = the 8 bytes we write */
        *(DWORD *)(fh + 8)  = FB_BYTES;
        *(DWORD *)(fh + 12) = 1024;          /* palette bytes preceding each frame */
        g_ffile = open_beside_exe("ign_trace_fb.bin", fh, sizeof fh);
    }

    g_prim_detour.name        = "prim_submit";
    g_prim_detour.addr        = PRIM_SUBMIT_VA;
    g_prim_detour.sig         = prim_sig;
    g_prim_detour.sig_len     = sizeof prim_sig;
    g_prim_detour.replacement = (void *)ign_prim_stub;
    st = detour_install(&g_prim_detour);
    g_prim_original = g_prim_detour.original;
    IGNLOG("trace: primitive hook %s, up to %d frames", detour_status_str(st), max_frames);
    if (st != DETOUR_OK) { CloseHandle(g_pfile); g_pfile = INVALID_HANDLE_VALUE; return 0; }
    return 1;
}

int gametrace_install(int max_steps, int car_bytes)
{
    detour_status_t st;
    BYTE  hdr[16];

    if (max_steps <= 0) return 0;
    if (car_bytes <= 0) car_bytes = 0x400;
    if (car_bytes > (int)CAR_STRIDE) car_bytes = (int)CAR_STRIDE;
    g_max_steps = max_steps;
    g_car_bytes = car_bytes;

    memcpy(hdr, "IGNSIM1", 8);
    *(DWORD *)(hdr + 8)  = CAR_STRIDE;
    *(DWORD *)(hdr + 12) = (DWORD)car_bytes;
    g_file = open_beside_exe("ign_trace_sim.bin", hdr, sizeof hdr);
    if (g_file == INVALID_HANDLE_VALUE) return 0;

    g_sim_detour.name        = "sim_step";
    g_sim_detour.addr        = SIM_STEP_VA;
    g_sim_detour.sig         = sim_sig;
    g_sim_detour.sig_len     = sizeof sim_sig;
    g_sim_detour.replacement = (void *)ign_sim_stub;
    st = detour_install(&g_sim_detour);
    /* Safe to publish after installing: this runs during DirectDrawCreate,
     * long before the simulation first ticks. */
    g_sim_original = g_sim_detour.original;

    IGNLOG("trace: sim hook %s, up to %d steps x %d bytes/car",
           detour_status_str(st), max_steps, car_bytes);
    if (st != DETOUR_OK) {
        CloseHandle(g_file);
        g_file = INVALID_HANDLE_VALUE;
        return 0;
    }
    return 1;
}

void gametrace_close(void)
{
    if (g_file != INVALID_HANDLE_VALUE) {
        FlushFileBuffers(g_file); CloseHandle(g_file); g_file = INVALID_HANDLE_VALUE;
    }
    if (g_pfile != INVALID_HANDLE_VALUE) {
        FlushFileBuffers(g_pfile); CloseHandle(g_pfile); g_pfile = INVALID_HANDLE_VALUE;
    }
    if (g_ffile != INVALID_HANDLE_VALUE) {
        FlushFileBuffers(g_ffile); CloseHandle(g_ffile); g_ffile = INVALID_HANDLE_VALUE;
    }
}
