/*
 * Backward LZ77 ("BLZ") as used by 3DS ExeFS .code and DQXI's PACA archives.
 *
 * Stream is decoded from the end of the buffer towards the start. Footer (8 bytes):
 *   u32 bufferTopAndBottom : low 24 bits = size of compressed region (from end),
 *                            high 8 bits = footer+padding size
 *   u32 originalBottom     : decompressed size - compressed size
 * Flag byte MSB first: 0 = literal byte, 1 = 2-byte token
 *   token = (hi << 8) | lo, length = (token >> 12) + 3, distance = (token & 0xFFF) + 3
 *
 * Build: gcc -O2 -shared -o blz.dll blz.c
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

/* Returns decompressed size, or negative on error. out must hold blz_decompressed_size(). */
EXPORT int64_t blz_decompressed_size(const uint8_t *in, uint32_t in_size)
{
    if (in_size < 8) return -1;
    uint32_t extra;
    memcpy(&extra, in + in_size - 4, 4);
    return (int64_t)in_size + extra;
}

EXPORT int blz_decompress(const uint8_t *in, uint32_t in_size, uint8_t *out, uint32_t out_size)
{
    if (in_size < 8) return -1;
    uint32_t tb, extra;
    memcpy(&tb, in + in_size - 8, 4);
    memcpy(&extra, in + in_size - 4, 4);
    uint32_t top = tb & 0xFFFFFF, bottom = tb >> 24;
    if (bottom < 8 || bottom > 11 || top < bottom || top > in_size) return -2;
    if (out_size != in_size + extra) return -3;
    memcpy(out, in, in_size);
    int64_t dst = out_size;
    int64_t src = in_size - bottom;
    int64_t end = in_size - top;
    while (src > end) {
        uint8_t flag = out[--src];
        for (int i = 0; i < 8; i++) {
            if (!((flag << i) & 0x80)) {
                if (dst - end < 1 || src - end < 1) return -4;
                out[--dst] = out[--src];
            } else {
                if (src - end < 2) return -5;
                uint32_t hi = out[--src];
                uint32_t lo = out[--src];
                uint32_t tok = (hi << 8) | lo;
                uint32_t off = (tok & 0x0FFF) + 3;
                uint32_t len = ((tok >> 12) & 0xF) + 3;
                if (dst - end < (int64_t)len) return -6;
                if (dst + off > out_size) return -7;
                for (uint32_t j = 0; j < len; j++) {
                    out[dst - 1] = out[dst - 1 + off];
                    dst--;
                }
            }
            if (src <= end) break;
        }
    }
    return 0;
}

/*
 * Compress. out must be at least in_size bytes. Returns compressed size,
 * 0 if data does not compress (caller should store raw), negative on error.
 */
#define MAX_LEN (0xF + 3)
#define MAX_DIST (0xFFF + 3)

