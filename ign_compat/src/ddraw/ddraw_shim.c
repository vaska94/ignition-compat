/* ddraw.dll shim for Ignition (UDS / Virgin Interactive, 1997)
 * ============================================================
 * Ignition is a software rasterizer.  It renders 8bpp frames into its own
 * system memory and uses DirectDraw only to get them on screen, via an
 * exclusive-fullscreen 8-bit flip chain.  On Windows 10/11 that path produces
 * a black screen: legacy palettised exclusive modes are emulated badly by
 * modern drivers.
 *
 * This shim reimplements the exact DirectDraw surface the game touches - 21
 * methods across four interfaces - and routes presentation through Direct3D 11
 * (see ../common/d3d11_present.c).  We never change the display mode; the
 * desktop stays where it is and the frame is scaled on the GPU instead.
 *
 * Every vtable slot is implemented with its correct signature, including the
 * ones the game never calls.  These are __stdcall (callee-cleans-stack), so a
 * stub with the wrong arity would corrupt the stack rather than fail cleanly.
 *
 * Behavioural quirks of this specific game, found by disassembly, that this
 * file deliberately accommodates:
 *
 *   1. Unlock()'s argument is garbage - the game passes &surf->lpDDSurface
 *      rather than the locked pointer.  It must be ignored, not validated.
 *   2. The screen-clear helper at 0x0045C3D0 writes DWORDs indexed as
 *      [lPitch*y + x] into an *8bpp* surface: four times the stride and four
 *      times the row length.  Surface memory is over-allocated accordingly or
 *      it would fault on every mode change.
 *   3. GetCaps() must report DDSCAPS_FLIP on back buffers; the game's present
 *      wrapper checks for it and silently skips presenting if it is absent.
 *   4. SetEntries() must recolour the frame already on screen - palette fades
 *      never redraw, they just reload the palette.
 *   5. SetDisplayMode(320,200,8) is a Mode X request and must succeed.
 */
#define CINTERFACE
#include <windows.h>
#include <ddraw.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <mmsystem.h>
#include "../common/iathook.h"
#include "../common/ignlog.h"
#include "../common/d3d11_present.h"

/* ------------------------------------------------------------- objects -- */

typedef struct PalImpl  PalImpl;
typedef struct ClipImpl ClipImpl;
typedef struct SurfImpl SurfImpl;
typedef struct DDImpl   DDImpl;

struct DDImpl {
    const IDirectDrawVtbl *lpVtbl;
    LONG    ref;
    HWND    hwnd;
    DWORD   coop;
    int     w, h, bpp;      /* last SetDisplayMode */
    int     present_up;
};

struct SurfImpl {
    const IDirectDrawSurfaceVtbl *lpVtbl;
    LONG      ref;
    DDImpl   *dd;
    DWORD     caps;
    int       w, h, bpp;
    LONG      pitch;          /* bytes per row */
    BYTE     *bits;
    SIZE_T    bytes;
    SurfImpl *backbuffer;   /* primary -> its back buffer   */
    SurfImpl *flipnext;     /* circular flip chain          */
    PalImpl  *pal;
    ClipImpl *clip;
    int       locked;
    /* GDI interop: the game draws menu//text through a surface DC. */
    HDC       dc;
    HBITMAP   dib;
    HGDIOBJ   olddib;
    void     *dibbits;
    LONG      dibpitch;
};

struct PalImpl {
    const IDirectDrawPaletteVtbl *lpVtbl;
    LONG     ref;
    DWORD    caps;
    uint32_t bgrx[256];
};

struct ClipImpl {
    const IDirectDrawClipperVtbl *lpVtbl;
    LONG  ref;
    HWND  hwnd;
};

/* Rate-limited tracing: the first few of each repeated call, then silence. */
static int g_trace_lock, g_trace_flip, g_trace_pal;
static int g_trace_all = -1;
static int trace_on(void) { return g_trace_all > 0; }
#define TRACE() do { if (trace_on()) ign_logf("  . %s", __func__); } while (0)
#define TRACE_N(counter, ...) do { if ((counter)++ < 5) IGNLOG(__VA_ARGS__); } while (0)

/* The surface whose contents are currently on screen.  A palette change has to
 * re-present it, because the game's fades never redraw the frame. */
static SurfImpl *g_onscreen;
static PalImpl  *g_active_pal;

/* ----------------------------------------------------------- user config - */

static present_scale_t g_scaling   = PRESENT_SCALE_ASPECT;
static int             g_filter    = 2;   /* 0 point, 1 bilinear, 2 sharp */
static int             g_vsync     = 1;
static int             g_windowed  = 0;
static int             g_win_scale = 2;
static int             g_force_bpp = 0;
/* The surface stride the game is given comes from SetDisplayMode's bpp, but the
 * engine is a palettised software rasterizer and writes 8-bit indices into that
 * stride.  Proven empirically: at 640x480x32 the game fills exactly 640 bytes of
 * each 2560-byte row, which renders as content in the left quarter of the frame.
 * So allocation follows the mode; interpretation is P8 unless overridden. */
static int             g_present_fmt = -1;   /* -1 = auto (P8) */
static int             g_block_joy = 1;
static int             g_block_mci = 1;
static int             g_cpu_fix   = 1;
static int             g_dump_frame = 0;

/* Settings come from ign_compat.ini beside the executable, so a user who
 * launches the game by double-clicking can still configure it.  An environment
 * variable of the same name (IGN_<KEY>) overrides the file. */
static char g_ini[MAX_PATH];

static const char *ini_path(void)
{
    if (!g_ini[0]) {
        char *p, *q;
        GetModuleFileNameA(NULL, g_ini, MAX_PATH);
        p = g_ini; q = g_ini;
        while (*q) { if (*q == '\\' || *q == '/') p = q + 1; q++; }
        lstrcpyA(p, "ign_compat.ini");
    }
    return g_ini;
}

static void cfg_str(const char *key, char *out, DWORD n, const char *dflt)
{
    char env[64];
    lstrcpyA(env, "IGN_");
    lstrcatA(env, key);
    if (GetEnvironmentVariableA(env, out, n) > 0) return;
    GetPrivateProfileStringA("ignition", key, dflt, out, n, ini_path());
}

static int cfg_int(const char *key, int dflt)
{
    char b[32];
    char d[16];
    wsprintfA(d, "%d", dflt);
    cfg_str(key, b, sizeof b, d);
    {   /* tiny atoi; avoids depending on the CRT for one parse */
        int v = 0, sign = 1, i = 0;
        if (b[0] == '-') { sign = -1; i = 1; }
        for (; b[i] >= '0' && b[i] <= '9'; i++) v = v * 10 + (b[i] - '0');
        return sign * v;
    }
}

