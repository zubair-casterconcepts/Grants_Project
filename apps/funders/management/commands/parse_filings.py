import os
import tempfile
import time
import zipfile
import zipfile_inflate64  # noqa: F401  IRS ZIPs use Deflate64, which zipfile can't read on its own
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal, InvalidOperation

import requests
from django.core.management.base import BaseCommand
from django.db import OperationalError, connection, transaction

from apps.funders.irs_zip import RangeNotSupported, RemoteFile, member_ranges, year_zip_batches, zip_url
from apps.funders.models import Filing, PastGrant, FunderContact


def tag(el):
    return el.tag.split("}")[-1]


def find(el, name):
    if el is None:
        return None
    return next((e for e in el.iter() if tag(e) == name), None)


def findall(el, name):
    return [e for e in el.iter() if tag(e) == name]


def text(el, name):
    e = find(el, name)
    return (e.text or "").strip() if e is not None else ""


def parse_filing(filing, root):
    ty = filing.tax_period[:4]
    year = int(ty) if ty.isdigit() else None
    checked = text(root, "OnlyContriToPreselectedInd").upper() in ("X", "TRUE", "1")

    grants = []
    for g in findall(root, "GrantOrContributionPdDurYrGrp"):
        biz = find(g, "RecipientBusinessName")
        if biz is not None:
            name = " ".join(x for x in (text(biz, "BusinessNameLine1Txt"),
                                        text(biz, "BusinessNameLine2Txt")) if x)
        else:
            name = "(individual)"  # we do not store names of individual people
        amt_txt = text(g, "Amt")
        try:
            amt = Decimal(amt_txt) if amt_txt else None
        except InvalidOperation:
            amt = None
        grants.append(PastGrant(
            funder=filing.funder, filing=filing, tax_year=year,
            recipient_name=name[:255],
            recipient_city=text(g, "CityNm")[:100],
            recipient_state=text(g, "StateAbbreviationCd")[:2],
            amount=amt,
            purpose=text(g, "GrantOrContributionPurposeTxt"),
        ))

    contacts = []
    for a in findall(root, "ApplicationSubmissionInfoGrp"):
        addr = find(a, "RecipientUSAddress")
        address = ", ".join(x for x in (text(addr, "AddressLine1Txt"), text(addr, "CityNm"),
                                        text(addr, "StateAbbreviationCd"), text(addr, "ZIPCd")) if x)
        notes = "\n".join(f"{label}: {val}" for label, val in (
            ("How to apply", text(a, "FormAndInfoAndMaterialsTxt")),
            ("Deadlines", text(a, "SubmissionDeadlinesTxt")),
            ("Restrictions", text(a, "RestrictionsOnAwardsTxt")),
        ) if val)
        person = text(a, "RecipientPersonNm") or text(find(a, "RecipientBusinessName"), "BusinessNameLine1Txt")
        contacts.append(FunderContact(
            funder=filing.funder, filing=filing,
            contact_name=person[:255],
            phone=text(a, "RecipientPhoneNum")[:50],
            address=address,
            accepts_unsolicited=not checked,
            notes=notes,
        ))

    filing.only_preselected = checked
    filing.grant_count = len(grants)
    filing.processed = True
    return grants, contacts


SAVE_EVERY = 50  # filings written per transaction


def save_parsed(parsed, attempts=4):
    """Write a group of parsed filings in one transaction; retry if the DB connection drops."""
    for attempt in range(1, attempts + 1):
        try:
            return _save_parsed(parsed)
        except OperationalError:
            if attempt == attempts:
                raise
            connection.close()  # the transaction rolled back; reconnect and write the group again
            time.sleep(10 * attempt)


def _save_parsed(parsed):
    filings = [f for f, _, _ in parsed]
    with transaction.atomic():
        # If this client drops mid-transaction, the server rolls it back after 30s
        # instead of holding row locks that block the retry.
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
        PastGrant.objects.filter(filing__in=filings).delete()      # safe to re-run
        FunderContact.objects.filter(filing__in=filings).delete()
        PastGrant.objects.bulk_create([g for _, gs, _ in parsed for g in gs], batch_size=1000)
        FunderContact.objects.bulk_create([c for _, _, cs in parsed for c in cs], batch_size=1000)
        Filing.objects.bulk_update(filings, ["only_preselected", "grant_count", "processed"])


