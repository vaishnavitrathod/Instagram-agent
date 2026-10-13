"""Instagram publishing via the Graph API (single image or carousel).
Called only by the worker, on posts that were approved and scheduled."""
import os
import time

import requests

import db
import images


def _check(r):
    if not r.ok:
        raise RuntimeError(f"Instagram API {r.status_code}: {r.text}")
    return r.json()


def _create(base, ig_id, token, data):
    return _check(requests.post(f"{base}/{ig_id}/media", data={**data, "access_token": token}, timeout=60))["id"]


def _wait(base, cid, token):
    for _ in range(40):
        status = _check(requests.get(f"{base}/{cid}", params={"fields": "status_code", "access_token": token},
                                     timeout=30))["status_code"]
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Container {status}")
        time.sleep(2)
    raise RuntimeError("Container not ready in time")


def publish_instagram(caption: str, image_path: str):
    paths = db.unpack(image_path)
    if os.getenv("DRY_RUN", "1") == "1":
        kind = "carousel of %d images" % len(paths) if len(paths) > 1 else "image"
        print(f"  [DRY RUN] would upload {kind} {paths} and post to Instagram:\n  {caption!r}")
        return

    ig_id, token = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    base = f"https://graph.facebook.com/{os.getenv('GRAPH_VERSION', 'v26.0')}"
    urls = [images.upload_public(p) for p in paths]

    if len(urls) == 1:
        cid = _create(base, ig_id, token, {"image_url": urls[0], "caption": caption})
    else:
        kids = []
        for u in urls:  # one container per slide, then one parent container
            kid = _create(base, ig_id, token, {"image_url": u, "is_carousel_item": "true"})
            _wait(base, kid, token)
            kids.append(kid)
        cid = _create(base, ig_id, token, {"media_type": "CAROUSEL", "children": ",".join(kids), "caption": caption})
    _wait(base, cid, token)
    _check(requests.post(f"{base}/{ig_id}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60))


PUBLISHERS = {"instagram": publish_instagram}