static void load_config(void)
{
    char b[32];
    cfg_str("SCALING", b, sizeof b, "aspect");
    if (!lstrcmpiA(b, "stretch")) g_scaling = PRESENT_SCALE_STRETCH;
    else if (!lstrcmpiA(b, "integer")) g_scaling = PRESENT_SCALE_INTEGER;
    else g_scaling = PRESENT_SCALE_ASPECT;

    /* "sharp" keeps nearest-neighbour crispness while removing the uneven
     * pixel widths a non-integer upscale produces (640->1440 is 2.25x). */
    cfg_str("FILTER", b, sizeof b, "sharp");
    if (!lstrcmpiA(b, "point"))       g_filter = 0;
    else if (!lstrcmpiA(b, "linear")) g_filter = 1;
    else                              g_filter = 2;

    g_trace_all = cfg_int("TRACE", 0);
    g_vsync     = cfg_int("VSYNC", 1);
    g_windowed  = cfg_int("WINDOWED", 0);
    g_win_scale = cfg_int("WINDOW_SCALE", 2);
    g_force_bpp = cfg_int("FORCE_BPP", 0);
    {
        char f[16];
        cfg_str("PIXEL_FORMAT", f, sizeof f, "auto");
        if (!lstrcmpiA(f, "p8"))            g_present_fmt = PRESENT_FMT_P8;
        else if (!lstrcmpiA(f, "rgb565"))   g_present_fmt = PRESENT_FMT_RGB565;
        else if (!lstrcmpiA(f, "xrgb888"))  g_present_fmt = PRESENT_FMT_XRGB888;
        else                                g_present_fmt = -1;
    }
    g_dump_frame = cfg_int("DUMP_FRAME", 0);
    g_block_joy  = cfg_int("BLOCK_JOYSTICK", 1);
    g_block_mci  = cfg_int("BLOCK_MCI", 1);
    g_cpu_fix    = cfg_int("CPU_FIX", 1);
    if (g_win_scale < 1) g_win_scale = 1;
}

static void install_hooks(void);   /* defined below, called from DirectDrawCreate */

/* --------------------------------------------------------------- helpers - */

static SIZE_T surface_bytes(int w, int h, int bpp, LONG *out_pitch)
{
    /* Row stride in BYTES, padded for alignment.  Then quadrupled, plus a
     * margin, to survive quirk (2): the clear helper walks the buffer as
     * DWORDs indexed by [lPitch*y + x], overrunning a correctly-sized
     * allocation by 4x regardless of the pixel depth. */
    int bytespp = bpp / 8;
    LONG pitch;
    if (bytespp < 1) bytespp = 1;
    pitch = (LONG)(((w * bytespp) + 31) & ~31);
    *out_pitch = pitch;
    return (SIZE_T)pitch * (SIZE_T)h * 4u + 4096u;
}

static present_format_t fmt_for_bpp(int bpp)
{
    if (g_present_fmt >= 0) return (present_format_t)g_present_fmt;
    /* Auto: this engine always rasterises 8-bit indices, whatever depth it
     * asked the display for. */
    (void)bpp;
    return PRESENT_FMT_P8;
}

static int g_flipno;

static void dump_frame(SurfImpl *s)
{
    char path[MAX_PATH], *p, *q;
    HANDLE h;
    DWORD wr;
    GetModuleFileNameA(NULL, path, MAX_PATH);
    p = path; q = path;
    while (*q) { if (*q == '\\' || *q == '/') p = q + 1; q++; }
    lstrcpyA(p, "frame_dump.bin");
    h = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    {   /* header: w, h, bpp, pitch, then pixels, then 256 palette entries */
        int hdr[4];
        hdr[0] = s->w; hdr[1] = s->h; hdr[2] = s->bpp; hdr[3] = (int)s->pitch;
        WriteFile(h, hdr, sizeof hdr, &wr, NULL);
        WriteFile(h, s->bits, (DWORD)((SIZE_T)s->pitch * s->h), &wr, NULL);
        if (g_active_pal) WriteFile(h, g_active_pal->bgrx, 256 * 4, &wr, NULL);
    }
    CloseHandle(h);
    IGNLOG("dumped frame %d (%dx%d bpp=%d pitch=%ld)", g_flipno, s->w, s->h,
           s->bpp, (long)s->pitch);
}

static void present_this(SurfImpl *s)
{
    if (!s || !s->bits || !s->dd || !s->dd->present_up) return;
    if (g_dump_frame && ++g_flipno == g_dump_frame) dump_frame(s);
    /* Track window size changes without subclassing the game's WndProc. */
    {
        RECT rc;
        if (GetClientRect(s->dd->hwnd, &rc)) {
            int cw = rc.right - rc.left, ch = rc.bottom - rc.top;
            if (cw > 0 && ch > 0) present_resize(cw, ch);
        }
    }
    present_frame(s->bits, s->pitch);
    g_onscreen = s;
}

