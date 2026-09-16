/* prim_gpu.h - draw the game's own primitive list on the GPU.
 *
 * Ignition hands its software rasterizer a NULL-terminated list of records
 * whose vertices are already screen-space 24.8 fixed point. Nothing in that
 * list is resolution-bound: the same list drawn into a larger target simply
 * produces a larger picture. That is the whole basis for running this game
 * above the 800x600 its static framebuffer caps it at.
 */
#ifndef IGN_PRIM_GPU_H
#define IGN_PRIM_GPU_H

#include <windows.h>

#ifdef __cplusplus
extern "C" {
#endif

/* scale = output size relative to the game's own resolution (2 = 1600x1200). */
int  prim_gpu_init(int scale);
void prim_gpu_shutdown(void);

/* Render one submit block's list. `block` is the 0x20-byte structure the game
 * passes to the rasterizer. Returns 1 if the frame was drawn on the GPU. */
int  prim_gpu_render(const void *block);

/* True once a frame has been rendered and is ready to present. */
int  prim_gpu_have_frame(void);

#ifdef __cplusplus
}
#endif
#endif
