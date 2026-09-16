#!/usr/bin/env python3
"""anchors.py -- write _re/out/labels.tsv with the CERTAIN anchors established in
_re/notes/01..03 (each mid-function anchor is mapped to its owning function).
Columns: va  subsystem  role  confidence  evidence"""
import json
R = "/mnt/c/Games/IGNITION/_re/out/"
db = json.load(open(R + "funcdb.json"))
F = {int(k, 16): v for k, v in db["funcs"].items()}
S = sorted(F)

def owner(a):
    best = None
    for f in S:
        if f <= a < max(F[f]["end"], f + 1):
            best = f
    return best

A = [
 # platform
 (0x004120A0, "platform", "WinMain", "03 C.1"), (0x004122D0, "platform", "WndProc", "01 1.1"),
 (0x00412460, "platform", "GetTimeMs (QPC, pause-aware)", "02 5.2"), (0x00412500, "platform", "DX bring-up: gfx init + DirectInput", "03 C.3"),
 (0x00412530, "platform", "PostMessage(WM_CLOSE) shutdown", "03 C.3"), (0x0045B170, "platform", "engine subsystem init", "03 C.5"),
 (0x0045B4F0, "platform", "engine banner printf", "03 C.5"), (0x0045AD50, "platform", "named-object table reset", "03 C.5"),
 (0x0045AD80, "platform", "named-object register", "03 C.5"), (0x0045B1F0, "platform", "event free-slot ring init", "03 C.5"),
 (0x0045B5C0, "platform", "debug logger (disabled)", "03 C.7"), (0x0045B550, "platform", "logger enable / Default.fil", "03 C.7"),
 (0x00457420, "platform", "load_file(name,buf,len,ofs)", "03 C.7c"), (0x00457590, "platform", "save_file(name,buf,len)", "03 C.7c"),
 (0x004576B0, "platform", "file_exists", "03 C.7c"),
 (0x00469950, "crt", "WinMainCRTStartup", "03 C.1"),
 # main loop
 (0x00412230, "main_loop", "FrameTick: app-state dispatcher 0/1/2", "02 5.3b"), (0x00417270, "main_loop", "app enter (state 0)", "03 A.4"),
 (0x004172B0, "main_loop", "per-frame step / race-menu state machine", "02 5.3b"), (0x00417E90, "main_loop", "app leave (state 2)", "02 4"),
 (0x00420C00, "main_loop", "GetFrameDeltaTicks 36 FPS gate", "02 5.3b"), (0x00420D10, "main_loop", "catch-up cap 200 ticks", "02 5.4b"),
 (0x0041F8F0, "main_loop", "sim clock resync", "02 5.4b"),
 # video
 (0x00456A40, "video_ddraw", "surface slot table zero-init", "01 2.5"), (0x00456AF0, "video_ddraw", "select gfx driver", "01 8"),
 (0x0045B690, "video_ddraw", "install DDraw driver table", "01 8"), (0x0045B730, "video_ddraw", "driver stub", "01 8"),
 (0x0045B740, "video_ddraw", "display init: window + DirectDrawCreate", "01 1"), (0x0045BD70, "video_ddraw", "change display mode", "01 8"),
 (0x0045C060, "video_ddraw", "DDraw shutdown", "01 8"), (0x0045C150, "video_ddraw", "driver stub", "01 8"),
 (0x0045C160, "video_ddraw", "BltFast (dead)", "01 5"), (0x0045C1D0, "video_ddraw", "SwBlt sysmem->surface", "01 4.3"),
 (0x0045C3D0, "video_ddraw", "Clear surface (OOB bug)", "01 9"), (0x0045C4B0, "video_ddraw", "Flip", "01 4.4"),
 (0x0045C520, "video_ddraw", "driver stub", "01 8"), (0x0045C530, "video_ddraw", "LockSurface", "01 4.1"),
 (0x0045C680, "video_ddraw", "UnlockSurface", "01 4.2"), (0x0045C6D0, "video_ddraw", "SetGamePalette", "01 3.2"),
 (0x0045C730, "video_ddraw", "RestoreAll", "01 8"), (0x00456E60, "video_ddraw", "install 2nd gfx table", "03 C.3"),
 (0x00456BC0, "video_ddraw", "gfx->init wrapper", "01 8"), (0x00456BE0, "video_ddraw", "gfx->modechange wrapper", "01 8"),
 (0x00456BF0, "video_ddraw", "gfx->shutdown wrapper", "01 8"), (0x00456B70, "video_ddraw", "gfx->SwBlt wrapper", "01 8"),
 (0x00456BB0, "video_ddraw", "gfx->Clear wrapper", "01 8"), (0x00456C90, "video_ddraw", "gfx->Flip wrapper", "01 8"),
 (0x00456C40, "video_ddraw", "gfx->SetPalette wrapper", "01 8"), (0x00456C50, "video_ddraw", "gfx->RestoreAll wrapper", "01 8"),
 (0x00403DA0, "video_ddraw", "menu/2D present", "01 4.5"), (0x00446410, "video_ddraw", "3D view present", "01 4.5"),
 (0x00403B10, "video_ddraw", "mode-change reset: palette+clear+flip", "01 4.5"),
 # input
 (0x00455AB0, "input", "input state reset", "03 C.5"), (0x00455AC0, "input", "InitInput (DirectInput keyboard)", "02 3.2"),
 (0x00455C60, "input", "InitInput_WithTimer (dead)", "02 2"), (0x00455E20, "input", "ShutdownInput", "02 3.4"),
 (0x00455EB0, "input", "ReInitInput", "02 3.4"), (0x00455EF0, "input", "PollInput GetDeviceData", "02 3.3"),
 (0x00456070, "input", "key_is_down", "03 C.6"), (0x00456080, "input", "key pressed edge", "03 C.6"),
 (0x004560A0, "input", "key repeat edge", "03 C.6"), (0x004560C0, "input", "key hook dispatch", "02 3.3"),
 (0x004560E0, "input", "cheat-code recorder", "02 3.3"), (0x00456120, "input", "cheat strstr", "02 3.3"),
 (0x00456160, "input", "timeSetEvent callback (dead)", "02 2"),
 (0x00459A4A, "input", "joystick poll joyGetPosEx", "02 6.3"), (0x0045972A, "input", "joystick init joyGetDevCaps", "02 6.2"),
 (0x00441390, "race_controls", "in-race joystick/keyboard -> control block", "02 6.5"),
 (0x00442030, "race_controls", "ApplyControls(throttle,brake,steer,gear)", "02 6.5"),
 # audio
 (0x004577A0, "audio", "SND_Init", "02 1.1"), (0x00457890, "audio", "SND_Service pump", "02 1.10"),
 (0x00465DF0, "audio", "mixer init", "02 1.11"), (0x00465F20, "audio", "Mix front end", "02 1.11"),
 (0x00466150, "audio", "mixer render inner", "02 1.11"), (0x00467120, "audio", "voice list walker", "02 1.11"),
 (0x004673D0, "audio", "DS_ClearBuffer", "02 1.1"), (0x00467510, "audio", "DS_BeginWrite", "02 1.9"),
 (0x004677F0, "audio", "DS_EndWrite", "02 1.9"), (0x004678B0, "audio", "DS_Shutdown", "02 1.1"),
 (0x00467920, "audio", "DS_Init DirectSoundCreate", "02 1.3"), (0x0041F9B0, "audio", "race sound bring-up", "02 1.11"),
 # cd
 (0x00457B10, "cd_audio", "cd_is_playing", "03 A.3"), (0x00457B50, "cd_audio", "cd_update_status", "03 A.3"),
 (0x00457CF0, "cd_audio", "cd_set_volume_onoff", "03 A.3"), (0x00457D30, "cd_audio", "cd_seek_track (dead)", "03 A.3"),
 (0x00457D60, "cd_audio", "cd_seek_msf", "03 A.3"), (0x00457DA0, "cd_audio", "cd_read_toc", "03 A.3"),
 (0x00457EB0, "cd_audio", "cd_play_whole_disc (dead)", "03 A.3"), (0x00457ED0, "cd_audio", "cd_play_track", "03 A.3"),
 (0x00457F10, "cd_audio", "cd_play_track_range (dead)", "03 A.3"), (0x00457F60, "cd_audio", "cd_play_from_len", "03 A.3"),
 (0x00457FC0, "cd_audio", "cd_stop", "03 A.3"), (0x00457FE0, "cd_audio", "cd_play_to (dead)", "03 A.3"),
 (0x00458040, "cd_audio", "cd_open", "03 A.3"), (0x004580A0, "cd_audio", "cd_close", "03 A.3"),
 (0x004580C0, "cd_audio", "cd_door_open (dead)", "03 A.3"), (0x004580E0, "cd_audio", "MSF helper", "03 A.3"),
 (0x00458160, "cd_audio", "MSF helper", "03 A.3"), (0x004581B0, "cd_audio", "MSF helper", "03 A.3"),
 (0x004032EA, "cd_audio", "in-race/menu music scheduler (enclosing fn)", "03 A.3"),
 # net
 (0x00459D40, "net", "open network dialog", "03 B.6"), (0x00459ED1, "net", "Net_RefreshPlayers", "03 B.8"),
 (0x0045A2B0, "net", "DirectPlay system message handler", "03 B.3"), (0x0045A3C0, "net", "network DlgProc", "03 B.6"),
 (0x0045A680, "net", "WM_INITDIALOG DirectPlayEnumerate", "03 B.7"), (0x0045A7A0, "net", "EnumSP callback", "03 B.3"),
 (0x0045A830, "net", "MakeDP2", "03 B.7"), (0x0045A9D0, "net", "OnHost", "03 B.7"), (0x0045AB50, "net", "OnSearch", "03 B.7"),
 (0x0045AC40, "net", "EnumSessions callback", "03 B.3"), (0x0045ACD0, "net", "EnumPlayers callback", "03 B.3"),
 (0x0040CEC0, "net", "per-frame network state machine", "03 B.7"), (0x00404610, "menu", "menu action: network game", "03 B.7"),
 # menu / settings
 (0x00410AF0, "menu", "menu draw routine", "03 C.7b"), (0x00410D80, "menu", "menu input/toggle handler", "03 C.7b"),
 (0x00409FB0, "menu", "GFX page on_activate", "03 C.7b"), (0x00409610, "menu", "GFX page on_key", "03 C.7b"),
 (0x0040C810, "settings", "settings defaults", "03 C.7c"), (0x00404A60, "settings", "menu-side settings load (tolerant)", "03 C.7c"),
 (0x00404800, "settings", "settings commit", "03 C.7c"), (0x004045B0, "menu", "menu-confirm callback", "03 C.7c"),
 (0x00402C00, "menu", "menu frame + joystick-as-keys + autosave", "02 6.5 / 03 C.7c"), (0x004029A0, "settings", "early language load", "03 C.8"),
 (0x00419260, "settings", "race-side settings loader (exit on fail)", "03 C.7c"), (0x0041935C, "settings", "apply race settings to globals", "03 C.7c"),
 (0x004225D0, "settings", "save settings on leaving race", "03 C.7c"), (0x00420870, "game_state", "race-module teardown (+save)", "03 C.7c"),
 # ghosts / screenshot / textures
 (0x004222B0, "ghost_replay", "write GHOSTS\\%s.GST", "03 C.7c"), (0x0043A500, "ghost_replay", "best-lap/ghost tables", "03 C.7c"),
 (0x00446040, "screenshot", "F12 IGN%d.TGA writer", "03 C.7c"),
 (0x00447280, "textures", "plain texture upload", "03 C.7b"), (0x00448620, "textures", "mip chain + conv.tab cache", "03 C.7b"),
 (0x0041B470, "render3d", "renderer setup (PERSP/MIP selection)", "03 C.7b"),
 # race / physics
 (0x00417EA0, "game_state", "front-end/gfx init (menu init, cd_open)", "03 A.4"), (0x004182D0, "game_state", "INSERT CD screen (dead)", "03 A.4"),
 (0x004184C0, "game_state", "advance past CD screen", "03 A.4"), (0x00418500, "video_ddraw", "resolution mode setter", "03 C.7a"),
 (0x004357A0, "sim_tick", "36 Hz simulation tick (accumulator B)", "02 5.4"), (0x00435350, "physics", "vehicle integrator (accumulator A)", "02 5.4b"),
 (0x00436DA0, "camera", "camera routine (reads look-back)", "02 6.5"),
]
out = open(R + "labels.tsv", "w")
out.write("#va\tsubsystem\trole\tconfidence\tevidence\n")
seen = {}
for a, sub, role, ev in A:
    f = a if a in F else owner(a)
    if f is None:
        print("no owner for", hex(a)); continue
    if f in seen:
        continue
    seen[f] = 1
    note = role if f == a else f"{role} (anchor {a:#x})"
    out.write(f"{f:#010x}\t{sub}\t{note}\tCERTAIN\tnotes {ev}\n")
# section rules
for f in S:
    if f in seen: continue
    if f >= 0x0064E000:
        out.write(f"{f:#010x}\trasterizer\tcode-section asm routine\tCERTAIN\tsection `code`\n")
    elif 0x00469100 <= f < 0x00478B8C:
        out.write(f"{f:#010x}\tcrt\tMSVC CRT\tINFERRED\taddress >= CRT start 0x469100\n")
print("anchors written:", len(seen))
