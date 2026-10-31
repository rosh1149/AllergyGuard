"""Photo-job cost: one 12-megapixel label photo through decode, quality checks, barcode and OCR,
run by 1, 2, 4 ... threads at once.  Usage (in backend/):  W=1,2,4,8 python scripts/photo_bench.py
"""
import io, json, os, resource, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.getcwd())
from app.evaluation.ocr_eval import FONT, _wrap
from app.packaged.barcode import read_barcodes
from app.packaged.images import load_image
from app.packaged.ocr import run_ocr

text = ("wheat flour, sugar, palm oil, cocoa powder, skimmed milk powder, emulsifier (soya lecithin), "
        "raising agents (sodium bicarbonate), salt, hazelnut paste, natural flavouring. may contain peanuts")
lines = ["INGREDIENTS: " + _wrap(text)[0], *_wrap(text)[1:]]
im = Image.new("RGB", (3024, 4032), (235, 235, 230))  # 12 MP phone photo
dr = ImageDraw.Draw(im); font = ImageFont.truetype(FONT, 64)
for j, l in enumerate(lines):
    dr.text((200, 1400 + j * 110), l, fill="black", font=font)
buf = io.BytesIO(); im.save(buf, "JPEG", quality=88); data = buf.getvalue()

def job(_=None):
    t = time.perf_counter(); c = time.process_time()
    li = load_image(data, 20 * 1024 * 1024)
    read_barcodes(li.image)
    r = run_ocr(li.image)
    return time.perf_counter() - t, r

def cpu_children():
    u = resource.getrusage(resource.RUSAGE_CHILDREN); s = resource.getrusage(resource.RUSAGE_SELF)
    return u.ru_utime + u.ru_stime, s.ru_utime + s.ru_stime

job()  # warm up
out = {"photo_bytes": len(data), "cpus": os.cpu_count()}
for workers in [int(x) for x in os.environ.get("W", "1,2,4").split(",")]:
    n = max(3, workers * 2)
    c0 = cpu_children(); t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as ex:
        lat = [d for d, _ in ex.map(job, range(n))]
    wall = time.perf_counter() - t0; c1 = cpu_children()
    out[f"threads_{workers}"] = {"jobs": n, "wall_s": round(wall, 2), "jobs_per_s": round(n / wall, 2),
        "latency_p50_s": round(statistics.median(lat), 2), "latency_max_s": round(max(lat), 2),
        "cpu_in_ocr_process_s": round(c1[0] - c0[0], 2), "cpu_in_api_process_s": round(c1[1] - c0[1], 2)}
out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
print(json.dumps(out, indent=1))
