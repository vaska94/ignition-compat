/* d3d11_present.c - see d3d11_present.h
 *
 * Design notes
 * ------------
 * Ignition renders 8bpp palettised frames with a software rasterizer.  The
 * naive modern port (CPU palette expansion + StretchBlt) throws away the one
 * thing a GPU is good at here, and makes per-frame palette animation cost a
 * full-screen conversion.  So we keep the framebuffer 8bpp on the GPU:
 *
 *   - the frame goes into an R8_UNORM dynamic texture (one byte per pixel)
 *   - the palette goes into a 256x1 B8G8R8A8 texture
 *   - a pixel shader does the lookup
 *
 * A palette-only change is then a 1 KB upload instead of a full reconvert,
 * which is what the game's fade/flash effects actually do.
 *
 * Shaders are compiled at runtime through d3dcompiler_47.dll, loaded lazily so
 * that a machine without it degrades instead of failing to start.
 */
#define COBJMACROS
#define CINTERFACE
#include "d3d11_present.h"
#include <d3d11.h>
#include <dxgi.h>
#include <d3dcompiler.h>
#include <string.h>
#include <stdlib.h>

#ifndef SAFE_RELEASE
#define SAFE_RELEASE(p) do { if (p) { (p)->lpVtbl->Release(p); (p) = NULL; } } while (0)
#endif

typedef HRESULT (WINAPI *PFN_D3DCOMPILE)(LPCVOID, SIZE_T, LPCSTR,
        const D3D_SHADER_MACRO *, ID3DInclude *, LPCSTR, LPCSTR,
        UINT, UINT, ID3DBlob **, ID3DBlob **);

static struct {
    HWND                     hwnd;
    ID3D11Device            *dev;
    ID3D11DeviceContext     *ctx;
    IDXGISwapChain          *swap;
    ID3D11RenderTargetView  *rtv;

    ID3D11VertexShader      *vs;
    ID3D11PixelShader       *ps_p8;
    ID3D11PixelShader       *ps_rgb;
    ID3D11SamplerState      *samp_point;
    ID3D11SamplerState      *samp_linear;

    ID3D11Texture2D         *tex_src;
    ID3D11ShaderResourceView*srv_src;
    ID3D11Texture2D         *tex_pal;
    ID3D11ShaderResourceView*srv_pal;

    present_config_t         cfg;
    int                      client_w, client_h;
    float                    vp_w, vp_h;
    uint32_t                 palette[256];
    int                      palette_dirty;
    int                      ready;
} G;

/* ---------------------------------------------------------------- shaders */

static const char *VS_SRC =
"struct VSOut { float4 pos : SV_Position; float2 uv : TEXCOORD0; };\n"
"VSOut main(uint id : SV_VertexID) {\n"
"  VSOut o;\n"
"  float2 t = float2((id << 1) & 2, id & 2);\n"
"  o.uv  = t;\n"
"  o.pos = float4(t * float2(2, -2) + float2(-1, 1), 0, 1);\n"
"  return o;\n"
"}\n";

/* Palettised path.  Nearest-neighbour on the *index*, because interpolating
 * palette indices is meaningless.  When smoothing is requested we interpolate
 * after the lookup instead, which is the correct order of operations. */
static const char *PS_P8_SRC =
"Texture2D<float>  IndexTex : register(t0);\n"
"Texture2D<float4> PalTex   : register(t1);\n"
"SamplerState      Samp     : register(s0);\n"
"cbuffer Params : register(b0) {\n"
"  float2 texel; float mode_; float pad_;\n"
"  float2 srcSize; float2 outSize;\n"
"};\n"
/* Sharp bilinear: keep the nearest-neighbour look, but confine the blend to
   the single screen pixel that straddles a texel boundary.  Without this a
   non-integer upscale (640->1440 is 2.25x) makes some source pixels 2 screen
   pixels wide and others 3, which reads as uneven, shimmering UI text. */
