#include "ignlog.h"
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>

static FILE *g_fp;
static int   g_on = -1;
static char  g_mod[32] = "shim";
static char  g_path[MAX_PATH];

/* Log beside the executable, so a stray CWD cannot hide the file. */
static const char *logpath(void)
{
    if (!g_path[0]) {
        char *p;
        GetModuleFileNameA(NULL, g_path, MAX_PATH);
        p = g_path;
        { char *q = p; while (*q) { if (*q == '\\' || *q == '/') p = q + 1; q++; } }
        lstrcpyA(p, "ign_compat.log");
    }
    return g_path;
}

int ign_log_enabled(void)
{
    if (g_on < 0) {
        char buf[8];
        /* Default ON during bring-up: a shim that fails silently is useless.
         * Set IGN_COMPAT_LOG=0 to turn it off. */
        g_on = !(GetEnvironmentVariableA("IGN_COMPAT_LOG", buf, sizeof buf) > 0
                 && buf[0] == '0');
    }
    return g_on;
}

void ign_log_init(const char *module)
{
    if (module) { lstrcpynA(g_mod, module, sizeof g_mod); }
    if (!ign_log_enabled() || g_fp) return;
    g_fp = fopen(logpath(), "a");
    if (g_fp) fprintf(g_fp, "\n=== %s loaded (pid %lu) ===\n",
                      g_mod, (unsigned long)GetCurrentProcessId());
}

void ign_logf(const char *fmt, ...)
{
    va_list ap;
    if (!ign_log_enabled()) return;
    if (!g_fp) { g_fp = fopen(logpath(), "a"); }
    {
        char line[1024];
        int n = wsprintfA(line, "[%s] ", g_mod);
        va_start(ap, fmt);
        wvsprintfA(line + n, fmt, ap);
        va_end(ap);
        OutputDebugStringA(line);
        if (g_fp) { fprintf(g_fp, "%s\n", line); fflush(g_fp); }
    }
}
