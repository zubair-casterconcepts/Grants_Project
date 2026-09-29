"""
Read files out of a remote IRS ZIP without downloading the whole ZIP.

The IRS server supports HTTP range requests, so `RemoteFile` acts like a local,
seekable file: zipfile reads the ZIP's file list from the end of the ZIP (a few
MB), and `prefetch()` then fetches only the byte ranges of the XML files we
need, in parallel. A ~500 MB batch ZIP costs roughly its file list plus
~50 KB per filing instead of the full download.
"""

import bisect
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

MIN_FETCH = 64 * 1024  # smallest range fetched when zipfile reads something not prefetched
LOCAL_HEADER_SLACK = 30 + 1024  # local header + name/extra fields, which can differ from the central copy
ZIP_URL = "https://apps.irs.gov/pub/epostcard/990/xml/{year}/{batch}.zip"
DOWNLOADS_PAGE = "https://www.irs.gov/charities-non-profits/form-990-series-downloads"


def zip_url(batch):
    return ZIP_URL.format(year=batch[:4], batch=batch)


def year_zip_batches(year):
    """ZIP names for a year, from the IRS "Form 990 series downloads" page (e.g. 2023_TEOS_XML_01A)."""
    page = requests.get(DOWNLOADS_PAGE, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    page.raise_for_status()
    return sorted(set(re.findall(rf"epostcard/990/xml/{year}/([A-Za-z0-9_\-]+)\.zip", page.text)))


class RangeNotSupported(Exception):
    pass


class RemoteFile:
    """Read-only, seekable file over HTTP range requests, with a byte cache."""

    def __init__(self, url, session=None, timeout=120):
        self.url = url
        self.session = session or requests.Session()
        self.timeout = timeout
        self.pos = 0
        self.fetched_bytes = 0
        self._starts = []  # sorted start offsets of cached segments
        self._segments = {}  # start -> bytes
        head = self.session.head(url, timeout=timeout, allow_redirects=True)
        head.raise_for_status()
        if head.headers.get("Accept-Ranges", "").lower() != "bytes":
            raise RangeNotSupported(url)
        self.size = int(head.headers["Content-Length"])

    # -- file API used by zipfile --------------------------------------------
    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        if not n:
            return b""
        data = self._cached(self.pos, n)
        if data is None:
            start = self.pos
            end = min(self.size, start + max(n, MIN_FETCH))
            self._store(start, self._fetch(start, end))
            data = self._cached(self.pos, n)
        self.pos += len(data)
        return data

    def close(self):
        self._segments.clear()
        self._starts.clear()

    # -- ranges ---------------------------------------------------------------
    def prefetch(self, ranges, workers=8):
        """Fetch many (start, end) byte ranges in parallel into the cache."""
        todo = [(s, min(e, self.size)) for s, e in ranges if self._cached(s, min(e, self.size) - s) is None]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for (start, _), data in zip(todo, pool.map(lambda r: self._fetch(*r), todo)):
                self._store(start, data)

    def _fetch(self, start, end):
        """Bytes [start, end). Retries network hiccups."""
        for attempt in range(1, 5):
            try:
                r = self.session.get(
                    self.url, headers={"Range": f"bytes={start}-{end - 1}"}, timeout=self.timeout
                )
                if r.status_code != 206:
                    raise RangeNotSupported(f"{self.url}: HTTP {r.status_code} for a range request")
                self.fetched_bytes += len(r.content)
                return r.content
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(2 * attempt)

    def _store(self, start, data):
        if start not in self._segments:
            bisect.insort(self._starts, start)
        if len(data) >= len(self._segments.get(start, b"")):
            self._segments[start] = data

    def _cached(self, pos, n):
        i = bisect.bisect_right(self._starts, pos) - 1
        while i >= 0:
            start = self._starts[i]
            seg = self._segments[start]
            if start + len(seg) >= pos + n:
                return seg[pos - start:pos - start + n]
            i -= 1
            if pos - start > 16 * 1024 * 1024:  # no older segment can reach this far
                break
        return None


def member_ranges(zf, names):
    """Byte ranges that cover each member's local header and compressed data."""
    ranges = []
    for name in names:
        info = zf.getinfo(name)
        start = info.header_offset
        ranges.append((start, start + LOCAL_HEADER_SLACK + len(info.filename.encode()) + info.compress_size))
    return ranges