"float2 sharp_uv(float2 uv) {\n"
"  float2 p = uv * srcSize;\n"
"  float2 b = floor(p) + 0.5;\n"
"  float2 f = p - b;\n"
"  float2 s = outSize / srcSize;\n"
"  float2 t = clamp(f * s, -0.5, 0.5);\n"
"  return (b + t) / srcSize;\n"
"}\n"
"float4 lookup(float2 uv) {\n"
"  float  i = IndexTex.SampleLevel(Samp, uv, 0).r;\n"
"  return PalTex.Load(int3((int)(i * 255.0 + 0.5), 0, 0));\n"
"}\n"
"float4 main(float4 pos : SV_Position, float2 uv : TEXCOORD0) : SV_Target {\n"
"  if (mode_ < 0.5) return lookup(uv);\n"
"  float2 p = (mode_ > 1.5 ? sharp_uv(uv) : uv) / texel - 0.5;\n"
"  float2 f = frac(p);\n"
"  float2 b = (floor(p) + 0.5) * texel;\n"
"  float4 c00 = lookup(b);\n"
"  float4 c10 = lookup(b + float2(texel.x, 0));\n"
"  float4 c01 = lookup(b + float2(0, texel.y));\n"
"  float4 c11 = lookup(b + texel);\n"
"  return lerp(lerp(c00, c10, f.x), lerp(c01, c11, f.x), f.y);\n"
"}\n";

static const char *PS_RGB_SRC =
"Texture2D<float4> SrcTex : register(t0);\n"
"SamplerState      Samp   : register(s0);\n"
"cbuffer Params : register(b0) {\n"
"  float2 texel; float mode_; float pad_;\n"
"  float2 srcSize; float2 outSize;\n"
"};\n"
"float4 main(float4 pos : SV_Position, float2 uv : TEXCOORD0) : SV_Target {\n"
"  if (mode_ > 1.5) {\n"
"    float2 p = uv * srcSize;\n"
"    float2 b = floor(p) + 0.5;\n"
"    float2 s = outSize / srcSize;\n"
"    uv = (b + clamp(p - b, -0.5, 0.5) * s) / srcSize;\n"
"  }\n"
"  return SrcTex.Sample(Samp, uv);\n"
"}\n";

typedef struct { float texel_x, texel_y, mode_, pad_;
                 float src_w, src_h, out_w, out_h; } ps_params_t;
static ID3D11Buffer *g_cb;

static PFN_D3DCOMPILE load_compiler(void)
{
    static PFN_D3DCOMPILE fn;
    static int tried;
    if (!tried) {
        tried = 1;
        HMODULE m = LoadLibraryA("d3dcompiler_47.dll");
        if (!m) m = LoadLibraryA("d3dcompiler_43.dll");
        if (m) *(FARPROC *)&fn = GetProcAddress(m, "D3DCompile");
    }
    return fn;
}

static ID3DBlob *compile(const char *src, const char *target)
{
    PFN_D3DCOMPILE d3dcompile = load_compiler();
    ID3DBlob *code = NULL, *err = NULL;
    if (!d3dcompile) return NULL;
    if (FAILED(d3dcompile(src, strlen(src), NULL, NULL, NULL,
                          "main", target, 0, 0, &code, &err))) {
        SAFE_RELEASE(err);
        return NULL;
    }
    SAFE_RELEASE(err);
    return code;
}

/* -------------------------------------------------------------- resources */

static void release_target(void)
{
    if (G.ctx) ID3D11DeviceContext_OMSetRenderTargets(G.ctx, 0, NULL, NULL);
    SAFE_RELEASE(G.rtv);
}

static HRESULT create_target(void)
{
    ID3D11Texture2D *bb = NULL;
    HRESULT hr = IDXGISwapChain_GetBuffer(G.swap, 0, &IID_ID3D11Texture2D, (void **)&bb);
    if (FAILED(hr)) return hr;
    hr = ID3D11Device_CreateRenderTargetView(G.dev, (ID3D11Resource *)bb, NULL, &G.rtv);
    SAFE_RELEASE(bb);
    return hr;
}

