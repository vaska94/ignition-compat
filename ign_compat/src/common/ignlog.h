/* Shared tracing for the Ignition compatibility shims.
 * Enabled by creating ign_compat.log's directory marker, or setting
 * IGN_COMPAT_LOG=1 in the environment.  Off by default and near-free. */
#ifndef IGN_LOG_H
#define IGN_LOG_H
#ifdef __cplusplus
extern "C" {
#endif
void ign_log_init(const char *module);
void ign_logf(const char *fmt, ...);
int  ign_log_enabled(void);
#define IGNLOG(...) do { if (ign_log_enabled()) ign_logf(__VA_ARGS__); } while (0)
#ifdef __cplusplus
}
#endif
#endif
