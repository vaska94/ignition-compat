/* detour_test.c - exercises detour.c without involving the game.
 *
 * The targets are written byte by byte in assembly so their signatures are
 * fixed, and they use MSVC's encodings (8B EC for mov ebp,esp) to resemble
 * what Ign_win.exe actually contains.  One is cdecl, one is register-passing
 * fastcall, because the game's hand-written assembly will not all be cdecl.
 */
#include <stdio.h>
#include <string.h>
#include "../src/common/detour.h"

__asm__(
    ".text\n"
    "t_mul_impl:\n"
    "  .byte 0x55\n"                  /* push ebp            */
    "  .byte 0x8B, 0xEC\n"            /* mov  ebp, esp       */
    "  .byte 0x8B, 0x45, 0x08\n"      /* mov  eax, [ebp+8]   */
    "  .byte 0x0F, 0xAF, 0x45, 0x0C\n"/* imul eax, [ebp+0Ch] */
    "  .byte 0x5D\n"                  /* pop  ebp            */
    "  .byte 0xC3\n"                  /* ret                 */
    "t_fadd_impl:\n"
    "  .byte 0x8B, 0xC1\n"            /* mov eax, ecx        */
    "  .byte 0x03, 0xC2\n"            /* add eax, edx        */
    "  .byte 0x90\n"                  /* nop                 */
    "  .byte 0xC3\n"                  /* ret                 */
);
extern int __cdecl    t_mul(int a, int b)  __asm__("t_mul_impl");
extern int __fastcall t_fadd(int a, int b) __asm__("t_fadd_impl");

typedef int (__cdecl    *mul_fn)(int, int);
typedef int (__fastcall *add_fn)(int, int);

static detour_t D_mul, D_fadd;
static int g_replacement_calls;

static int __cdecl my_mul(int a, int b)
{
    g_replacement_calls++;
    return ((mul_fn)D_mul.original)(a, b) + 1000;   /* proves both ran */
}

static int __fastcall my_fadd(int a, int b)
{
    g_replacement_calls++;
    return ((add_fn)D_fadd.original)(a, b) * 10;
}

static int pass, fail;
#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (cond) pass++;                                                 \
        else { fail++; printf("FAIL (line %d): ", __LINE__);              \
               printf(__VA_ARGS__); printf("\n"); }                       \
    } while (0)

