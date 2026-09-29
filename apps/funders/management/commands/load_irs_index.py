import csv
import os
import tempfile
import zipfile

import requests
from django.core.management.base import BaseCommand

from apps.funders.irs_zip import RemoteFile, year_zip_batches, zip_url
from apps.funders.models import Filing, Funder

URL = "https://apps.irs.gov/pub/epostcard/990/xml/{y}/index_{y}.csv"


def find_batches(year, object_ids):
    """
    Older indexes (2023 and before) have no XML_BATCH_ID column. Read only the file
    list of each of that year's ZIPs (a few MB each, via HTTP ranges) to see which
    ZIP holds each filing.
    """
    wanted = set(object_ids)
    found = {}
    for batch in year_zip_batches(year):
        remote = RemoteFile(zip_url(batch))
        with zipfile.ZipFile(remote) as z:
            for name in z.namelist():
                oid = os.path.basename(name).split("_")[0]
                if oid in wanted:
                    found[oid] = batch
        remote.close()
        print(f"  {batch}: {sum(1 for b in found.values() if b == batch)} of our filings")
    return found


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("year", type=int)

    def handle(self, *args, **opts):
        y = opts["year"]
        path = os.path.join(tempfile.gettempdir(), f"index_{y}.csv")
        with requests.get(URL.format(y=y), stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)

        funders = {f.ein: f for f in Funder.objects.all()}
        seen = saved = 0
        filings = {}
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            print("Columns:", reader.fieldnames)
            has_batch = "XML_BATCH_ID" in (reader.fieldnames or [])
            for row in reader:
                if row["RETURN_TYPE"].strip().upper() != "990PF":
                    continue
                seen += 1
                funder = funders.get(row["EIN"].strip().zfill(9))
                if not funder:
                    continue
                # RETURN_ID is blank in newer indexes; OBJECT_ID is unique per return.
                return_id = row["RETURN_ID"].strip() or row.get("OBJECT_ID", "").strip()
                filings[return_id] = Filing(
                    return_id=return_id,
                    funder=funder,
                    object_id=(row.get("OBJECT_ID") or "").strip(),
                    tax_period=(row.get("TAX_PERIOD") or "").strip(),
                    # Some rows say "2024_TEOS_XML_04a"; the ZIP on the IRS server is "..._04A.zip".
                    batch_id=(row.get("XML_BATCH_ID") or "").strip().upper(),
                )
                saved += 1

        if not has_batch and filings:
            print(f"No XML_BATCH_ID column in the {y} index; reading the {y} ZIP file lists to find each filing")
            batches = find_batches(y, [f.object_id for f in filings.values() if f.object_id])
            for f in filings.values():
                f.batch_id = batches.get(f.object_id, "")
            print(f"  located {sum(1 for f in filings.values() if f.batch_id)} of {len(filings)} filings")

        # One insert-or-update query per 1000 rows. processed / grant_count / only_preselected
        # are left as they are, so filings already read are not read again.
        Filing.objects.bulk_create(
            filings.values(),
            batch_size=1000,
            update_conflicts=True,
            unique_fields=["return_id"],
            update_fields=["funder", "object_id", "tax_period", "batch_id"],
        )
        print(f"990-PF filings in index: {seen}. Matched to your Michigan list: {saved}")
