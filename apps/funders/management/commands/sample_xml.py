import os
import tempfile
import zipfile, requests
import zipfile_inflate64  # noqa: F401  IRS ZIPs use Deflate64, which zipfile can't read on its own
import xml.etree.ElementTree as ET
from django.core.management.base import BaseCommand
from apps.funders.models import Filing

KEYWORDS = ("grant", "contribution", "application", "unsolicited", "preselect", "recipient")
TMP = tempfile.gettempdir()  # /tmp does not exist on Windows

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--skip", type=int, default=0)

    def handle(self, *args, **opts):
        f = (Filing.objects.exclude(batch_id="").exclude(object_id="")
             .order_by("id")[opts["skip"]:opts["skip"] + 1].first())
        year = f.batch_id[:4]
        url = f"https://apps.irs.gov/pub/epostcard/990/xml/{year}/{f.batch_id}.zip"
        zpath = os.path.join(TMP, f"{f.batch_id}.zip")

        print("Foundation:", f.funder.name, "| Object ID:", f.object_id)
        print("Downloading", url, "(big file, wait a few minutes)")
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(zpath, "wb") as out:
                for chunk in r.iter_content(1024 * 1024):
                    out.write(chunk)

        with zipfile.ZipFile(zpath) as z:
            name = next((n for n in z.namelist() if f.object_id in n), None)
            if not name:
                print("File not found inside the ZIP")
                return
            data = z.read(name)

        sample_path = os.path.join(TMP, "sample.xml")
        with open(sample_path, "wb") as out:
            out.write(data)
        print("Saved", sample_path)

        seen = {}
        for el in ET.fromstring(data).iter():
            tag = el.tag.split("}")[-1]
            if any(k in tag.lower() for k in KEYWORDS) and tag not in seen:
                seen[tag] = (el.text or "").strip()[:60]
        for tag, val in seen.items():
            print(tag, "->", val)
