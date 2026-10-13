"""Tiny HTTP helper: retries when a service is busy (429/5xx) or the network blips."""
import time
import urllib.error
import urllib.request

RETRY_CODES = {429, 500, 502, 503, 504}
UA = {"User-Agent": "ig-agent/1.0"}


def http(req, timeout=60, tries=3, label=""):
    for attempt in range(tries):
        last = attempt == tries - 1
        try:
            return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code in RETRY_CODES and not last:
                wait = 2 ** (attempt + 1)
                print(f"  {label}: busy (HTTP {e.code}), retrying in {wait}s")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if not last:
                wait = 2 ** (attempt + 1)
                print(f"  {label}: network problem ({e}), retrying in {wait}s")
                time.sleep(wait)
                continue
            raise