static void release_source(void)
{
    SAFE_RELEASE(G.srv_src);
    SAFE_RELEASE(G.tex_src);
}

static HRESULT create_source(int w, int h, present_format_t fmt)
{
    D3D11_TEXTURE2D_DESC td;
    HRESULT hr;

    release_source();
    memset(&td, 0, sizeof td);
    td.Width          = w;
    td.Height         = h;
    td.MipLevels      = 1;
    td.ArraySize      = 1;
    td.Format         = (fmt == PRESENT_FMT_P8) ? DXGI_FORMAT_R8_UNORM
                                                : DXGI_FORMAT_B8G8R8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage          = D3D11_USAGE_DYNAMIC;
    td.BindFlags      = D3D11_BIND_SHADER_RESOURCE;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;

    hr = ID3D11Device_CreateTexture2D(G.dev, &td, NULL, &G.tex_src);
    if (FAILED(hr)) return hr;
    return ID3D11Device_CreateShaderResourceView(G.dev, (ID3D11Resource *)G.tex_src,
                                                 NULL, &G.srv_src);
}

static HRESULT create_palette_tex(void)
{
    D3D11_TEXTURE2D_DESC td;
    HRESULT hr;
    memset(&td, 0, sizeof td);
    td.Width = 256; td.Height = 1; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DYNAMIC;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    hr = ID3D11Device_CreateTexture2D(G.dev, &td, NULL, &G.tex_pal);
    if (FAILED(hr)) return hr;
    return ID3D11Device_CreateShaderResourceView(G.dev, (ID3D11Resource *)G.tex_pal,
                                                 NULL, &G.srv_pal);
}

/* ------------------------------------------------------------------- init */

