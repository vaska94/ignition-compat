/* detour.c - replace functions of Ign_win.exe in place, one at a time.
 *
 * The hybrid rebuild swaps the original game out piece by piece while it keeps
 * running, so each replacement can be checked against the code it replaces.
 * Ign_win.exe has no ASLR (DllCharacteristics = 0), so every function lives at
 * the same address on every run and a hook can simply name it.
 *
 * Mechanism: the first bytes of the function become `jmp replacement`, and a
 * trampoline holding the displaced bytes followed by `jmp back` keeps the
 * original callable - by the replacement, or by a test comparing the two.
 *
 * Two safety rules are checked offline by _re/tools/hookspec.py, not here:
 *   - the displaced bytes end on an instruction boundary and contain no
 *     relative branch or call, so they run unchanged from the trampoline;
 *   - nothing in the program branches into the displaced range.
 * What is checked here is the byte signature. If the bytes at the address are
 * not the ones the hook was written against, the hook is refused - which turns
 * "different executable" or "hooked twice" into an error code instead of
 * memory corruption.
 */
#include "detour.h"
#include <string.h>

#define JMP_LEN 5
#define SIG_MAX 32

/* Encode `jmp to` into buf, for a jmp that will execute at address `site`.
 * The two differ when the bytes are assembled in a scratch buffer and copied
 * into place afterwards, and rel32 must be relative to where the jmp runs.
 * EIP arithmetic wraps modulo 2^32 in a 32-bit process, so every target in the
 * process is reachable. */
static void encode_jmp(BYTE *buf, const BYTE *site, const BYTE *to)
{
    INT32 rel = (INT32)((UINT_PTR)to - ((UINT_PTR)site + JMP_LEN));
    buf[0] = 0xE9;
    memcpy(buf + 1, &rel, sizeof rel);
}

static int readable(const BYTE *p, unsigned n)
{
    MEMORY_BASIC_INFORMATION mbi;
    if (!VirtualQuery(p, &mbi, sizeof mbi)) return 0;
    if (mbi.State != MEM_COMMIT) return 0;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return 0;
    return (UINT_PTR)mbi.BaseAddress + mbi.RegionSize >= (UINT_PTR)p + n;
}

static int patch(BYTE *at, const BYTE *bytes, unsigned n)
{
    DWORD old;
    if (!VirtualProtect(at, n, PAGE_EXECUTE_READWRITE, &old)) return 0;
    memcpy(at, bytes, n);
    VirtualProtect(at, n, old, &old);
    FlushInstructionCache(GetCurrentProcess(), at, n);
    return 1;
}

detour_status_t detour_install(detour_t *d)
{
    BYTE *target;
    BYTE  code[SIG_MAX];
    unsigned i;

    if (!d || !d->sig || !d->replacement || d->sig_len < JMP_LEN || d->sig_len > SIG_MAX)
        return DETOUR_BAD_ARGS;
    if (d->active) return DETOUR_OK;

    target = (BYTE *)(UINT_PTR)d->addr;
    if (!readable(target, d->sig_len)) return DETOUR_BAD_ADDRESS;
    if (memcmp(target, d->sig, d->sig_len) != 0) return DETOUR_SIG_MISMATCH;

    if (!d->original) {   /* reused if this hook was installed before */
        BYTE *t = (BYTE *)VirtualAlloc(NULL, d->sig_len + JMP_LEN,
                                       MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!t) return DETOUR_NO_MEMORY;
        memcpy(t, d->sig, d->sig_len);
        encode_jmp(t + d->sig_len, t + d->sig_len, target + d->sig_len);
        d->original = t;
    }

    encode_jmp(code, target, (const BYTE *)d->replacement);
    /* The rest of the displaced range is never executed.  int3 rather than nop,
     * so a violation of the no-branch-into-here rule crashes loudly instead of
     * running into the middle of our jmp. */
    for (i = JMP_LEN; i < d->sig_len; i++) code[i] = 0xCC;

    if (!patch(target, code, d->sig_len)) return DETOUR_PROTECT_FAILED;
    d->active = 1;
    return DETOUR_OK;
}

detour_status_t detour_remove(detour_t *d)
{
    if (!d) return DETOUR_BAD_ARGS;
    if (!d->active) return DETOUR_OK;
    if (!patch((BYTE *)(UINT_PTR)d->addr, d->sig, d->sig_len)) return DETOUR_PROTECT_FAILED;
    /* The trampoline is kept: a replacement may still be running inside a call
     * to the original.  It is reused if the hook is installed again. */
    d->active = 0;
    return DETOUR_OK;
}

const char *detour_status_str(detour_status_t s)
{
    switch (s) {
    case DETOUR_OK:             return "ok";
    case DETOUR_BAD_ARGS:       return "bad arguments";
    case DETOUR_BAD_ADDRESS:    return "address not readable";
    case DETOUR_SIG_MISMATCH:   return "signature mismatch";
    case DETOUR_NO_MEMORY:      return "out of memory";
    case DETOUR_PROTECT_FAILED: return "VirtualProtect failed";
    }
    return "unknown";
}
