/* winmm.dll shim for Ignition.  *** NOT BUILT - ABANDONED APPROACH ***
 *
 * Kept as documentation of a dead end.  Shipping our own winmm.dll next to the
 * game breaks process startup outright: other system DLLs (dsound, msacm, ...)
 * import from winmm too, and a stub exporting only the 8 functions this game
 * uses cannot satisfy them, so the loader fails before any code runs.
 *
 * The joystick and MCI fixes live in ddraw_shim.c as IAT hooks instead, which
 * patch only Ign_win.exe's own imports and leave every other module alone.
 *
 *
 * Ign_win.exe crashes with an access violation *inside* winmmbase.dll
 * (0xC0000005 @ +0x5AF3) as it enters a race.  It touches only two risky
 * WinMM areas: the legacy multimedia joystick API (joyGetDevCapsA /
 * joyGetPosEx) and MCI CD-audio (mciSendStringA) for redbook music.  Both are
 * 1997-era interfaces that behave badly on modern Windows with no joystick and
 * no disc in the drive.
 *
 * This shim forwards every function the game imports to the real winmm.dll,
 * loaded by absolute path (we cannot use DEF forwarders - our DLL shares its
 * name with the target).  The joystick and MCI entry points can be neutered
 * independently via ign_compat.ini so we can bisect the crash and then keep
 * only the minimum disabled.
 */
#include <windows.h>
#include <mmsystem.h>
#include "../common/ignlog.h"

static HMODULE g_real;
static int g_block_joy = 1;   /* default: pretend no joystick   */
static int g_block_mci = 1;   /* default: no CD audio           */
static int g_trace     = 1;

static int cfg_int(const char *key, int dflt)
{
    char path[MAX_PATH], *p, *q, buf[32], def[16];
    GetModuleFileNameA(NULL, path, MAX_PATH);
    p = path; q = path;
    while (*q) { if (*q == '\\' || *q == '/') p = q + 1; q++; }
    lstrcpyA(p, "ign_compat.ini");
    wsprintfA(def, "%d", dflt);
    GetPrivateProfileStringA("ignition", key, def, buf, sizeof buf, path);
    {
        int v = 0, sign = 1, i = 0;
        if (buf[0] == '-') { sign = -1; i = 1; }
        for (; buf[i] >= '0' && buf[i] <= '9'; i++) v = v * 10 + (buf[i] - '0');
        return sign * v;
    }
}

static FARPROC real(const char *name)
{
    if (!g_real) {
        char p[MAX_PATH];
        UINT n = GetSystemDirectoryA(p, MAX_PATH);
        if (n && n < MAX_PATH - 12) {
            lstrcatA(p, "\\winmm.dll");
            g_real = LoadLibraryA(p);
        }
        if (!g_real) IGNLOG("FATAL: cannot load the real winmm.dll");
    }
    return g_real ? GetProcAddress(g_real, name) : NULL;
}

#define FWD(ret, name, params, args, fail)                                   \
    ret WINAPI name params {                                                 \
        typedef ret (WINAPI *fn_t) params;                                   \
        static fn_t fn;                                                      \
        if (!fn) *(FARPROC *)&fn = real(#name);                              \
        if (!fn) return fail;                                                \
        return fn args;                                                      \
    }

/* --- timing: always forwarded, the game depends on these ---------------- */
FWD(DWORD,  timeGetTime,     (void),                          (),        0)
FWD(MMRESULT, timeBeginPeriod, (UINT u),                      (u),       0)
FWD(MMRESULT, timeEndPeriod,   (UINT u),                      (u),       0)
FWD(MMRESULT, timeKillEvent,   (UINT id),                     (id),      0)
FWD(MMRESULT, timeSetEvent,  (UINT d, UINT r, LPTIMECALLBACK c, DWORD_PTR u, UINT f),
                             (d, r, c, u, f), 0)

/* --- joystick: the prime suspect ---------------------------------------- */
MMRESULT WINAPI joyGetDevCapsA(UINT_PTR id, LPJOYCAPSA caps, UINT cb)
{
    typedef MMRESULT (WINAPI *fn_t)(UINT_PTR, LPJOYCAPSA, UINT);
    static fn_t fn;
    if (g_trace) IGNLOG("joyGetDevCapsA(id=%u, cb=%u)%s",
                        (unsigned)id, cb, g_block_joy ? " [blocked]" : "");
    if (g_block_joy) {
        if (caps && cb) { ZeroMemory(caps, cb); }
        return MMSYSERR_NODRIVER;
    }
    if (!fn) *(FARPROC *)&fn = real("joyGetDevCapsA");
    return fn ? fn(id, caps, cb) : MMSYSERR_NODRIVER;
}

MMRESULT WINAPI joyGetPosEx(UINT id, LPJOYINFOEX pji)
{
    typedef MMRESULT (WINAPI *fn_t)(UINT, LPJOYINFOEX);
    static fn_t fn;
    static int logged;
    if (g_trace && logged++ < 3)
        IGNLOG("joyGetPosEx(id=%u, dwSize=%lu)%s", id,
               pji ? (unsigned long)pji->dwSize : 0ul,
               g_block_joy ? " [blocked]" : "");
    if (g_block_joy) return JOYERR_UNPLUGGED;
    if (!fn) *(FARPROC *)&fn = real("joyGetPosEx");
    return fn ? fn(id, pji) : JOYERR_UNPLUGGED;
}

/* --- MCI: CD redbook music, silent without a disc ----------------------- */
MCIERROR WINAPI mciSendStringA(LPCSTR cmd, LPSTR ret, UINT cchRet, HWND cb)
{
    typedef MCIERROR (WINAPI *fn_t)(LPCSTR, LPSTR, UINT, HWND);
    static fn_t fn;
    if (g_trace) IGNLOG("mciSendStringA(\"%s\")%s", cmd ? cmd : "(null)",
                        g_block_mci ? " [blocked]" : "");
    if (g_block_mci) {
        if (ret && cchRet) ret[0] = '\0';
        return MCIERR_DEVICE_NOT_INSTALLED;
    }
    if (!fn) *(FARPROC *)&fn = real("mciSendStringA");
    return fn ? fn(cmd, ret, cchRet, cb) : MCIERR_DEVICE_NOT_INSTALLED;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(inst);
        ign_log_init("winmm");
        g_block_joy = cfg_int("BLOCK_JOYSTICK", 1);
        g_block_mci = cfg_int("BLOCK_MCI", 1);
        g_trace     = cfg_int("WINMM_TRACE", 1);
        IGNLOG("winmm shim attached (block_joy=%d block_mci=%d)",
               g_block_joy, g_block_mci);
    }
    return TRUE;
}