EXPORT int64_t blz_compress(const uint8_t *in, uint32_t in_size, uint8_t *out, uint32_t out_cap)
{
    if (in_size <= 8 || out_cap < in_size) return 0;
    /* hash chains over 3-byte prefixes, searching towards higher addresses (backwards coding) */
    const int HBITS = 16;
    int32_t *head = (int32_t *)malloc(sizeof(int32_t) << HBITS);
    int32_t *prev = (int32_t *)malloc(sizeof(int32_t) * (size_t)in_size);
    uint8_t *tmp = (uint8_t *)malloc(in_size + 16);
    if (!head || !prev || !tmp) { free(head); free(prev); free(tmp); return -1; }
    for (int i = 0; i < (1 << HBITS); i++) head[i] = -1;

    /* We emit compressed stream into tmp growing downward from tmp+in_size.
       Position p denotes "bytes [0,p) not yet encoded"; next symbol ends at p. */
    int64_t p = in_size;
    int64_t d = in_size;               /* write cursor in tmp (downward) */
    int ok = 1;
    /* insert positions as we pass them: positions q >= p can be referenced as
       match sources. A match for bytes [p-len, p) copies from [p-len+dist, p+dist). */
#define HASH3(q) ((((uint32_t)in[(q)] << 8) ^ ((uint32_t)in[(q)+1] << 4) ^ (uint32_t)in[(q)+2]) * 2654435761u >> (32 - HBITS))
    int64_t inserted = in_size;        /* positions >= inserted are in hash (as start positions of 3-byte windows) */
    while (p > 0 && ok) {
        int64_t flagpos = --d;
        if (d < 0) { ok = 0; break; }
        tmp[flagpos] = 0;
        for (int i = 0; i < 8; i++) {
            /* ensure all window starts q in [p, in_size-3] inserted */
            while (inserted > p) {
                inserted--;
                if (inserted + 3 <= in_size) {
                    uint32_t h = HASH3(inserted);
                    prev[inserted] = head[h];
                    head[h] = (int32_t)inserted;
                }
            }
            int best_len = 0, best_dist = 0;
            int maxlen = MAX_LEN;
            if (maxlen > p) maxlen = (int)p;
            if (maxlen >= 3) {
                /* candidate source windows start at s = (p - len) + dist; use 3-byte hash of target end region */
                int64_t t = p - 3;            /* target last-3-bytes start */
                uint32_t h = HASH3(t);
                int32_t s = head[h];
                int chain = 256;
                while (s >= 0 && chain-- > 0) {
                    int64_t dist = s - t;
                    if (dist > MAX_DIST) break;
                    if (dist >= 3) {
                        /* extend match backwards: compare in[p-1-k] with in[p-1-k+dist] */
                        int l = 0;
                        while (l < maxlen && p - 1 - l + dist < in_size && in[p - 1 - l] == in[p - 1 - l + dist]) l++;
                        if (l > best_len) { best_len = l; best_dist = (int)dist; if (l == maxlen) break; }
                    }
                    s = prev[s];
                }
            }
            if (best_len >= 3) {
                if (d < 2) { ok = 0; break; }
                tmp[flagpos] |= (uint8_t)(0x80 >> i);
                uint32_t tok = ((uint32_t)(best_len - 3) << 12) | (uint32_t)(best_dist - 3);
                tmp[--d] = (uint8_t)(tok >> 8);
                tmp[--d] = (uint8_t)(tok & 0xFF);
                p -= best_len;
            } else {
                if (d < 1) { ok = 0; break; }
                tmp[--d] = in[--p];
            }
            if (p <= 0) break;
        }
    }
    free(head); free(prev);
    if (!ok) { free(tmp); return 0; }

    /* tmp[d, in_size) holds the full backward stream. Find the largest raw prefix such that
       in-place decoding never overwrites unread compressed bytes. */
    uint8_t *cbuf = tmp + d;
    int64_t csize = in_size - d;
    int64_t orig = in_size, cpos = csize;
    int64_t orig_safe = 0, comp_safe = 0;
    int over = 0;
    while (orig > 0) {
        uint8_t flag = cbuf[--cpos];
        for (int i = 0; i < 8; i++) {
            if (!((flag << i) & 0x80)) {
                cpos--; orig--;
            } else {
                int len = ((cbuf[--cpos] >> 4) & 0xF) + 3;
                cpos--;
                orig -= len;
                if (orig < cpos) { orig_safe = orig; comp_safe = cpos; over = 1; break; }
            }
            if (orig <= 0) break;
        }
        if (over) break;
    }
    int64_t comp_len = csize - comp_safe;
    int64_t pad_off = orig_safe + comp_len;
    int64_t foot_off = (pad_off + 3) & ~3LL;
    int64_t total = foot_off + 8;
    int64_t top = total - orig_safe;
    int64_t bottom = total - pad_off;
    if (total >= in_size || top > 0xFFFFFF) { free(tmp); return 0; }
    memcpy(out, in, (size_t)orig_safe);
    memmove(out + orig_safe, cbuf + comp_safe, (size_t)comp_len);
    memset(out + pad_off, 0xFF, (size_t)(foot_off - pad_off));
    uint32_t tb = (uint32_t)top | ((uint32_t)bottom << 24);
    uint32_t extra = (uint32_t)(in_size - total);
    memcpy(out + foot_off, &tb, 4);
    memcpy(out + foot_off + 4, &extra, 4);
    free(tmp);
    return total;
}
