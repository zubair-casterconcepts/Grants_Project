import os, tempfile, zipfile, requests
import zipfile_inflate64  # noqa: F401  IRS ZIPs use Deflate64, which zipfile can't read on its own
import xml.etree.ElementTree as ET
from django.core.management.base import BaseCommand
from apps.funders.models import Filing


def tag(el):
    return el.tag.split("}")[-1]


def show(el, depth=0):
    kids = list(el)
    if kids:
        print("  " * depth + tag(el))
        for k in kids:
            show(k, depth + 1)
    else:
        print("  " * depth + f"{tag(el)} -> {(el.text or '').strip()[:60]}")


def first(root, name):
    return next((e for e in root.iter() if tag(e) == name), None)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--batch", default="2025_TEOS_XML_11B")

    def handle(self, *args, **opts):
        batch = opts["batch"]
        zpath = os.path.join(tempfile.gettempdir(), f"{batch}.zip")
        if not os.path.exists(zpath):
            url = f"https://apps.irs.gov/pub/epostcard/990/xml/{batch[:4]}/{batch}.zip"
            print("Downloading", url)
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(zpath, "wb") as out:
                    for chunk in r.iter_content(1024 * 1024):
                        out.write(chunk)

        filings = list(Filing.objects.filter(batch_id=batch).select_related("funder")[:400])
        print("Filings from your list in this ZIP:", len(filings))

        shown_grant = shown_contact = False
        preselected = has_app = checked = 0

        with zipfile.ZipFile(zpath) as z:
            by_obj = {os.path.basename(n).split("_")[0]: n for n in z.namelist()}
            print("Example file names:", list(z.namelist())[:2])
            for f in filings:
                name = by_obj.get(f.object_id)
                if not name:
                    continue
                checked += 1
                root = ET.fromstring(z.read(name))
                if first(root, "OnlyContriToPreselectedInd") is not None:
                    preselected += 1
                app = first(root, "ApplicationSubmissionInfoGrp")
                if app is not None:
                    has_app += 1
                grant = first(root, "GrantOrContributionPdDurYrGrp")
                if grant is not None and not shown_grant:
                    print("\n=== ONE GRANT:", f.funder.name, "===")
                    show(grant)
                    shown_grant = True
                if app is not None and not shown_contact:
                    print("\n=== APPLICATION CONTACT:", f.funder.name, "===")
                    show(app)
                    shown_contact = True

        print(f"\nChecked {checked} filings. Only give to pre-selected groups: {preselected}. Have application info: {has_app}.")