static HRESULT ensure_present(DDImpl *dd)
{
    present_config_t cfg;
    HRESULT hr;

    if (dd->present_up) return S_OK;
    if (!dd->hwnd || dd->w <= 0 || dd->h <= 0) return S_OK;   /* not yet */

    memset(&cfg, 0, sizeof cfg);
    cfg.width        = dd->w;
    cfg.height       = dd->h;
    cfg.format       = fmt_for_bpp(dd->bpp);
    cfg.scaling      = g_scaling;
    cfg.filter_mode  = g_filter;
    cfg.vsync        = g_vsync;

    if (g_windowed) {
        RECT r = { 0, 0, dd->w * g_win_scale, dd->h * g_win_scale };
        SetWindowLongA(dd->hwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE);
        AdjustWindowRectEx(&r, WS_OVERLAPPEDWINDOW, FALSE, 0);
        SetWindowPos(dd->hwnd, HWND_NOTOPMOST, 60, 60,
                     r.right - r.left, r.bottom - r.top, SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    }

    hr = present_init(dd->hwnd, &cfg);
    if (FAILED(hr)) { IGNLOG("present_init failed hr=0x%08lX", (unsigned long)hr); return hr; }
    dd->present_up = 1;
    IGNLOG("present up: %dx%d bpp=%d fmt=%d scaling=%d filter=%s vsync=%d windowed=%d",
           dd->w, dd->h, dd->bpp, (int)fmt_for_bpp(dd->bpp), (int)g_scaling,
           g_filter == 0 ? "point" : g_filter == 1 ? "linear" : "sharp",
           g_vsync, g_windowed);
    if (g_active_pal) present_set_palette(g_active_pal->bgrx);
    return S_OK;
}

/* ====================================================== IDirectDrawPalette */

static HRESULT WINAPI Pal_QueryInterface(IDirectDrawPalette *me, REFIID riid, void **out)
{ TRACE();
    (void)riid;
    if (!out) return E_POINTER;
    *out = me;
    ((PalImpl *)me)->ref++;
    return S_OK;
}
static ULONG WINAPI Pal_AddRef(IDirectDrawPalette *me) { TRACE(); return ++((PalImpl *)me)->ref; }
static ULONG WINAPI Pal_Release(IDirectDrawPalette *me)
{ TRACE();
    PalImpl *p = (PalImpl *)me;
    LONG r = --p->ref;
    if (r <= 0) { if (g_active_pal == p) g_active_pal = NULL; free(p); return 0; }
    return r;
}
static HRESULT WINAPI Pal_GetCaps(IDirectDrawPalette *me, LPDWORD caps)
{ TRACE();
    if (!caps) return DDERR_INVALIDPARAMS;
    *caps = ((PalImpl *)me)->caps;
    return DD_OK;
}
static HRESULT WINAPI Pal_GetEntries(IDirectDrawPalette *me, DWORD flags, DWORD base,
                                     DWORD count, LPPALETTEENTRY out)
{ TRACE();
    PalImpl *p = (PalImpl *)me;
    DWORD i;
    (void)flags;
    if (!out || base + count > 256) return DDERR_INVALIDPARAMS;
    for (i = 0; i < count; i++) {
        uint32_t c = p->bgrx[base + i];
        out[i].peRed   = (BYTE)(c >> 16);
        out[i].peGreen = (BYTE)(c >> 8);
        out[i].peBlue  = (BYTE)c;
        out[i].peFlags = 0;
    }
    return DD_OK;
}
static HRESULT WINAPI Pal_Initialize(IDirectDrawPalette *me, LPDIRECTDRAW dd,
                                     DWORD flags, LPPALETTEENTRY tbl)
{ TRACE(); (void)me; (void)dd; (void)flags; (void)tbl; return DDERR_ALREADYINITIALIZED; }

/* Quirk (4): fades reload the palette without redrawing, so a palette change
 * must recolour whatever is already on screen. */
static HRESULT WINAPI Pal_SetEntries(IDirectDrawPalette *me, DWORD flags, DWORD start,
                                     DWORD count, LPPALETTEENTRY in)
{ TRACE();
    PalImpl *p = (PalImpl *)me;
    DWORD i;
    (void)flags;
    if (!in) return DDERR_INVALIDPARAMS;
    if (start > 256) return DDERR_INVALIDPARAMS;
    if (start + count > 256) count = 256 - start;

    for (i = 0; i < count; i++)
        p->bgrx[start + i] = 0xFF000000u
                           | ((uint32_t)in[i].peRed   << 16)
                           | ((uint32_t)in[i].peGreen << 8)
                           |  (uint32_t)in[i].peBlue;

    TRACE_N(g_trace_pal, "SetEntries start=%lu count=%lu active=%d",
            (unsigned long)start, (unsigned long)count, p == g_active_pal);
    if (p == g_active_pal) {
        present_set_palette(p->bgrx);
        present_this(g_onscreen);
    }
    return DD_OK;
}

static const IDirectDrawPaletteVtbl g_pal_vtbl = {
    Pal_QueryInterface, Pal_AddRef, Pal_Release,
    Pal_GetCaps, Pal_GetEntries, Pal_Initialize, Pal_SetEntries
};

/* ====================================================== IDirectDrawClipper */

static HRESULT WINAPI Clip_QueryInterface(IDirectDrawClipper *me, REFIID riid, void **out)
{ TRACE(); (void)riid; if (!out) return E_POINTER; *out = me; ((ClipImpl *)me)->ref++; return S_OK; }
static ULONG WINAPI Clip_AddRef(IDirectDrawClipper *me) { TRACE(); return ++((ClipImpl *)me)->ref; }
static ULONG WINAPI Clip_Release(IDirectDrawClipper *me)
{ TRACE();
    ClipImpl *c = (ClipImpl *)me;
    LONG r = --c->ref;
    if (r <= 0) { free(c); return 0; }
    return r;
}
static HRESULT WINAPI Clip_GetClipList(IDirectDrawClipper *me, LPRECT r,
                                       LPRGNDATA data, LPDWORD size)
{ TRACE(); (void)me; (void)r; (void)data; (void)size; return DDERR_NOCLIPLIST; }
static HRESULT WINAPI Clip_GetHWnd(IDirectDrawClipper *me, HWND *out)
{ TRACE(); if (!out) return DDERR_INVALIDPARAMS; *out = ((ClipImpl *)me)->hwnd; return DD_OK; }
static HRESULT WINAPI Clip_Initialize(IDirectDrawClipper *me, LPDIRECTDRAW dd, DWORD flags)
{ TRACE(); (void)me; (void)dd; (void)flags; return DDERR_ALREADYINITIALIZED; }
static HRESULT WINAPI Clip_IsClipListChanged(IDirectDrawClipper *me, WINBOOL *changed)
{ TRACE(); (void)me; if (changed) *changed = FALSE; return DD_OK; }
static HRESULT WINAPI Clip_SetClipList(IDirectDrawClipper *me, LPRGNDATA data, DWORD flags)
{ TRACE(); (void)me; (void)data; (void)flags; return DD_OK; }
static HRESULT WINAPI Clip_SetHWnd(IDirectDrawClipper *me, DWORD flags, HWND hwnd)
{ TRACE(); (void)flags; ((ClipImpl *)me)->hwnd = hwnd; return DD_OK; }

static const IDirectDrawClipperVtbl g_clip_vtbl = {
    Clip_QueryInterface, Clip_AddRef, Clip_Release,
    Clip_GetClipList, Clip_GetHWnd, Clip_Initialize,
    Clip_IsClipListChanged, Clip_SetClipList, Clip_SetHWnd
};

/* ====================================================== IDirectDrawSurface */

static HRESULT WINAPI Surf_QueryInterface(IDirectDrawSurface *me, REFIID riid, void **out)
{ TRACE(); (void)riid; if (!out) return E_POINTER; *out = me; ((SurfImpl *)me)->ref++; return S_OK; }
static ULONG WINAPI Surf_AddRef(IDirectDrawSurface *me) { TRACE(); return ++((SurfImpl *)me)->ref; }
static ULONG WINAPI Surf_Release(IDirectDrawSurface *me)
{ TRACE();
    SurfImpl *s = (SurfImpl *)me;
    LONG r = --s->ref;
    if (r > 0) return r;
    if (g_onscreen == s) g_onscreen = NULL;
    if (s->backbuffer) Surf_Release((IDirectDrawSurface *)s->backbuffer);
    if (s->dc) { SelectObject(s->dc, s->olddib); DeleteObject(s->dib); DeleteDC(s->dc); }
    if (s->pal)  Pal_Release((IDirectDrawPalette *)s->pal);
    if (s->clip) Clip_Release((IDirectDrawClipper *)s->clip);
    free(s->bits);
    free(s);
    return 0;
}

static HRESULT WINAPI Surf_AddAttachedSurface(IDirectDrawSurface *me, LPDIRECTDRAWSURFACE a)
{ TRACE(); (void)me; (void)a; return DDERR_CANNOTATTACHSURFACE; }
static HRESULT WINAPI Surf_AddOverlayDirtyRect(IDirectDrawSurface *me, LPRECT r)
{ TRACE(); (void)me; (void)r; return DDERR_UNSUPPORTED; }

/* Not reached by this game (no Blt call sites), but implemented rather than
 * stubbed so a variant build cannot silently corrupt the screen. */
static HRESULT WINAPI Surf_Blt(IDirectDrawSurface *me, LPRECT dst, LPDIRECTDRAWSURFACE src,
                               LPRECT srcr, DWORD flags, LPDDBLTFX fx)
{ TRACE();
    SurfImpl *d = (SurfImpl *)me, *s = (SurfImpl *)src;
    int x, y, w, h, dx = 0, dy = 0;
    if ((flags & DDBLT_COLORFILL) && fx) {
        BYTE c = (BYTE)fx->dwFillColor;
        int x0 = dst ? dst->left : 0,  y0 = dst ? dst->top : 0;
        int x1 = dst ? dst->right : d->w, y1 = dst ? dst->bottom : d->h;
        if (x1 > d->w) x1 = d->w;
        if (y1 > d->h) y1 = d->h;
        for (y = y0; y < y1; y++)
            memset(d->bits + (SIZE_T)y * d->pitch + (SIZE_T)x0 * (d->bpp / 8 ? d->bpp / 8 : 1),
                   c, (SIZE_T)(x1 - x0) * (d->bpp / 8 ? d->bpp / 8 : 1));
        return DD_OK;
    }
    if (!s) return DDERR_INVALIDPARAMS;
    w = srcr ? srcr->right - srcr->left : s->w;
    h = srcr ? srcr->bottom - srcr->top : s->h;
    if (dst) { dx = dst->left; dy = dst->top; }
    for (y = 0; y < h; y++) {
        int sy = (srcr ? srcr->top : 0) + y, ty = dy + y;
        if (ty < 0 || ty >= d->h || sy < 0 || sy >= s->h) continue;
        for (x = 0; x < w; x++) {
            int sx = (srcr ? srcr->left : 0) + x, tx = dx + x;
            if (tx < 0 || tx >= d->w || sx < 0 || sx >= s->w) continue;
            {
                int bp = d->bpp / 8 ? d->bpp / 8 : 1;
                memcpy(d->bits + (SIZE_T)ty * d->pitch + (SIZE_T)tx * bp,
                       s->bits + (SIZE_T)sy * s->pitch + (SIZE_T)sx * bp, (SIZE_T)bp);
            }
        }
    }
    return DD_OK;
}
static HRESULT WINAPI Surf_BltBatch(IDirectDrawSurface *me, LPDDBLTBATCH b, DWORD n, DWORD f)
{ TRACE(); (void)me; (void)b; (void)n; (void)f; return DDERR_UNSUPPORTED; }

static HRESULT WINAPI Surf_BltFast(IDirectDrawSurface *me, DWORD x, DWORD y,
                                   LPDIRECTDRAWSURFACE src, LPRECT srcr, DWORD trans)
{ TRACE();
    RECT d;
    SurfImpl *s = (SurfImpl *)src;
    if (!s) return DDERR_INVALIDPARAMS;
    d.left = (LONG)x; d.top = (LONG)y;
    d.right  = (LONG)x + (srcr ? srcr->right - srcr->left : s->w);
    d.bottom = (LONG)y + (srcr ? srcr->bottom - srcr->top : s->h);
    (void)trans;
    return Surf_Blt(me, &d, src, srcr, 0, NULL);
}

static HRESULT WINAPI Surf_DeleteAttachedSurface(IDirectDrawSurface *me, DWORD f,
                                                 LPDIRECTDRAWSURFACE a)
{ TRACE(); (void)me; (void)f; (void)a; return DD_OK; }
static HRESULT WINAPI Surf_EnumAttachedSurfaces(IDirectDrawSurface *me, LPVOID ctx,
                                                LPDDENUMSURFACESCALLBACK cb)
{ TRACE(); (void)me; (void)ctx; (void)cb; return DD_OK; }
static HRESULT WINAPI Surf_EnumOverlayZOrders(IDirectDrawSurface *me, DWORD f, LPVOID ctx,
                                              LPDDENUMSURFACESCALLBACK cb)
{ TRACE(); (void)me; (void)f; (void)ctx; (void)cb; return DDERR_UNSUPPORTED; }

/* The present.  The game loops on DDERR_WASSTILLDRAWING, so we must never
 * return it - we always complete. */
static HRESULT WINAPI Surf_Flip(IDirectDrawSurface *me, LPDIRECTDRAWSURFACE override,
                                DWORD flags)
{ TRACE();
    SurfImpl *self = (SurfImpl *)me;
    SurfImpl *src  = override ? (SurfImpl *)override : self->backbuffer;
    (void)flags;
    if (!src) src = self;
    TRACE_N(g_trace_flip, "Flip self=%p override=%p -> present %p",
            (void *)self, (void *)override, (void *)src);
    present_this(src);
    return DD_OK;
}

static HRESULT WINAPI Surf_GetAttachedSurface(IDirectDrawSurface *me, LPDDSCAPS caps,
                                              LPDIRECTDRAWSURFACE *out)
{ TRACE();
    SurfImpl *s = (SurfImpl *)me;
    SurfImpl *r = NULL;
    if (!out || !caps) return DDERR_INVALIDPARAMS;
    if (caps->dwCaps & DDSCAPS_BACKBUFFER) r = s->backbuffer;
    else if (caps->dwCaps & DDSCAPS_FLIP)  r = s->flipnext;
    if (!r) return DDERR_NOTFOUND;
    r->ref++;
    *out = (IDirectDrawSurface *)r;
    return DD_OK;
}

static HRESULT WINAPI Surf_GetBltStatus(IDirectDrawSurface *me, DWORD f)
{ TRACE(); (void)me; (void)f; return DD_OK; }

/* Quirk (3): the game refuses to present unless DDSCAPS_FLIP is reported. */
static HRESULT WINAPI Surf_GetCaps(IDirectDrawSurface *me, LPDDSCAPS caps)
{ TRACE();
    if (!caps) return DDERR_INVALIDPARAMS;
    caps->dwCaps = ((SurfImpl *)me)->caps;
    return DD_OK;
}

static HRESULT WINAPI Surf_GetClipper(IDirectDrawSurface *me, LPDIRECTDRAWCLIPPER *out)
{ TRACE();
    SurfImpl *s = (SurfImpl *)me;
    if (!out) return DDERR_INVALIDPARAMS;
    if (!s->clip) return DDERR_NOCLIPPERATTACHED;
    s->clip->ref++;
    *out = (IDirectDrawClipper *)s->clip;
    return DD_OK;
}
static HRESULT WINAPI Surf_GetColorKey(IDirectDrawSurface *me, DWORD f, LPDDCOLORKEY k)
{ TRACE(); (void)me; (void)f; (void)k; return DDERR_NOCOLORKEY; }
/* The game really does draw through a surface DC (contrary to a first reading
 * of the disassembly, which found no GetDC call sites).  Returning
 * DDERR_UNSUPPORTED here makes it abort startup silently, so implement it.
 *
 * We back the DC with a DIB section rather than handing GDI our own buffer,
 * because that buffer is deliberately over-allocated for the clear-helper
 * quirk and a DIB cannot be.  Contents are copied in on GetDC and out on
 * ReleaseDC; these calls are rare (menus, text), not per-frame. */
static HRESULT WINAPI Surf_GetDC(IDirectDrawSurface *me, HDC *lphDC)
{
    SurfImpl *s = (SurfImpl *)me;
    struct { BITMAPINFOHEADER h; RGBQUAD pal[256]; } bi;
    int bytespp = s->bpp / 8 ? s->bpp / 8 : 1;
    int y, rowbytes;
    TRACE();
    if (!lphDC) return DDERR_INVALIDPARAMS;
    *lphDC = NULL;
    if (s->dc) { *lphDC = s->dc; return DD_OK; }   /* already held */

    memset(&bi, 0, sizeof bi);
    bi.h.biSize        = sizeof(BITMAPINFOHEADER);
    bi.h.biWidth       = s->w;
    bi.h.biHeight      = -s->h;            /* top-down, matching our layout */
    bi.h.biPlanes      = 1;
    bi.h.biBitCount    = (WORD)s->bpp;
    bi.h.biCompression = BI_RGB;
    if (s->bpp <= 8) {
        int i;
        bi.h.biClrUsed = 256;
        for (i = 0; i < 256; i++) {
            uint32_t c = s->pal ? s->pal->bgrx[i] : (g_active_pal ? g_active_pal->bgrx[i] : 0);
            bi.pal[i].rgbRed   = (BYTE)(c >> 16);
            bi.pal[i].rgbGreen = (BYTE)(c >> 8);
            bi.pal[i].rgbBlue  = (BYTE)c;
        }
    }

    s->dc = CreateCompatibleDC(NULL);
    if (!s->dc) return DDERR_GENERIC;
    s->dib = CreateDIBSection(s->dc, (BITMAPINFO *)&bi, DIB_RGB_COLORS,
                              &s->dibbits, NULL, 0);
    if (!s->dib) { DeleteDC(s->dc); s->dc = NULL; return DDERR_GENERIC; }
    s->olddib   = SelectObject(s->dc, s->dib);
    s->dibpitch = (LONG)(((s->w * bytespp) + 3) & ~3);   /* DIB rows are DWORD aligned */

    rowbytes = s->w * bytespp;
    for (y = 0; y < s->h; y++)
        memcpy((BYTE *)s->dibbits + (SIZE_T)y * s->dibpitch,
               s->bits + (SIZE_T)y * s->pitch, (SIZE_T)rowbytes);

    *lphDC = s->dc;
    return DD_OK;
}
static HRESULT WINAPI Surf_GetFlipStatus(IDirectDrawSurface *me, DWORD f)
{ TRACE(); (void)me; (void)f; return DD_OK; }
static HRESULT WINAPI Surf_GetOverlayPosition(IDirectDrawSurface *me, LPLONG x, LPLONG y)
{ TRACE(); (void)me; (void)x; (void)y; return DDERR_NOTAOVERLAYSURFACE; }
static HRESULT WINAPI Surf_GetPalette(IDirectDrawSurface *me, LPDIRECTDRAWPALETTE *out)
{ TRACE();
    SurfImpl *s = (SurfImpl *)me;
    if (!out) return DDERR_INVALIDPARAMS;
    if (!s->pal) return DDERR_NOPALETTEATTACHED;
    s->pal->ref++;
    *out = (IDirectDrawPalette *)s->pal;
    return DD_OK;
}
static HRESULT WINAPI Surf_GetPixelFormat(IDirectDrawSurface *me, LPDDPIXELFORMAT pf)
{ TRACE();
    if (!pf) return DDERR_INVALIDPARAMS;
    {
        SurfImpl *s = (SurfImpl *)me;
        memset(pf, 0, sizeof *pf);
        pf->dwSize        = sizeof(DDPIXELFORMAT);
        pf->dwRGBBitCount = (DWORD)s->bpp;
        if (s->bpp <= 8) {
            pf->dwFlags = DDPF_RGB | DDPF_PALETTEINDEXED8;
        } else if (s->bpp == 16) {
            pf->dwFlags = DDPF_RGB;
            pf->dwRBitMask = 0xF800; pf->dwGBitMask = 0x07E0; pf->dwBBitMask = 0x001F;
        } else {
            pf->dwFlags = DDPF_RGB;
            pf->dwRBitMask = 0x00FF0000; pf->dwGBitMask = 0x0000FF00;
            pf->dwBBitMask = 0x000000FF;
        }
    }
    return DD_OK;
}
static HRESULT WINAPI Surf_GetSurfaceDesc(IDirectDrawSurface *me, LPDDSURFACEDESC d)
{ TRACE();
    SurfImpl *s = (SurfImpl *)me;
    if (!d) return DDERR_INVALIDPARAMS;
    memset(d, 0, sizeof *d);
    d->dwSize   = sizeof(DDSURFACEDESC);
    d->dwFlags  = DDSD_WIDTH | DDSD_HEIGHT | DDSD_PITCH | DDSD_CAPS | DDSD_PIXELFORMAT;
    d->dwWidth  = (DWORD)s->w;
    d->dwHeight = (DWORD)s->h;
    d->lPitch   = s->pitch;
    d->ddsCaps.dwCaps = s->caps;
    Surf_GetPixelFormat(me, &d->ddpfPixelFormat);
    return DD_OK;
}
static HRESULT WINAPI Surf_Initialize(IDirectDrawSurface *me, LPDIRECTDRAW dd,
                                      LPDDSURFACEDESC d)
{ TRACE(); (void)me; (void)dd; (void)d; return DDERR_ALREADYINITIALIZED; }

/* We never lose surfaces - there is no exclusive mode to lose them to. */
static HRESULT WINAPI Surf_IsLost(IDirectDrawSurface *me) { TRACE(); (void)me; return DD_OK; }

static HRESULT WINAPI Surf_Lock(IDirectDrawSurface *me, LPRECT rect, LPDDSURFACEDESC d,
                                DWORD flags, HANDLE ev)
{ TRACE();
    SurfImpl *s = (SurfImpl *)me;
    (void)flags; (void)ev;
    if (!d) return DDERR_INVALIDPARAMS;
    /* The game only reads lPitch and lpSurface back, but fill the descriptor
     * properly anyway - it costs nothing and keeps this honest. */
    memset(d, 0, sizeof(DDSURFACEDESC));
    d->dwSize   = sizeof(DDSURFACEDESC);
    d->dwFlags  = DDSD_WIDTH | DDSD_HEIGHT | DDSD_PITCH | DDSD_LPSURFACE | DDSD_CAPS;
    d->dwWidth  = (DWORD)s->w;
    d->dwHeight = (DWORD)s->h;
    d->lPitch   = s->pitch;
    d->ddsCaps.dwCaps = s->caps;
    d->lpSurface = rect
        ? s->bits + (SIZE_T)rect->top * s->pitch
                  + (SIZE_T)rect->left * (s->bpp / 8 ? s->bpp / 8 : 1)
        : s->bits;
    s->locked = 1;
    TRACE_N(g_trace_lock, "Lock surf=%p %dx%d bpp=%d pitch=%ld -> %p",
            (void *)s, s->w, s->h, s->bpp, (long)s->pitch, d->lpSurface);
    return DD_OK;
}

/* Quirk (1): the pointer argument is garbage.  Ignore it entirely. */
static HRESULT WINAPI Surf_Unlock(IDirectDrawSurface *me, LPVOID ignored)
{ TRACE();
    (void)ignored;
    ((SurfImpl *)me)->locked = 0;
    return DD_OK;
}

static HRESULT WINAPI Surf_ReleaseDC(IDirectDrawSurface *me, HDC dc)
{
    SurfImpl *s = (SurfImpl *)me;
    int bytespp = s->bpp / 8 ? s->bpp / 8 : 1;
    int y, rowbytes;
    TRACE();
    (void)dc;
    if (!s->dc) return DD_OK;
    GdiFlush();
    rowbytes = s->w * bytespp;
    for (y = 0; y < s->h; y++)
        memcpy(s->bits + (SIZE_T)y * s->pitch,
               (BYTE *)s->dibbits + (SIZE_T)y * s->dibpitch, (SIZE_T)rowbytes);
    SelectObject(s->dc, s->olddib);
    DeleteObject(s->dib);
    DeleteDC(s->dc);
    s->dc = NULL; s->dib = NULL; s->dibbits = NULL;
    /* If this was the visible surface, show what GDI just drew. */
    if (s == g_onscreen) present_this(s);
    return DD_OK;
}
static HRESULT WINAPI Surf_Restore(IDirectDrawSurface *me) { TRACE(); (void)me; return DD_OK; }
static HRESULT WINAPI Surf_SetClipper(IDirectDrawSurface *me, LPDIRECTDRAWCLIPPER c)
{ TRACE();
    SurfImpl *s = (SurfImpl *)me;
    if (s->clip) Clip_Release((IDirectDrawClipper *)s->clip);
    s->clip = (ClipImpl *)c;
    if (s->clip) s->clip->ref++;
    return DD_OK;
}
static HRESULT WINAPI Surf_SetColorKey(IDirectDrawSurface *me, DWORD f, LPDDCOLORKEY k)
{ TRACE(); (void)me; (void)f; (void)k; return DD_OK; }
static HRESULT WINAPI Surf_SetOverlayPosition(IDirectDrawSurface *me, LONG x, LONG y)
{ TRACE(); (void)me; (void)x; (void)y; return DDERR_NOTAOVERLAYSURFACE; }

static HRESULT WINAPI Surf_SetPalette(IDirectDrawSurface *me, LPDIRECTDRAWPALETTE p)
{ TRACE();
    SurfImpl *s = (SurfImpl *)me;
    if (s->pal) Pal_Release((IDirectDrawPalette *)s->pal);
    s->pal = (PalImpl *)p;
    if (s->pal) {
        s->pal->ref++;
        /* The game attaches the same logical palette to primary and offscreen
         * surfaces alike; the most recent one is the live one. */
        g_active_pal = s->pal;
        present_set_palette(s->pal->bgrx);
        present_this(g_onscreen);
    }
    return DD_OK;
}

static HRESULT WINAPI Surf_UpdateOverlay(IDirectDrawSurface *me, LPRECT sr,
                                         LPDIRECTDRAWSURFACE d, LPRECT dr, DWORD f,
                                         LPDDOVERLAYFX fx)
{ TRACE(); (void)me; (void)sr; (void)d; (void)dr; (void)f; (void)fx; return DDERR_UNSUPPORTED; }
static HRESULT WINAPI Surf_UpdateOverlayDisplay(IDirectDrawSurface *me, DWORD f)
{ TRACE(); (void)me; (void)f; return DDERR_UNSUPPORTED; }
static HRESULT WINAPI Surf_UpdateOverlayZOrder(IDirectDrawSurface *me, DWORD f,
                                               LPDIRECTDRAWSURFACE r)
{ TRACE(); (void)me; (void)f; (void)r; return DDERR_UNSUPPORTED; }

static const IDirectDrawSurfaceVtbl g_surf_vtbl = {
    Surf_QueryInterface, Surf_AddRef, Surf_Release,
    Surf_AddAttachedSurface, Surf_AddOverlayDirtyRect, Surf_Blt, Surf_BltBatch,
    Surf_BltFast, Surf_DeleteAttachedSurface, Surf_EnumAttachedSurfaces,
    Surf_EnumOverlayZOrders, Surf_Flip, Surf_GetAttachedSurface, Surf_GetBltStatus,
    Surf_GetCaps, Surf_GetClipper, Surf_GetColorKey, Surf_GetDC, Surf_GetFlipStatus,
    Surf_GetOverlayPosition, Surf_GetPalette, Surf_GetPixelFormat, Surf_GetSurfaceDesc,
    Surf_Initialize, Surf_IsLost, Surf_Lock, Surf_ReleaseDC, Surf_Restore,
    Surf_SetClipper, Surf_SetColorKey, Surf_SetOverlayPosition, Surf_SetPalette,
    Surf_Unlock, Surf_UpdateOverlay, Surf_UpdateOverlayDisplay, Surf_UpdateOverlayZOrder
};

static SurfImpl *surf_new(DDImpl *dd, int w, int h, DWORD caps)
{
    SurfImpl *s = (SurfImpl *)calloc(1, sizeof *s);
    if (!s) return NULL;
    s->lpVtbl = &g_surf_vtbl;
    s->ref    = 1;
    s->dd     = dd;
    s->w      = w;
    s->h      = h;
    s->bpp    = dd->bpp > 0 ? dd->bpp : 8;
    s->caps   = caps;
    s->bytes  = surface_bytes(w, h, s->bpp, &s->pitch);
    s->bits   = (BYTE *)calloc(1, s->bytes);
    if (!s->bits) { free(s); return NULL; }
    return s;
}

/* ============================================================= IDirectDraw */

static HRESULT WINAPI DD_QueryInterface(IDirectDraw *me, REFIID riid, void **out)
{ TRACE(); (void)riid; if (!out) return E_POINTER; *out = me; ((DDImpl *)me)->ref++; return S_OK; }
static ULONG WINAPI DD_AddRef(IDirectDraw *me) { TRACE(); return ++((DDImpl *)me)->ref; }
static ULONG WINAPI DD_Release(IDirectDraw *me)
{ TRACE();
    DDImpl *dd = (DDImpl *)me;
    LONG r = --dd->ref;
    if (r > 0) return r;
    if (dd->present_up) { present_shutdown(); dd->present_up = 0; }
    IGNLOG("IDirectDraw released");
    free(dd);
    return 0;
}
static HRESULT WINAPI DD_Compact(IDirectDraw *me) { TRACE(); (void)me; return DD_OK; }

static HRESULT WINAPI DD_CreateClipper(IDirectDraw *me, DWORD flags,
                                       LPDIRECTDRAWCLIPPER *out, IUnknown *outer)
{ TRACE();
    ClipImpl *c;
    (void)me; (void)flags; (void)outer;
    if (!out) return DDERR_INVALIDPARAMS;
    c = (ClipImpl *)calloc(1, sizeof *c);
    if (!c) return DDERR_OUTOFMEMORY;
    c->lpVtbl = &g_clip_vtbl;
    c->ref    = 1;
    *out = (IDirectDrawClipper *)c;
    return DD_OK;
}

static HRESULT WINAPI DD_CreatePalette(IDirectDraw *me, DWORD flags, LPPALETTEENTRY seed,
                                       LPDIRECTDRAWPALETTE *out, IUnknown *outer)
{ TRACE();
    PalImpl *p;
    int i;
    (void)me; (void)outer;
    if (!out) return DDERR_INVALIDPARAMS;
    p = (PalImpl *)calloc(1, sizeof *p);
    if (!p) return DDERR_OUTOFMEMORY;
    p->lpVtbl = &g_pal_vtbl;
    p->ref    = 1;
    p->caps   = flags;
    for (i = 0; i < 256; i++) p->bgrx[i] = 0xFF000000u;
    if (seed) Pal_SetEntries((IDirectDrawPalette *)p, 0, 0, 256, seed);
    IGNLOG("CreatePalette flags=0x%lX", (unsigned long)flags);
    *out = (IDirectDrawPalette *)p;
    return DD_OK;
}

static HRESULT WINAPI DD_CreateSurface(IDirectDraw *me, LPDDSURFACEDESC d,
                                       LPDIRECTDRAWSURFACE *out, IUnknown *outer)
{ TRACE();
    DDImpl *dd = (DDImpl *)me;
    SurfImpl *s;
    DWORD caps;
    int w, h;
    (void)outer;

    if (!d || !out) return DDERR_INVALIDPARAMS;
    if (FAILED(ensure_present(dd))) return DDERR_GENERIC;

    caps = (d->dwFlags & DDSD_CAPS) ? d->ddsCaps.dwCaps : 0;
    w = (d->dwFlags & DDSD_WIDTH)  ? (int)d->dwWidth  : dd->w;
    h = (d->dwFlags & DDSD_HEIGHT) ? (int)d->dwHeight : dd->h;
    if (w <= 0) w = dd->w ? dd->w : 640;
    if (h <= 0) h = dd->h ? dd->h : 480;

    if (caps & DDSCAPS_PRIMARYSURFACE) {
        DWORD nback = (d->dwFlags & DDSD_BACKBUFFERCOUNT) ? d->dwBackBufferCount : 0;
        s = surf_new(dd, dd->w, dd->h, caps | DDSCAPS_VIDEOMEMORY);
        if (!s) return DDERR_OUTOFMEMORY;
        if (nback > 0) {
            /* Quirk (3): the back buffer must advertise DDSCAPS_FLIP or the
             * game's present wrapper bails out before ever calling Flip. */
            SurfImpl *bb = surf_new(dd, dd->w, dd->h,
                                    DDSCAPS_BACKBUFFER | DDSCAPS_FLIP |
                                    DDSCAPS_COMPLEX | DDSCAPS_VIDEOMEMORY);
            if (!bb) { Surf_Release((IDirectDrawSurface *)s); return DDERR_OUTOFMEMORY; }
            s->backbuffer = bb;
            s->flipnext   = bb;
            bb->flipnext  = s;
            s->caps      |= DDSCAPS_FLIP | DDSCAPS_COMPLEX;
        }
        IGNLOG("CreateSurface PRIMARY %dx%d backbuffers=%lu caps=0x%lX",
               dd->w, dd->h, (unsigned long)nback, (unsigned long)s->caps);
    } else {
        s = surf_new(dd, w, h, caps | DDSCAPS_SYSTEMMEMORY);
        if (!s) return DDERR_OUTOFMEMORY;
        IGNLOG("CreateSurface OFFSCREEN %dx%d caps=0x%lX", w, h, (unsigned long)caps);
    }
    *out = (IDirectDrawSurface *)s;
    return DD_OK;
}

static HRESULT WINAPI DD_DuplicateSurface(IDirectDraw *me, LPDIRECTDRAWSURFACE in,
                                          LPDIRECTDRAWSURFACE *out)
{ TRACE(); (void)me; (void)in; (void)out; return DDERR_UNSUPPORTED; }

static HRESULT WINAPI DD_EnumDisplayModes(IDirectDraw *me, DWORD flags, LPDDSURFACEDESC d,
                                          LPVOID ctx, LPDDENUMMODESCALLBACK cb)
{ TRACE();
    /* The three modes this game ever asks for. */
    static const int modes[][2] = { { 320, 200 }, { 640, 480 }, { 800, 600 } };
    DDSURFACEDESC sd;
    size_t i;
    (void)me; (void)flags; (void)d;
    if (!cb) return DDERR_INVALIDPARAMS;
    for (i = 0; i < sizeof modes / sizeof modes[0]; i++) {
        memset(&sd, 0, sizeof sd);
        sd.dwSize   = sizeof sd;
        sd.dwFlags  = DDSD_WIDTH | DDSD_HEIGHT | DDSD_PIXELFORMAT | DDSD_PITCH;
        sd.dwWidth  = (DWORD)modes[i][0];
        sd.dwHeight = (DWORD)modes[i][1];
        sd.lPitch   = (LONG)((modes[i][0] + 31) & ~31);
        sd.ddpfPixelFormat.dwSize        = sizeof(DDPIXELFORMAT);
        sd.ddpfPixelFormat.dwFlags       = DDPF_RGB | DDPF_PALETTEINDEXED8;
        sd.ddpfPixelFormat.dwRGBBitCount = 8;
        if (cb(&sd, ctx) == DDENUMRET_CANCEL) break;
    }
    return DD_OK;
}

static HRESULT WINAPI DD_EnumSurfaces(IDirectDraw *me, DWORD f, LPDDSURFACEDESC d,
                                      LPVOID ctx, LPDDENUMSURFACESCALLBACK cb)
{ TRACE(); (void)me; (void)f; (void)d; (void)ctx; (void)cb; return DD_OK; }
static HRESULT WINAPI DD_FlipToGDISurface(IDirectDraw *me) { TRACE(); (void)me; return DD_OK; }

static HRESULT WINAPI DD_GetCaps(IDirectDraw *me, LPDDCAPS drv, LPDDCAPS hel)
{ TRACE();
    (void)me;
    if (drv) {
        DWORD sz = drv->dwSize ? drv->dwSize : sizeof(DDCAPS);
        memset(drv, 0, sz);
        drv->dwSize  = sz;
        drv->dwCaps  = DDCAPS_BLT | DDCAPS_BLTCOLORFILL | DDCAPS_PALETTE;
        drv->dwVidMemTotal = 64u * 1024u * 1024u;
        drv->dwVidMemFree  = 48u * 1024u * 1024u;
    }
    if (hel) { DWORD sz = hel->dwSize ? hel->dwSize : sizeof(DDCAPS); memset(hel, 0, sz); hel->dwSize = sz; }
    return DD_OK;
}

static HRESULT WINAPI DD_GetDisplayMode(IDirectDraw *me, LPDDSURFACEDESC d)
{ TRACE();
    DDImpl *dd = (DDImpl *)me;
    if (!d) return DDERR_INVALIDPARAMS;
    memset(d, 0, sizeof *d);
    d->dwSize   = sizeof(DDSURFACEDESC);
    d->dwFlags  = DDSD_WIDTH | DDSD_HEIGHT | DDSD_PITCH | DDSD_PIXELFORMAT;
    d->dwWidth  = (DWORD)dd->w;
    d->dwHeight = (DWORD)dd->h;
    {
        int bytespp = dd->bpp / 8; if (bytespp < 1) bytespp = 1;
        d->lPitch = (LONG)(((dd->w * bytespp) + 31) & ~31);
    }
    d->ddpfPixelFormat.dwSize        = sizeof(DDPIXELFORMAT);
    d->ddpfPixelFormat.dwRGBBitCount = (DWORD)dd->bpp;
    d->ddpfPixelFormat.dwFlags       = (dd->bpp <= 8) ? (DDPF_RGB | DDPF_PALETTEINDEXED8)
                                                      : DDPF_RGB;
    return DD_OK;
}

static HRESULT WINAPI DD_GetFourCCCodes(IDirectDraw *me, LPDWORD n, LPDWORD c)
{ TRACE(); (void)me; (void)c; if (n) *n = 0; return DD_OK; }
static HRESULT WINAPI DD_GetGDISurface(IDirectDraw *me, LPDIRECTDRAWSURFACE *out)
{ TRACE(); (void)me; (void)out; return DDERR_NOTFOUND; }
static HRESULT WINAPI DD_GetMonitorFrequency(IDirectDraw *me, LPDWORD f)
{ TRACE(); (void)me; if (f) *f = 60; return DD_OK; }
static HRESULT WINAPI DD_GetScanLine(IDirectDraw *me, LPDWORD s)
{ TRACE(); (void)me; if (s) *s = 0; return DD_OK; }
static HRESULT WINAPI DD_GetVerticalBlankStatus(IDirectDraw *me, WINBOOL *b)
{ TRACE(); (void)me; if (b) *b = FALSE; return DD_OK; }
static HRESULT WINAPI DD_Initialize(IDirectDraw *me, GUID *g)
{ TRACE(); (void)me; (void)g; return DDERR_ALREADYINITIALIZED; }
static HRESULT WINAPI DD_RestoreDisplayMode(IDirectDraw *me) { TRACE(); (void)me; return DD_OK; }

static HRESULT WINAPI DD_SetCooperativeLevel(IDirectDraw *me, HWND hwnd, DWORD flags)
{ TRACE();
    DDImpl *dd = (DDImpl *)me;
    dd->hwnd = hwnd;
    dd->coop = flags;
    IGNLOG("SetCooperativeLevel hwnd=%p flags=0x%lX%s", (void *)hwnd,
           (unsigned long)flags, (flags & DDSCL_EXCLUSIVE) ? " (exclusive->borderless)" : "");
    return ensure_present(dd) == S_OK ? DD_OK : DD_OK;
}

/* We accept the mode but never actually change the display.  The desktop stays
 * at its native resolution and the frame is scaled on the GPU - which is the
 * whole point, since it is the real mode-set that turns the screen black. */
static HRESULT WINAPI DD_SetDisplayMode(IDirectDraw *me, DWORD w, DWORD h, DWORD bpp)
{ TRACE();
    DDImpl *dd = (DDImpl *)me;
    IGNLOG("SetDisplayMode %lux%lux%lu (virtualised)",
           (unsigned long)w, (unsigned long)h, (unsigned long)bpp);
    dd->w = (int)w; dd->h = (int)h;
    dd->bpp = g_force_bpp ? g_force_bpp : ((int)bpp ? (int)bpp : 8);
    if (g_force_bpp) IGNLOG("  bpp overridden to %d by config", g_force_bpp);
    if (dd->present_up) present_set_mode((int)w, (int)h, fmt_for_bpp(dd->bpp));
    else ensure_present(dd);
    return DD_OK;
}

static HRESULT WINAPI DD_WaitForVerticalBlank(IDirectDraw *me, DWORD f, HANDLE e)
{ TRACE(); (void)me; (void)f; (void)e; return DD_OK; }

static const IDirectDrawVtbl g_dd_vtbl = {
    DD_QueryInterface, DD_AddRef, DD_Release, DD_Compact,
    DD_CreateClipper, DD_CreatePalette, DD_CreateSurface, DD_DuplicateSurface,
    DD_EnumDisplayModes, DD_EnumSurfaces, DD_FlipToGDISurface, DD_GetCaps,
    DD_GetDisplayMode, DD_GetFourCCCodes, DD_GetGDISurface, DD_GetMonitorFrequency,
    DD_GetScanLine, DD_GetVerticalBlankStatus, DD_Initialize, DD_RestoreDisplayMode,
    DD_SetCooperativeLevel, DD_SetDisplayMode, DD_WaitForVerticalBlank
};

/* ================================================================ exports */

HRESULT WINAPI DirectDrawCreate(GUID *guid, LPDIRECTDRAW *out, IUnknown *outer)
{
    DDImpl *dd;
    (void)guid; (void)outer;
    if (!out) return DDERR_INVALIDPARAMS;
    {   /* first call: safe point to install the IAT hooks */
        static int hooked;
        if (!hooked) { hooked = 1; install_hooks(); }
    }
    dd = (DDImpl *)calloc(1, sizeof *dd);
    if (!dd) return DDERR_OUTOFMEMORY;
    dd->lpVtbl = &g_dd_vtbl;
    dd->ref    = 1;
    *out = (IDirectDraw *)dd;
    IGNLOG("DirectDrawCreate -> %p", (void *)dd);
    return DD_OK;
}

HRESULT WINAPI DirectDrawCreateEx(GUID *guid, void **out, REFIID riid, IUnknown *outer)
{ (void)riid; return DirectDrawCreate(guid, (LPDIRECTDRAW *)out, outer); }

HRESULT WINAPI DirectDrawEnumerateA(LPDDENUMCALLBACKA cb, LPVOID ctx)
{
    if (cb) cb(NULL, (LPSTR)"Ignition D3D11 Compatibility Layer", (LPSTR)"display", ctx);
    return DD_OK;
}
HRESULT WINAPI DirectDrawEnumerateW(LPDDENUMCALLBACKW cb, LPVOID ctx)
{
    if (cb) cb(NULL, (LPWSTR)L"Ignition D3D11 Compatibility Layer", (LPWSTR)L"display", ctx);
    return DD_OK;
}
HRESULT WINAPI DirectDrawEnumerateExA(LPDDENUMCALLBACKEXA cb, LPVOID ctx, DWORD flags)
{
    (void)flags;
    if (cb) cb(NULL, (LPSTR)"Ignition D3D11 Compatibility Layer", (LPSTR)"display", ctx, NULL);
    return DD_OK;
}
HRESULT WINAPI DirectDrawEnumerateExW(LPDDENUMCALLBACKEXW cb, LPVOID ctx, DWORD flags)
{
    (void)flags;
    if (cb) cb(NULL, (LPWSTR)L"Ignition D3D11 Compatibility Layer", (LPWSTR)L"display", ctx, NULL);
    return DD_OK;
}
HRESULT WINAPI DllCanUnloadNow(void)  { return S_FALSE; }
HRESULT WINAPI DllGetClassObject(REFCLSID c, REFIID i, void **o)
{ (void)c; (void)i; if (o) *o = NULL; return CLASS_E_CLASSNOTAVAILABLE; }

/* ====================================================== IAT hooks ======= */

/* The game AVs inside winmmbase.dll as it enters a race.  It only touches two
 * risky WinMM areas: the legacy multimedia joystick API and MCI CD-audio.
 * Neither is needed - per the startup analysis there is no copy protection and
 * CD music is entirely optional - so both can be reported as absent. */
static MMRESULT WINAPI hook_joyGetDevCapsA(UINT_PTR id, LPJOYCAPSA caps, UINT cb)
{
    static int n;
    if (n++ < 3) IGNLOG("hook joyGetDevCapsA(id=%u cb=%u) -> NODRIVER", (unsigned)id, cb);
    if (caps && cb) ZeroMemory(caps, cb);
    return MMSYSERR_NODRIVER;
}

static MMRESULT WINAPI hook_joyGetPosEx(UINT id, LPJOYINFOEX pji)
{
    static int n;
    if (n++ < 3) IGNLOG("hook joyGetPosEx(id=%u) -> UNPLUGGED", id);
    (void)pji;
    return JOYERR_UNPLUGGED;
}

static MCIERROR WINAPI hook_mciSendStringA(LPCSTR cmd, LPSTR ret, UINT cch, HWND cb)
{
    static int n;
    (void)cb;
    if (n++ < 8) IGNLOG("hook mciSendStringA(\"%s\") -> no device", cmd ? cmd : "");
    if (ret && cch) ret[0] = '\0';
    return MCIERR_DEVICE_NOT_INSTALLED;
}

/* The main loop spins ~10^5 iterations/sec waiting out its own 36 FPS gate and
 * never calls Sleep (it does not even import it), so it pins a core.  Yielding
 * when there is no message costs nothing and caps the spin. */
static BOOL (WINAPI *real_PeekMessageA)(LPMSG, HWND, UINT, UINT, UINT);
static BOOL WINAPI hook_PeekMessageA(LPMSG m, HWND h, UINT f1, UINT f2, UINT rm)
{
    BOOL got = real_PeekMessageA ? real_PeekMessageA(m, h, f1, f2, rm) : FALSE;
    if (!got) Sleep(1);
    return got;
}

static void install_hooks(void)
{
    if (g_block_joy) {
        IGNLOG("hook joyGetDevCapsA: %s",
               iat_hook("WINMM.dll", "joyGetDevCapsA", (void *)hook_joyGetDevCapsA, NULL)
               ? "ok" : "NOT FOUND");
        IGNLOG("hook joyGetPosEx: %s",
               iat_hook("WINMM.dll", "joyGetPosEx", (void *)hook_joyGetPosEx, NULL)
               ? "ok" : "NOT FOUND");
    }
    if (g_block_mci) {
        IGNLOG("hook mciSendStringA: %s",
               iat_hook("WINMM.dll", "mciSendStringA", (void *)hook_mciSendStringA, NULL)
               ? "ok" : "NOT FOUND");
    }
    if (g_cpu_fix) {
        int ok = iat_hook("USER32.dll", "PeekMessageA", (void *)hook_PeekMessageA,
                          (void **)&real_PeekMessageA);
        IGNLOG("hook PeekMessageA (CPU fix): %s", ok ? "ok" : "NOT FOUND");
        timeBeginPeriod(1);
    }
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(inst);
        ign_log_init("ddraw");
        load_config();
        IGNLOG("Ignition ddraw shim attached (D3D11 backend)");
        IGNLOG("  pid=%lu exe=%s", (unsigned long)GetCurrentProcessId(), ini_path());
        IGNLOG("  cmdline=%s", GetCommandLineA());
        IGNLOG("  config: windowed=%d scale=%d scaling=%d filter=%s vsync=%d trace=%d",
               g_windowed, g_win_scale, (int)g_scaling,
               g_filter == 0 ? "point" : g_filter == 1 ? "linear" : "sharp",
               g_vsync, trace_on());
        IGNLOG("  fixes: block_joy=%d block_mci=%d cpu_fix=%d",
               g_block_joy, g_block_mci, g_cpu_fix);
        /* Hooks are installed from DirectDrawCreate, not here: DllMain runs
         * under the loader lock, and both patching the IAT and calling into
         * winmm from it can deadlock process startup. */
    } else if (reason == DLL_PROCESS_DETACH) {
        if (g_cpu_fix) timeEndPeriod(1);
        present_shutdown();
    }
    return TRUE;
}