int main(void)
{
    static const BYTE sig_mul[]   = { 0x55, 0x8B, 0xEC, 0x8B, 0x45, 0x08 };
    static const BYTE sig_fadd[]  = { 0x8B, 0xC1, 0x03, 0xC2, 0x90 };
    static const BYTE sig_wrong[] = { 0x55, 0x89, 0xE5, 0x8B, 0x45, 0x08 }; /* gcc's encoding */
    BYTE *mul_addr = (BYTE *)(UINT_PTR)t_mul;
    BYTE *add_addr = (BYTE *)(UINT_PTR)t_fadd;
    BYTE  before[6];
    long long sum = 0, want = 0;
    int i;
    detour_status_t st;

    setvbuf(stdout, NULL, _IONBF, 0);   /* keep output if a hook crashes the test */

    CHECK(t_mul(6, 7) == 42, "baseline t_mul = %d", t_mul(6, 7));
    CHECK(t_fadd(3, 4) == 7, "baseline t_fadd = %d", t_fadd(3, 4));

    /* failure paths leave the code byte-for-byte untouched */
    memcpy(before, mul_addr, sizeof before);
    {
        detour_t wrong = { "wrong-sig", (DWORD)(UINT_PTR)mul_addr, sig_wrong, 6, (void *)my_mul, 0, 0 };
        st = detour_install(&wrong);
        CHECK(st == DETOUR_SIG_MISMATCH, "wrong signature: got '%s'", detour_status_str(st));
    }
    {
        detour_t tiny = { "too-short", (DWORD)(UINT_PTR)mul_addr, sig_mul, 4, (void *)my_mul, 0, 0 };
        st = detour_install(&tiny);
        CHECK(st == DETOUR_BAD_ARGS, "4-byte signature: got '%s'", detour_status_str(st));
    }
    {
        detour_t norepl = { "no-replacement", (DWORD)(UINT_PTR)mul_addr, sig_mul, 6, NULL, 0, 0 };
        st = detour_install(&norepl);
        CHECK(st == DETOUR_BAD_ARGS, "NULL replacement: got '%s'", detour_status_str(st));
    }
    {
        detour_t unmapped = { "unmapped", 0x00000010, sig_mul, 6, (void *)my_mul, 0, 0 };
        st = detour_install(&unmapped);
        CHECK(st == DETOUR_BAD_ADDRESS, "unmapped address: got '%s'", detour_status_str(st));
    }
    CHECK(memcmp(before, mul_addr, sizeof before) == 0, "a refused hook modified the code");
    CHECK(t_mul(6, 7) == 42, "after refused hooks t_mul = %d", t_mul(6, 7));

    /* install: calls reach the replacement, and the original stays reachable */
    D_mul = (detour_t){ "t_mul", (DWORD)(UINT_PTR)mul_addr, sig_mul, 6, (void *)my_mul, 0, 0 };
    st = detour_install(&D_mul);
    CHECK(st == DETOUR_OK, "install t_mul: '%s'", detour_status_str(st));
    CHECK(mul_addr[0] == 0xE9, "entry is not a jmp: %02X", mul_addr[0]);
    CHECK(t_mul(6, 7) == 1042, "hooked t_mul = %d", t_mul(6, 7));
    CHECK(((mul_fn)D_mul.original)(6, 7) == 42, "trampoline t_mul = %d",
          ((mul_fn)D_mul.original)(6, 7));

    st = detour_install(&D_mul);
    CHECK(st == DETOUR_OK && t_mul(6, 7) == 1042, "second install of the same hook changed behaviour");
    {
        detour_t again = { "t_mul-again", (DWORD)(UINT_PTR)mul_addr, sig_mul, 6, (void *)my_mul, 0, 0 };
        st = detour_install(&again);
        CHECK(st == DETOUR_SIG_MISMATCH, "a different hook on hooked bytes: got '%s'", detour_status_str(st));
    }

    /* a register-passing convention, hooked independently */
    D_fadd = (detour_t){ "t_fadd", (DWORD)(UINT_PTR)add_addr, sig_fadd, 5, (void *)my_fadd, 0, 0 };
    st = detour_install(&D_fadd);
    CHECK(st == DETOUR_OK, "install t_fadd: '%s'", detour_status_str(st));
    CHECK(t_fadd(3, 4) == 70, "hooked fastcall t_fadd = %d", t_fadd(3, 4));
    CHECK(t_mul(6, 7) == 1042, "second hook disturbed the first");

    /* stack balance under load: a wrong convention would corrupt esp quickly */
    for (i = 0; i < 1000000; i++) {
        sum  += t_mul(i & 15, 3) + t_fadd(i & 7, 1);
        want += (long long)((i & 15) * 3 + 1000) + (long long)(((i & 7) + 1) * 10);
    }
    CHECK(sum == want, "1e6 hooked calls: sum %lld, want %lld", sum, want);

    /* remove restores the exact bytes and the original behaviour */
    st = detour_remove(&D_mul);
    CHECK(st == DETOUR_OK, "remove t_mul: '%s'", detour_status_str(st));
    CHECK(memcmp(mul_addr, sig_mul, sizeof sig_mul) == 0, "remove did not restore the original bytes");
    CHECK(t_mul(6, 7) == 42, "after remove t_mul = %d", t_mul(6, 7));
    CHECK(t_fadd(3, 4) == 70, "removing t_mul affected t_fadd");

    /* reinstall reuses the trampoline */
    {
        void *tramp = D_mul.original;
        st = detour_install(&D_mul);
        CHECK(st == DETOUR_OK && D_mul.original == tramp && t_mul(6, 7) == 1042,
              "reinstall: status '%s', trampoline reused %d, t_mul = %d",
              detour_status_str(st), D_mul.original == tramp, t_mul(6, 7));
    }

    printf("detour_test: %d passed, %d failed (replacement ran %d times)\n",
           pass, fail, g_replacement_calls);
    return fail ? 1 : 0;
}