class Command(BaseCommand):
    help = "Read the IRS XML of each unprocessed filing; save grants and contacts."

    def add_arguments(self, parser):
        parser.add_argument("--batch", help="Only this ZIP, e.g. 2025_TEOS_XML_11B")
        parser.add_argument("--keep", action="store_true", help="Keep the ZIP after reading")

    def handle(self, *args, **opts):
        todo = (Filing.objects.filter(processed=False)
                .exclude(batch_id="").exclude(object_id="")
                .select_related("funder"))
        if opts["batch"]:
            todo = todo.filter(batch_id=opts["batch"])
        batches = sorted(set(todo.values_list("batch_id", flat=True)))
        self.stdout.write(f"{todo.count()} filings to read in {len(batches)} ZIP file(s)")
        missing = []
        for batch in batches:
            missing += self.run_batch(batch, list(todo.filter(batch_id=batch)), opts["keep"])
        if missing:
            self.find_in_other_zips(missing, opts["keep"])

    def find_in_other_zips(self, filings, keep):
        """
        The IRS index sometimes names the wrong ZIP of a month (it says 05A, the file
        is in 05B). Look in the other ZIPs of the same year and month, correct the
        filing's batch_id, and read it from there.
        """
        by_month = defaultdict(list)
        for f in filings:
            by_month[f.batch_id[:-1]].append(f)  # "2025_TEOS_XML_05A" -> "2025_TEOS_XML_05"
        year_batches = {}
        for month, left in by_month.items():
            year = month[:4]
            if year not in year_batches:
                year_batches[year] = year_zip_batches(year)
            tried = {f.batch_id for f in left}
            for other in (b for b in year_batches[year] if b.startswith(month) and b not in tried):
                remote = RemoteFile(zip_url(other))
                with zipfile.ZipFile(remote) as z:
                    names = self.names_by_object_id(z)
                remote.close()
                found = [f for f in left if f.object_id in names]
                if not found:
                    continue
                self.stdout.write(f"{len(found)} filings listed under {found[0].batch_id} are in {other}")
                Filing.objects.filter(pk__in=[f.pk for f in found]).update(batch_id=other)
                for f in found:
                    f.batch_id = other
                self.run_batch(other, found, keep)
                left = [f for f in left if f.object_id not in names]
                if not left:
                    break
            if left:
                self.stdout.write(self.style.WARNING(
                    f"{len(left)} filings of {month}* were not found in any {month}* ZIP"))

    def run_batch(self, batch, filings, keep):
        """Read `filings` from one ZIP; returns the filings that were not inside it."""
        zpath = os.path.join(tempfile.gettempdir(), f"{batch}.zip")
        url = zip_url(batch)
        started = time.time()

        if not os.path.exists(zpath):
            # Fast path: read only our filings out of the remote ZIP (HTTP ranges),
            # instead of downloading the whole ~500 MB file.
            try:
                remote = RemoteFile(url)
            except RangeNotSupported:
                remote = None
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 404:
                    raise
                # No such ZIP: treat its filings as missing, so the other ZIPs of
                # the same month are searched for them.
                self.stdout.write(self.style.WARNING(f"{batch}: ZIP not found on the IRS server ({url})"))
                return list(filings)
            if remote is not None:
                with zipfile.ZipFile(remote) as z:
                    names = self.names_by_object_id(z)
                    wanted = [names[f.object_id] for f in filings if f.object_id in names]
                    remote.prefetch(member_ranges(z, wanted))
                    self.stdout.write(f"{batch}: fetched {remote.fetched_bytes / 1e6:.1f} MB "
                                      f"of {remote.size / 1e6:.0f} MB for {len(wanted)} filings")
                    connection.close()  # the DB connection sat idle while fetching; Django reopens it
                    counts = self.parse_zip(z, names, filings)
                remote.close()
                self.report(batch, counts, started)
                return [f for f in filings if f.object_id not in names]

            self.stdout.write(f"Downloading {url} ...")
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(zpath + ".part", "wb") as out:
                    for chunk in r.iter_content(1024 * 1024):
                        out.write(chunk)
            os.replace(zpath + ".part", zpath)
            connection.close()  # the DB connection sat idle during the download; Django reopens it

        with zipfile.ZipFile(zpath) as z:
            names = self.names_by_object_id(z)
            counts = self.parse_zip(z, names, filings)
        if not keep:
            os.remove(zpath)
        self.report(batch, counts, started)
        return [f for f in filings if f.object_id not in names]

    @staticmethod
    def names_by_object_id(z):
        return {os.path.basename(n).split("_")[0]: n for n in z.namelist()}

    def parse_zip(self, z, by_obj, filings):
        done = missing = broken = n_grants = n_contacts = 0
        pending = []
        for f in filings:
            name = by_obj.get(f.object_id)
            if not name:
                missing += 1
                continue
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError:
                broken += 1
                continue
            grants, contacts = parse_filing(f, root)
            pending.append((f, grants, contacts))
            done += 1
            n_grants += len(grants)
            n_contacts += len(contacts)
            if len(pending) == SAVE_EVERY:
                save_parsed(pending)
                pending = []
                self.stdout.write(f"  ... {done}/{len(filings)} filings saved")
        if pending:
            save_parsed(pending)
        return done, missing, broken, n_grants, n_contacts

    def report(self, batch, counts, started):
        done, missing, broken, n_grants, n_contacts = counts
        self.stdout.write(self.style.SUCCESS(
            f"{batch}: read {done}, missing {missing}, broken {broken}, "
            f"grants saved {n_grants}, contacts saved {n_contacts} ({time.time() - started:.0f}s)"))
