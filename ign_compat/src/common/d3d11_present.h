/* d3d11_present.h - modern-DirectX presentation backend for Ignition (1997)
 *
 * The game is a software rasterizer: it renders into a linear framebuffer and
 * hands it to DirectDraw to get on screen.  This module replaces that final
 * step with Direct3D 11.  It knows nothing about DirectDraw COM plumbing - it
 * takes a pixel buffer plus (for 8bpp) a palette, and presents it.
 *
 * Supported source formats mirror what a 1997 DirectDraw title can produce:
 *   PRESENT_FMT_P8      8bpp palettised, 256-entry BGRX palette
 *   PRESENT_FMT_RGB565  16bpp
 *   PRESENT_FMT_XRGB888 32bpp
 */
#ifndef IGN_D3D11_PRESENT_H
#define IGN_D3D11_PRESENT_H

#include <windows.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    PRESENT_FMT_P8 = 0,
    PRESENT_FMT_RGB565,
    PRESENT_FMT_XRGB888
} present_format_t;

typedef enum {
    PRESENT_SCALE_STRETCH = 0,  /* fill window, ignore aspect            */
    PRESENT_SCALE_ASPECT,       /* letterbox, preserve 4:3 source aspect */
    PRESENT_SCALE_INTEGER       /* largest whole-pixel multiple          */
} present_scale_t;

typedef struct {
    int             width, height;      /* source framebuffer size        */
    present_format_t format;
    present_scale_t scaling;
    int             filter_mode;        /* 0 = point, 1 = bilinear, 2 = sharp */
    int             vsync;
} present_config_t;

/* Lifecycle. present_init() creates the device+swapchain against hwnd. */
HRESULT present_init(HWND hwnd, const present_config_t *cfg);
void    present_shutdown(void);

/* Called when the game changes display mode (SetDisplayMode). */
HRESULT present_set_mode(int width, int height, present_format_t fmt);

/* Called when the host window is resized. */
void    present_resize(int client_w, int client_h);

/* Palette upload for PRESENT_FMT_P8. entries are 0x00RRGGBB (top byte free). */
void    present_set_palette(const uint32_t *bgrx256);

/* Push one frame.  `pixels` is the source framebuffer, `pitch` its stride in
 * bytes.  Returns S_OK on a successful Present. */
HRESULT present_frame(const void *pixels, int pitch);

/* The device and context, so other modules can render into the same swapchain
 * rather than standing up a second device. Both are NULL before present_init.
 * Declared as void* to keep this header free of the D3D11 headers. */
void    present_get_device(void **device, void **context);

/* Hand the presenter a finished RGBA image to show instead of the game's
 * palettised framebuffer. Pass NULL to go back to the normal 8bpp path. */
void    present_set_external(void *srv, int width, int height);

/* Query the current backbuffer/client size (for clipper + cursor mapping). */
void    present_get_client_size(int *w, int *h);

#ifdef __cplusplus
}
#endif
#endif /* IGN_D3D11_PRESENT_H */
