/* prim_gpu.c - draw Ignition's own primitive list with Direct3D 11.
 *
 * The game's rasterizer is handed a NULL-terminated list of records whose
 * vertices are already screen-space, in 24.8 fixed point, with texture
 * coordinates in 8.8 texels of a 256x256 page. Nothing in that list is tied to
 * a resolution - the same list drawn into a bigger target just makes a bigger
 * picture. That is what lets this game exceed the 800x600 its 488,000-byte
 * static framebuffer caps it at.
 *
 * The render target is R8_UINT - palette *indices*, not colours. That is
 * deliberate: the game's translucency is a lookup table indexed by
 * [source_index << 8 | destination_index], so blending only means anything
 * while the target still holds indices. Conversion to colour happens once, at
 * present time.
 *
 * Textures are read straight out of the game's own memory rather than from the
 * .TEX files. The texture area is allocated at run time and its address
 * changes between runs, so reading the live pages is both simpler and immune
 * to that - the pointer in each record IS the address of the page.
 */
#define COBJMACROS
#define CINTERFACE
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <string.h>
#include <stdlib.h>
#include "prim_gpu.h"
#include "d3d11_present.h"
#include "ignlog.h"

#ifndef SAFE_RELEASE
#define SAFE_RELEASE(p) do { if (p) { (p)->lpVtbl->Release(p); (p) = NULL; } } while (0)
#endif

#define PAGE_W 256
#define PAGE_H 256
#define MAX_VERTS 65536

typedef struct { float x, y, u, v; } vtx_t;
typedef struct { float inv_w, inv_h, pad0, pad1; } vs_params_t;

static struct {
    ID3D11Device            *dev;
    ID3D11DeviceContext     *ctx;
    ID3D11Texture2D         *target;      /* R8_UINT indices */
    ID3D11RenderTargetView  *rtv;
    ID3D11ShaderResourceView*srv;
    ID3D11Texture2D         *page;        /* one 256x256 texture page */
    ID3D11ShaderResourceView*page_srv;
    ID3D11Buffer            *vb, *cb;
    ID3D11VertexShader      *vs;
    ID3D11PixelShader       *ps;
    ID3D11InputLayout       *layout;
    int  scale, w, h, ready, have_frame;
    vtx_t *verts;
    int    nverts;
} G;

static const char *VS_SRC =
"cbuffer P : register(b0) { float2 inv; float2 pad; };\n"
"struct IN  { float2 pos : POSITION; float2 uv : TEXCOORD0; };\n"
"struct OUT { float4 pos : SV_Position; float2 uv : TEXCOORD0; };\n"
"OUT main(IN i) {\n"
"  OUT o;\n"
"  o.pos = float4(i.pos.x * inv.x * 2.0 - 1.0, 1.0 - i.pos.y * inv.y * 2.0, 0, 1);\n"
"  o.uv  = i.uv;\n"
"  return o;\n"
"}\n";

/* Writes a palette index, not a colour. Texel coordinates wrap at 256 for
 * free, exactly as the game's `(v<<8)|u` addressing does. */
static const char *PS_SRC =
"Texture2D<uint> Page : register(t0);\n"
"uint main(float4 pos : SV_Position, float2 uv : TEXCOORD0) : SV_Target {\n"
"  int2 t = int2((int)uv.x & 255, (int)uv.y & 255);\n"
"  return Page.Load(int3(t, 0));\n"
"}\n";

typedef HRESULT (WINAPI *PFN_D3DCOMPILE)(LPCVOID, SIZE_T, LPCSTR,
        const D3D_SHADER_MACRO *, ID3DInclude *, LPCSTR, LPCSTR,
        UINT, UINT, ID3DBlob **, ID3DBlob **);