HRESULT present_init(HWND hwnd, const present_config_t *cfg)
{
    DXGI_SWAP_CHAIN_DESC sd;
    D3D_FEATURE_LEVEL got;
    D3D11_SAMPLER_DESC sdesc;
    D3D11_BUFFER_DESC bd;
    ID3DBlob *vsb, *psb;
    RECT rc;
    HRESULT hr;
    UINT flags = 0;

    memset(&G, 0, sizeof G);
    G.hwnd = hwnd;
    G.cfg  = *cfg;

    GetClientRect(hwnd, &rc);
    G.client_w = rc.right  - rc.left;
    G.client_h = rc.bottom - rc.top;
    if (G.client_w <= 0) G.client_w = cfg->width;
    if (G.client_h <= 0) G.client_h = cfg->height;

    memset(&sd, 0, sizeof sd);
    sd.BufferCount        = 2;
    sd.BufferDesc.Width   = G.client_w;
    sd.BufferDesc.Height  = G.client_h;
    sd.BufferDesc.Format  = DXGI_FORMAT_B8G8R8A8_UNORM;
    sd.BufferUsage        = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.OutputWindow       = hwnd;
    sd.SampleDesc.Count   = 1;
    sd.Windowed           = TRUE;   /* borderless, never exclusive: DWM-friendly */
    sd.SwapEffect         = DXGI_SWAP_EFFECT_DISCARD;

#ifdef IGN_D3D_DEBUG
    flags |= D3D11_CREATE_DEVICE_DEBUG;
#endif

    hr = D3D11CreateDeviceAndSwapChain(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, flags,
                                       NULL, 0, D3D11_SDK_VERSION, &sd, &G.swap,
                                       &G.dev, &got, &G.ctx);
    if (FAILED(hr))
        hr = D3D11CreateDeviceAndSwapChain(NULL, D3D_DRIVER_TYPE_WARP, NULL, flags,
                                           NULL, 0, D3D11_SDK_VERSION, &sd, &G.swap,
                                           &G.dev, &got, &G.ctx);
    if (FAILED(hr)) return hr;

    /* The game manages its own window and Alt-Tab; stop DXGI fighting it. */
    {
        IDXGIDevice  *dxdev  = NULL;
        IDXGIAdapter *adap   = NULL;
        IDXGIFactory *fact   = NULL;
        if (SUCCEEDED(ID3D11Device_QueryInterface(G.dev, &IID_IDXGIDevice, (void **)&dxdev)) &&
            SUCCEEDED(IDXGIDevice_GetAdapter(dxdev, &adap)) &&
            SUCCEEDED(IDXGIAdapter_GetParent(adap, &IID_IDXGIFactory, (void **)&fact))) {
            IDXGIFactory_MakeWindowAssociation(fact, hwnd,
                DXGI_MWA_NO_WINDOW_CHANGES | DXGI_MWA_NO_ALT_ENTER);
        }
        SAFE_RELEASE(fact); SAFE_RELEASE(adap); SAFE_RELEASE(dxdev);
    }

    if (FAILED(hr = create_target()))        goto fail;
    if (FAILED(hr = create_palette_tex()))   goto fail;
    if (FAILED(hr = create_source(cfg->width, cfg->height, cfg->format))) goto fail;

    vsb = compile(VS_SRC, "vs_4_0");
    if (!vsb) { hr = E_FAIL; goto fail; }
    hr = ID3D11Device_CreateVertexShader(G.dev, ID3D10Blob_GetBufferPointer(vsb),
                                         ID3D10Blob_GetBufferSize(vsb), NULL, &G.vs);
    SAFE_RELEASE(vsb);
    if (FAILED(hr)) goto fail;

    psb = compile(PS_P8_SRC, "ps_4_0");
    if (!psb) { hr = E_FAIL; goto fail; }
    hr = ID3D11Device_CreatePixelShader(G.dev, ID3D10Blob_GetBufferPointer(psb),
                                        ID3D10Blob_GetBufferSize(psb), NULL, &G.ps_p8);
    SAFE_RELEASE(psb);
    if (FAILED(hr)) goto fail;

    psb = compile(PS_RGB_SRC, "ps_4_0");
    if (!psb) { hr = E_FAIL; goto fail; }
    hr = ID3D11Device_CreatePixelShader(G.dev, ID3D10Blob_GetBufferPointer(psb),
                                        ID3D10Blob_GetBufferSize(psb), NULL, &G.ps_rgb);
    SAFE_RELEASE(psb);
    if (FAILED(hr)) goto fail;

    memset(&sdesc, 0, sizeof sdesc);
    sdesc.Filter   = D3D11_FILTER_MIN_MAG_MIP_POINT;
    sdesc.AddressU = sdesc.AddressV = sdesc.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sdesc.ComparisonFunc = D3D11_COMPARISON_NEVER;
    sdesc.MaxLOD = D3D11_FLOAT32_MAX;
    if (FAILED(hr = ID3D11Device_CreateSamplerState(G.dev, &sdesc, &G.samp_point))) goto fail;
    sdesc.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    if (FAILED(hr = ID3D11Device_CreateSamplerState(G.dev, &sdesc, &G.samp_linear))) goto fail;

    memset(&bd, 0, sizeof bd);
    bd.ByteWidth      = sizeof(ps_params_t);
    bd.Usage          = D3D11_USAGE_DYNAMIC;
    bd.BindFlags      = D3D11_BIND_CONSTANT_BUFFER;
    bd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    if (FAILED(hr = ID3D11Device_CreateBuffer(G.dev, &bd, NULL, &g_cb))) goto fail;

    G.palette_dirty = 1;
    G.ready = 1;
    return S_OK;

fail:
    present_shutdown();
    return hr;
}

void present_shutdown(void)
{
    G.ready = 0;
    release_target();
    release_source();
    SAFE_RELEASE(g_cb);
    SAFE_RELEASE(G.srv_pal);   SAFE_RELEASE(G.tex_pal);
    SAFE_RELEASE(G.samp_point);SAFE_RELEASE(G.samp_linear);
    SAFE_RELEASE(G.ps_p8);     SAFE_RELEASE(G.ps_rgb);
    SAFE_RELEASE(G.vs);
    SAFE_RELEASE(G.swap);
    SAFE_RELEASE(G.ctx);
    SAFE_RELEASE(G.dev);
    G.hwnd = NULL;
}

