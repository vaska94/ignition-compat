#ifndef IGN_IATHOOK_H
#define IGN_IATHOOK_H
#ifdef __cplusplus
extern "C" {
#endif
/* Redirect one import of the main EXE. Returns 1 on success.
 * `original` receives the previous target so the hook can chain. */
int iat_hook(const char *dll, const char *func, void *replacement, void **original);
#ifdef __cplusplus
}
#endif
#endif