static ID3DBlob *compile(const char *src, const char *target)
{
    static PFN_D3DCOMPILE fn;
    ID3DBlob *code = NULL, *err = NULL;
    if (!fn) {
        HMODULE m = LoadLibraryA("d3dcompiler_47.dll");
        if (!m) m = LoadLibraryA("d3dcompiler_43.dll");
        if (m) *(FARPROC *)&fn = GetProcAddress(m, "D3DCompile");
        if (!fn) return NULL;
    }
    if (FAILED(fn(src, strlen(src), NULL, NULL, NULL, "main", target, 0, 0, &code, &err))) {
        if (err) { IGNLOG("prim_gpu: shader error: %s", (const char *)ID3D10Blob_GetBufferPointer(err)); }
        SAFE_RELEASE(err);
        return NULL;
    }
    SAFE_RELEASE(err);
    return code;
}

static int readable(const void *p, unsigned n)
{
    MEMORY_BASIC_INFORMATION mbi;
    if (!p || !VirtualQuery(p, &mbi, sizeof mbi)) return 0;
    if (mbi.State != MEM_COMMIT || (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD))) return 0;
    return (UINT_PTR)mbi.BaseAddress + mbi.RegionSize >= (UINT_PTR)p + n;
}

int prim_gpu_init(int scale)
{
    D3D11_TEXTURE2D_DESC td;
    D3D11_BUFFER_DESC bd;
    ID3DBlob *vsb, *psb;
    D3D11_INPUT_ELEMENT_DESC el[2] = {
        { "POSITION", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 0,  D3D11_INPUT_PER_VERTEX_DATA, 0 },
        { "TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 8,  D3D11_INPUT_PER_VERTEX_DATA, 0 },
    };

    if (G.ready) return 1;
    present_get_device((void **)&G.dev, (void **)&G.ctx);
    if (!G.dev || !G.ctx) { IGNLOG("prim_gpu: no device yet"); return 0; }
    if (scale < 1) scale = 1;
    G.scale = scale;

    G.verts = (vtx_t *)calloc(MAX_VERTS, sizeof(vtx_t));
    if (!G.verts) return 0;

    memset(&td, 0, sizeof td);
    td.Width = PAGE_W; td.Height = PAGE_H; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8_UINT; td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DYNAMIC; td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    if (FAILED(ID3D11Device_CreateTexture2D(G.dev, &td, NULL, &G.page))) goto fail;
    if (FAILED(ID3D11Device_CreateShaderResourceView(G.dev, (ID3D11Resource *)G.page,
                                                     NULL, &G.page_srv))) goto fail;

    memset(&bd, 0, sizeof bd);
    bd.ByteWidth = MAX_VERTS * sizeof(vtx_t);
    bd.Usage = D3D11_USAGE_DYNAMIC; bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    bd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    if (FAILED(ID3D11Device_CreateBuffer(G.dev, &bd, NULL, &G.vb))) goto fail;
    bd.ByteWidth = sizeof(vs_params_t); bd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    if (FAILED(ID3D11Device_CreateBuffer(G.dev, &bd, NULL, &G.cb))) goto fail;

    vsb = compile(VS_SRC, "vs_4_0");
    if (!vsb) goto fail;
    if (FAILED(ID3D11Device_CreateVertexShader(G.dev, ID3D10Blob_GetBufferPointer(vsb),
            ID3D10Blob_GetBufferSize(vsb), NULL, &G.vs))) { SAFE_RELEASE(vsb); goto fail; }
    if (FAILED(ID3D11Device_CreateInputLayout(G.dev, el, 2, ID3D10Blob_GetBufferPointer(vsb),
            ID3D10Blob_GetBufferSize(vsb), &G.layout))) { SAFE_RELEASE(vsb); goto fail; }
    SAFE_RELEASE(vsb);

    psb = compile(PS_SRC, "ps_4_0");
    if (!psb) goto fail;
    if (FAILED(ID3D11Device_CreatePixelShader(G.dev, ID3D10Blob_GetBufferPointer(psb),
            ID3D10Blob_GetBufferSize(psb), NULL, &G.ps))) { SAFE_RELEASE(psb); goto fail; }
    SAFE_RELEASE(psb);

    G.ready = 1;
    IGNLOG("prim_gpu: ready, scale %dx", scale);
    return 1;
fail:
    IGNLOG("prim_gpu: init failed");
    prim_gpu_shutdown();
    return 0;
}

static int ensure_target(int w, int h)
{
    D3D11_TEXTURE2D_DESC td;
    if (G.target && G.w == w && G.h == h) return 1;
    SAFE_RELEASE(G.srv); SAFE_RELEASE(G.rtv); SAFE_RELEASE(G.target);
    memset(&td, 0, sizeof td);
    td.Width = w; td.Height = h; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8_UINT; td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    if (FAILED(ID3D11Device_CreateTexture2D(G.dev, &td, NULL, &G.target))) return 0;
    if (FAILED(ID3D11Device_CreateRenderTargetView(G.dev, (ID3D11Resource *)G.target,
                                                   NULL, &G.rtv))) return 0;
    if (FAILED(ID3D11Device_CreateShaderResourceView(G.dev, (ID3D11Resource *)G.target,
                                                     NULL, &G.srv))) return 0;
    G.w = w; G.h = h;
    IGNLOG("prim_gpu: target %dx%d", w, h);
    return 1;
}

static void upload_page(const BYTE *page)
{
    D3D11_MAPPED_SUBRESOURCE m;
    int y;
    if (FAILED(ID3D11DeviceContext_Map(G.ctx, (ID3D11Resource *)G.page, 0,
                                       D3D11_MAP_WRITE_DISCARD, 0, &m))) return;
    for (y = 0; y < PAGE_H; y++)
        memcpy((BYTE *)m.pData + (size_t)y * m.RowPitch, page + (size_t)y * PAGE_W, PAGE_W);
    ID3D11DeviceContext_Unmap(G.ctx, (ID3D11Resource *)G.page, 0);
}

static void flush(const BYTE *page)
{
    D3D11_MAPPED_SUBRESOURCE m;
    UINT stride = sizeof(vtx_t), off = 0;
    if (G.nverts < 3 || !page) { G.nverts = 0; return; }
    if (SUCCEEDED(ID3D11DeviceContext_Map(G.ctx, (ID3D11Resource *)G.vb, 0,
                                          D3D11_MAP_WRITE_DISCARD, 0, &m))) {
        memcpy(m.pData, G.verts, (size_t)G.nverts * sizeof(vtx_t));
        ID3D11DeviceContext_Unmap(G.ctx, (ID3D11Resource *)G.vb, 0);
    }
    upload_page(page);
    ID3D11DeviceContext_PSSetShaderResources(G.ctx, 0, 1, &G.page_srv);
    ID3D11DeviceContext_IASetVertexBuffers(G.ctx, 0, 1, &G.vb, &stride, &off);
    ID3D11DeviceContext_Draw(G.ctx, (UINT)G.nverts, 0);
    G.nverts = 0;
}

int prim_gpu_render(const void *block)
{
    const BYTE *b = (const BYTE *)block;
    const BYTE *const *list;
    D3D11_VIEWPORT vp;
    D3D11_MAPPED_SUBRESOURCE m;
    const UINT clear[4] = { 0, 0, 0, 0 };
    const BYTE *cur_page = NULL;
    int x0, y0, x1, y1, w, h, i;

    if (!G.ready || !readable(b, 0x20)) return 0;
    x0 = *(const int *)(b + 0x10); y0 = *(const int *)(b + 0x14);
    x1 = *(const int *)(b + 0x18); y1 = *(const int *)(b + 0x1C);
    w = (x1 - x0 + 1) * G.scale; h = (y1 - y0 + 1) * G.scale;
    if (w <= 0 || h <= 0 || !ensure_target(w, h)) return 0;

    list = *(const BYTE *const *const *)(b + 0x00);
    if (!readable(list, sizeof(void *))) return 0;

    ID3D11DeviceContext_OMSetRenderTargets(G.ctx, 1, &G.rtv, NULL);
    ID3D11DeviceContext_ClearRenderTargetView(G.ctx, G.rtv, (const FLOAT *)clear);
    vp.TopLeftX = 0; vp.TopLeftY = 0; vp.Width = (FLOAT)w; vp.Height = (FLOAT)h;
    vp.MinDepth = 0; vp.MaxDepth = 1;
    ID3D11DeviceContext_RSSetViewports(G.ctx, 1, &vp);
    ID3D11DeviceContext_IASetInputLayout(G.ctx, G.layout);
    ID3D11DeviceContext_IASetPrimitiveTopology(G.ctx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_VSSetShader(G.ctx, G.vs, NULL, 0);
    ID3D11DeviceContext_PSSetShader(G.ctx, G.ps, NULL, 0);
    if (SUCCEEDED(ID3D11DeviceContext_Map(G.ctx, (ID3D11Resource *)G.cb, 0,
                                          D3D11_MAP_WRITE_DISCARD, 0, &m))) {
        vs_params_t *p = (vs_params_t *)m.pData;
        p->inv_w = 1.0f / (float)w; p->inv_h = 1.0f / (float)h;
        p->pad0 = p->pad1 = 0.0f;
        ID3D11DeviceContext_Unmap(G.ctx, (ID3D11Resource *)G.cb, 0);
    }
    ID3D11DeviceContext_VSSetConstantBuffers(G.ctx, 0, 1, &G.cb);

    /* Order is the depth test: the game paints back to front out of depth
     * buckets and the records carry no z, so the list must be drawn in order.
     * Batches therefore break whenever the texture changes, not by material. */
    G.nverts = 0;
    for (i = 0; ; i++) {
        const BYTE *r;
        unsigned type;
        if (!readable(list + i, sizeof(void *)) || !list[i]) break;
        r = list[i];
        if (!readable(r, 0x24)) break;
        type = *(const DWORD *)r;
        if (type == 0x11 || type == 0x16) {
            const BYTE *page = *(const BYTE *const *)(r + 0x20);
            const int  *uv   = (const int *)(*(const BYTE *const *)(r + 0x1C));
            int v;
            if (!readable(page, PAGE_W * PAGE_H) || !readable(uv, 24)) continue;
            if (page != cur_page) { flush(cur_page); cur_page = page; }
            if (G.nverts + 3 > MAX_VERTS) flush(cur_page);
            for (v = 0; v < 3; v++) {
                const int *xy = (const int *)(r + 4 + v * 8);
                vtx_t *o = &G.verts[G.nverts++];
                o->x = (float)(xy[0] / 256.0 - x0) * (float)G.scale;
                o->y = (float)(xy[1] / 256.0 - y0) * (float)G.scale;
                o->u = (float)(uv[v * 2 + 0] / 256.0);
                o->v = (float)(uv[v * 2 + 1] / 256.0);
            }
        }
        /* TODO: 0x12 blended, 0x13 shaded, 0x0F flat, 0x14/0x15 perspective,
         * 0x07 sprites. 0x11/0x16 alone are 459 of 611 records in a race frame. */
    }
    flush(cur_page);
    G.have_frame = 1;
    return 1;
}

int prim_gpu_have_frame(void) { return G.have_frame; }

void prim_gpu_shutdown(void)
{
    SAFE_RELEASE(G.layout); SAFE_RELEASE(G.ps); SAFE_RELEASE(G.vs);
    SAFE_RELEASE(G.cb); SAFE_RELEASE(G.vb);
    SAFE_RELEASE(G.page_srv); SAFE_RELEASE(G.page);
    SAFE_RELEASE(G.srv); SAFE_RELEASE(G.rtv); SAFE_RELEASE(G.target);
    free(G.verts); G.verts = NULL;
    G.ready = 0; G.have_frame = 0;
}