HRESULT present_set_mode(int width, int height, present_format_t fmt)
{
    if (!G.ready) return E_FAIL;
    if (width == G.cfg.width && height == G.cfg.height && fmt == G.cfg.format)
        return S_OK;
    G.cfg.width  = width;
    G.cfg.height = height;
    G.cfg.format = fmt;
    return create_source(width, height, fmt);
}

void present_resize(int client_w, int client_h)
{
    if (!G.ready || client_w <= 0 || client_h <= 0) return;
    if (client_w == G.client_w && client_h == G.client_h) return;
    G.client_w = client_w;
    G.client_h = client_h;
    release_target();
    IDXGISwapChain_ResizeBuffers(G.swap, 0, client_w, client_h,
                                 DXGI_FORMAT_UNKNOWN, 0);
    create_target();
}

void present_set_palette(const uint32_t *bgrx256)
{
    memcpy(G.palette, bgrx256, sizeof G.palette);
    G.palette_dirty = 1;
}

void present_get_client_size(int *w, int *h)
{
    if (w) *w = G.client_w;
    if (h) *h = G.client_h;
}

/* --------------------------------------------------------------- viewport */

static void compute_viewport(D3D11_VIEWPORT *vp)
{
    float sw = (float)G.cfg.width, sh = (float)G.cfg.height;
    float dw = (float)G.client_w,  dh = (float)G.client_h;
    float w, h;

    switch (G.cfg.scaling) {
    case PRESENT_SCALE_INTEGER: {
        int k = (int)(dw / sw);
        int k2 = (int)(dh / sh);
        if (k2 < k) k = k2;
        if (k < 1) k = 1;
        w = sw * k; h = sh * k;
        break;
    }
    case PRESENT_SCALE_ASPECT: {
        /* 320x240 / 640x480 are already 4:3; 320x200 is not - it was displayed
         * on a 4:3 CRT, so correct to 4:3 rather than to the pixel ratio. */
        float target = 4.0f / 3.0f;
        if (dw / dh > target) { h = dh; w = dh * target; }
        else                  { w = dw; h = dw / target; }
        break;
    }
    default:
        w = dw; h = dh;
        break;
    }
    vp->TopLeftX = (dw - w) * 0.5f;
    vp->TopLeftY = (dh - h) * 0.5f;
    vp->Width    = w;
    vp->Height   = h;
    vp->MinDepth = 0.0f;
    vp->MaxDepth = 1.0f;
}

/* ------------------------------------------------------------------ frame */

