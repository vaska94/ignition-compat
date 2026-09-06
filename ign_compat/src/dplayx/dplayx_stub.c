/* dplayx.dll shim for Ignition.
 *
 * Why this exists, and why it comes first:
 * Ign_win.exe imports dplayx.dll ordinals #1 (DirectPlayCreate) and
 * #2 (DirectPlayEnumerate) *statically*.  DirectPlay is an off-by-default
 * optional feature on Windows 8 and later, so on a stock Windows 10/11 box the
 * PE loader cannot resolve the import table and terminates the process before
 * a single instruction of game code executes.  No amount of graphics work
 * matters until this import resolves.
 *
 * This stub resolves it.  Both entry points behave as "no service providers
 * are installed", which is a state the 1997 code already had to handle (a
 * machine with no network stack), so single-player takes the normal path.
 *
 * A functional TCP/IP replacement can grow in here later; see
 * _re/notes/03_startup_cdcheck_netplay.md for the method surface it must cover.
 */
#include <windows.h>
#include "../common/ignlog.h"

/* DirectPlay 1.0 typedefs - deliberately local so this builds without dplay.h
 * pulling in the whole legacy SDK surface. */
typedef BOOL (WINAPI *LPDPENUMDPCALLBACKA)(LPGUID lpguidSP, LPSTR lpSPName,
                                           DWORD dwMajor, DWORD dwMinor,
                                           LPVOID lpContext);

#define DPERR_NOTINSTALLED  0x8877028Cu   /* service provider not present   */
#define DPERR_UNAVAILABLE   0x88770005u   /* generic "cannot do that now"   */

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)inst; (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(inst);
        ign_log_init("dplayx");
        IGNLOG("attached - reporting no service providers");
    }
    return TRUE;
}

/* Ordinal 1. Called from 0x0045A851 when the user starts a network game. */
HRESULT WINAPI DirectPlayCreate(LPGUID lpGUID, void **lplpDP, void *pUnk)
{
    (void)lpGUID; (void)pUnk;
    IGNLOG("DirectPlayCreate -> DPERR_UNAVAILABLE");
    if (lplpDP) *lplpDP = NULL;
    return (HRESULT)DPERR_UNAVAILABLE;
}

/* Ordinal 2. Called from 0x0045A696 to populate the connection-type list.
 * Invoking the callback zero times leaves that list empty, which is exactly
 * what the game saw on a machine with no protocols bound. */
HRESULT WINAPI DirectPlayEnumerate(LPDPENUMDPCALLBACKA cb, LPVOID ctx)
{
    (void)cb; (void)ctx;
    IGNLOG("DirectPlayEnumerate -> no providers");
    return S_OK;
}
