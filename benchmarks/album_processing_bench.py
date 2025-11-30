#!/usr/bin/env python3
"""
Minimal benchmark to measure performance of album art processing (sync vs process pool).

Usage:
    python benchmarks/album_processing_bench.py <path-to-sample-image.png>

This script will run N iterations and measure wall-clock time for the two approaches.
"""
import sys
import time
from io import BytesIO
from PIL import Image
from concurrent.futures import ProcessPoolExecutor

def process_fn(img_bytes):
    from PIL import Image as PILImage
    from io import BytesIO as _BytesIO
    img = PILImage.open(_BytesIO(img_bytes)).convert('RGB')
    img.thumbnail((150,150), PILImage.BILINEAR)
    bio = _BytesIO(); img.save(bio, format='PNG'); return bio.getvalue()

def sync_process(img, iterations=50):
    t0 = time.time()
    for _ in range(iterations):
        img2 = img.copy()
        img2.thumbnail((150,150), Image.BILINEAR)
        buf = BytesIO(); img2.save(buf, format='PNG')
    return time.time() - t0

def proc_pool_process(img_bytes, iterations=50, workers=2):
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        # submit N tasks
        futures = [ex.submit(process_fn, img_bytes) for _ in range(iterations)]
        for f in futures:
            f.result()
    return time.time() - t0

def main():
    if len(sys.argv) < 2:
        print("Usage: python benchmarks/album_processing_bench.py sample.png")
        return
    path = sys.argv[1]
    img = Image.open(path).convert('RGB')
    buf = BytesIO(); img.save(buf, format='PNG'); img_bytes = buf.getvalue()
    print('Loaded sample image, running benchmarks...')
    iterations = 30
    sync_time = sync_process(img, iterations=iterations)
    print(f'Sync: {iterations} iterations took {sync_time:.3f}s')
    pool_time = proc_pool_process(img_bytes, iterations=iterations, workers=max(1,(os.cpu_count() or 1)//2))
    print(f'ProcessPool: {iterations} iterations took {pool_time:.3f}s')

if __name__ == '__main__':
    import os
    main()
