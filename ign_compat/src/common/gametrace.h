/* gametrace.h - record the original game's simulation so replacements can be
 * diffed against it. Off unless enabled in ign_compat.ini. */
#ifndef IGN_GAMETRACE_H
#define IGN_GAMETRACE_H
#ifdef __cplusplus
extern "C" {
#endif
int  gametrace_install(int max_steps, int car_bytes);
int  gametrace_install_prims(int max_frames);
void gametrace_close(void);
#ifdef __cplusplus
}
#endif
#endif
