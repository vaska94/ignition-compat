/* detour.h - replace individual functions of Ign_win.exe in place. */
#ifndef IGN_DETOUR_H
#define IGN_DETOUR_H

#include <windows.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct detour {
    const char *name;
    DWORD       addr;          /* VA of the original function                */
    const BYTE *sig;           /* bytes that must be at addr; these get moved */
    unsigned    sig_len;       /* 5..32, ending on an instruction boundary   */
    void       *replacement;   /* where calls to addr go once installed       */
    /* filled in by detour_install */
    void       *original;      /* trampoline: call it to run the original    */
    int         active;
} detour_t;

typedef enum {
    DETOUR_OK = 0,
    DETOUR_BAD_ARGS,           /* null pointer, or sig_len out of range      */
    DETOUR_BAD_ADDRESS,        /* addr is not committed, readable memory     */
    DETOUR_SIG_MISMATCH,       /* wrong executable, or already hooked         */
    DETOUR_NO_MEMORY,
    DETOUR_PROTECT_FAILED
} detour_status_t;

detour_status_t detour_install(detour_t *d);
detour_status_t detour_remove(detour_t *d);
const char     *detour_status_str(detour_status_t s);

#ifdef __cplusplus
}
#endif
#endif
