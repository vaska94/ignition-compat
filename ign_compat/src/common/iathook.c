/* iathook.c - patch individual imports of the main executable in memory.
 *
 * Replacing a system DLL wholesale (shipping our own winmm.dll next to the
 * game) does not work: other system DLLs import from it too, and a stub that
 * exports only the handful of functions the game uses breaks their imports,
 * killing the process before it starts.
 *
 * Hooking single IAT entries of Ign_win.exe avoids all of that - nothing else
 * in the process sees a difference.
 */
#include <windows.h>
#include "iathook.h"

int iat_hook(const char *dll, const char *func, void *replacement, void **original)
{
    HMODULE base = GetModuleHandleA(NULL);
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt;
    IMAGE_IMPORT_DESCRIPTOR *imp;
    DWORD rva;

    if (!base || dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
    nt = (IMAGE_NT_HEADERS *)((BYTE *)base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return 0;

    rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    if (!rva) return 0;
    imp = (IMAGE_IMPORT_DESCRIPTOR *)((BYTE *)base + rva);

    for (; imp->Name; imp++) {
        const char *name = (const char *)((BYTE *)base + imp->Name);
        IMAGE_THUNK_DATA *ith, *oft;
        if (lstrcmpiA(name, dll) != 0) continue;

        ith = (IMAGE_THUNK_DATA *)((BYTE *)base + imp->FirstThunk);
        oft = imp->OriginalFirstThunk
            ? (IMAGE_THUNK_DATA *)((BYTE *)base + imp->OriginalFirstThunk)
            : ith;

        for (; oft->u1.AddressOfData; oft++, ith++) {
            IMAGE_IMPORT_BY_NAME *ibn;
            DWORD old;
            if (IMAGE_SNAP_BY_ORDINAL(oft->u1.Ordinal)) continue;
            ibn = (IMAGE_IMPORT_BY_NAME *)((BYTE *)base + oft->u1.AddressOfData);
            if (lstrcmpA((const char *)ibn->Name, func) != 0) continue;

            if (!VirtualProtect(&ith->u1.Function, sizeof(void *),
                                PAGE_READWRITE, &old))
                return 0;
            if (original) *original = (void *)(UINT_PTR)ith->u1.Function;
            ith->u1.Function = (UINT_PTR)replacement;
            VirtualProtect(&ith->u1.Function, sizeof(void *), old, &old);
            return 1;
        }
    }
    return 0;
}
