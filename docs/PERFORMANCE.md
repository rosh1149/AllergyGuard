# Performance and resource use

Measured on the development container: 4 CPU cores, Python 3.13, Tesseract 5, PostgreSQL 16 on the
same machine. Numbers will differ on other hardware; the scripts are in the repository so they can
be re-run.

## Allergen engine and API

From the Stage 4 experiment run (`docs/experiments/2026-10-31-final/`):

| Measure | Value |
|---|---|
| Engine, one analysis (mean / P95) | 3.0 ms / 5.0 ms |
| Engine throughput, one core | 332 analyses/s |
| API `/api/analyze/text`, 8 clients (P50 / P95) | 127 ms / 176 ms |
| API throughput, 8 clients | 54.6 requests/s, 0 errors |

## Photo processing

One job = one 12-megapixel JPEG label photo: decode, quality checks, barcode reading, deskew and
Tesseract OCR. Script: `backend/scripts/photo_bench.py`.

### The problem found

Tesseract uses OpenMP to spread one page over all cores. With two photos processed at once, the two
Tesseract processes competed for the same cores:

| Concurrent jobs | Photos/s | Latency P50 | Latency max | CPU used by OCR |
|---|---|---|---|---|
| 1 | 0.88 | 1.1 s | 1.3 s | 2.2 s for 3 jobs |
| 2 (before the fix) | **0.05** | **38 s** | **63 s** | **295 s for 4 jobs** |

### The fix

`OMP_THREAD_LIMIT=1` is set for Tesseract (`app/packaged/ocr.py`), so each job uses one core and
parallelism comes from running several jobs. The photo-job limit now defaults to one job per core
(`MAX_CONCURRENT_IMAGE_JOBS=0`).

| Concurrent jobs | Photos/s | Latency P50 | Latency max | Peak memory (process) |
|---|---|---|---|---|
| 1 | 0.89 | 1.09 s | 1.23 s | |
| 2 | 1.58 | 1.25 s | 1.29 s | |
| 4 (= cores) | 2.57 | 1.32 s | 1.91 s | 783 MB |
| 8 | 2.79 | 2.48 s | 3.79 s | 1430 MB |

Beyond one job per core, throughput barely rises while latency and memory double, which is why
the limit is one job per core and further requests wait (up to `IMAGE_JOB_WAIT_SECONDS`, then 503).

### Separate photo worker?

Not needed at this scale. The heavy work does not hold Python's global interpreter lock (Tesseract is
a separate process; Pillow and the barcode reader release the lock), so threads in the API process
scale with cores, as the table shows. A separate worker service (a queue plus worker processes)
becomes worthwhile when photo traffic needs more cores than the API machine has, or when photo
memory use (about 200 MB per concurrent 12 MP job) must be isolated from the API. The photo pipeline
is a single function call (`run_packaged_pipeline`), so moving it behind a queue would not change
the engine.

## Monitoring

`GET /api/admin/metrics` reports, besides assessment counts and latencies:

- `image_jobs`: jobs run, running now, refused as busy, the limit, and P50/P95 job duration and
  wait time over the last 500 jobs;
- `process`: CPU seconds (API process and OCR processes), average CPU share, load average, current
  and peak resident memory, thread count, uptime.