HRESULT present_frame(const void *pixels, int pitch)
{
    D3D11_MAPPED_SUBRESOURCE map;
    D3D11_VIEWPORT vp;
    const float clear[4] = { 0, 0, 0, 1 };
    ID3D11ShaderResourceView *srvs[2];
    ID3D11SamplerState *samp;
    int y, is_p8;
    HRESULT hr;

    if (!G.ready) return E_FAIL;
    is_p8 = (G.cfg.format == PRESENT_FMT_P8);

    /* --- upload the frame -------------------------------------------- */
    hr = ID3D11DeviceContext_Map(G.ctx, (ID3D11Resource *)G.tex_src, 0,
                                 D3D11_MAP_WRITE_DISCARD, 0, &map);
    if (FAILED(hr)) return hr;
    {
        const uint8_t *src = (const uint8_t *)pixels;
        uint8_t *dst = (uint8_t *)map.pData;
        if (is_p8) {
            for (y = 0; y < G.cfg.height; y++)
                memcpy(dst + (size_t)y * map.RowPitch, src + (size_t)y * pitch,
                       (size_t)G.cfg.width);
        } else if (G.cfg.format == PRESENT_FMT_XRGB888) {
            for (y = 0; y < G.cfg.height; y++)
                memcpy(dst + (size_t)y * map.RowPitch, src + (size_t)y * pitch,
                       (size_t)G.cfg.width * 4);
        } else { /* RGB565 -> BGRA8 */
            for (y = 0; y < G.cfg.height; y++) {
                const uint16_t *s = (const uint16_t *)(src + (size_t)y * pitch);
                uint32_t *d = (uint32_t *)(dst + (size_t)y * map.RowPitch);
                int x;
                for (x = 0; x < G.cfg.width; x++) {
                    uint16_t p = s[x];
                    uint32_t r = (p >> 11) & 0x1F, g = (p >> 5) & 0x3F, b = p & 0x1F;
                    d[x] = 0xFF000000u
                         | ((r * 255 / 31) << 16)
                         | ((g * 255 / 63) << 8)
                         |  (b * 255 / 31);
                }
            }
        }
    }
    ID3D11DeviceContext_Unmap(G.ctx, (ID3D11Resource *)G.tex_src, 0);

    /* --- upload the palette if it moved ------------------------------- */
    if (is_p8 && G.palette_dirty) {
        if (SUCCEEDED(ID3D11DeviceContext_Map(G.ctx, (ID3D11Resource *)G.tex_pal, 0,
                                              D3D11_MAP_WRITE_DISCARD, 0, &map))) {
            memcpy(map.pData, G.palette, sizeof G.palette);
            ID3D11DeviceContext_Unmap(G.ctx, (ID3D11Resource *)G.tex_pal, 0);
            G.palette_dirty = 0;
        }
    }

    compute_viewport(&vp);
    G.vp_w = vp.Width;
    G.vp_h = vp.Height;

    /* --- shader params ------------------------------------------------ */
    if (SUCCEEDED(ID3D11DeviceContext_Map(G.ctx, (ID3D11Resource *)g_cb, 0,
                                          D3D11_MAP_WRITE_DISCARD, 0, &map))) {
        ps_params_t *p = (ps_params_t *)map.pData;
        p->texel_x = 1.0f / (float)G.cfg.width;
        p->texel_y = 1.0f / (float)G.cfg.height;
        p->mode_   = (float)G.cfg.filter_mode;
        p->pad_    = 0.0f;
        p->src_w   = (float)G.cfg.width;
        p->src_h   = (float)G.cfg.height;
        p->out_w   = G.vp_w > 0 ? G.vp_w : (float)G.client_w;
        p->out_h   = G.vp_h > 0 ? G.vp_h : (float)G.client_h;
        ID3D11DeviceContext_Unmap(G.ctx, (ID3D11Resource *)g_cb, 0);
    }

    /* --- draw --------------------------------------------------------- */
    ID3D11DeviceContext_OMSetRenderTargets(G.ctx, 1, &G.rtv, NULL);
    ID3D11DeviceContext_ClearRenderTargetView(G.ctx, G.rtv, clear);
    ID3D11DeviceContext_RSSetViewports(G.ctx, 1, &vp);
    ID3D11DeviceContext_IASetInputLayout(G.ctx, NULL);
    ID3D11DeviceContext_IASetPrimitiveTopology(G.ctx,
            D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_VSSetShader(G.ctx, G.vs, NULL, 0);
    ID3D11DeviceContext_PSSetShader(G.ctx, is_p8 ? G.ps_p8 : G.ps_rgb, NULL, 0);
    ID3D11DeviceContext_PSSetConstantBuffers(G.ctx, 0, 1, &g_cb);

    srvs[0] = G.srv_src;
    srvs[1] = G.srv_pal;
    ID3D11DeviceContext_PSSetShaderResources(G.ctx, 0, is_p8 ? 2 : 1, srvs);
    /* Index lookups must be point-sampled; smoothing happens post-lookup. */
    samp = (is_p8 || G.cfg.filter_mode == 0) ? G.samp_point : G.samp_linear;
    ID3D11DeviceContext_PSSetSamplers(G.ctx, 0, 1, &samp);

    ID3D11DeviceContext_Draw(G.ctx, 3, 0);
    return IDXGISwapChain_Present(G.swap, G.cfg.vsync ? 1 : 0, 0);
}
