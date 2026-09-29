"""
Report what the free NTEE-based tagging would do. READ-ONLY: nothing is saved.

Each PastGrant's recipient_name is matched to a Funder (the saved ProPublica
Michigan organizations) by exact, trimmed, case-insensitive name. The matched
org's NTEE major letter is looked up in NTEE_MAJOR_TO_PRIORITY.
"""

import random
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from apps.funders.models import Funder, PastGrant
from apps.funders.ntee_priority import (
    NTEE_MAJOR_TO_PRIORITY, SKIP_RECIPIENTS, name_key, ntee_letter, priority_for,
)


class Command(BaseCommand):
    help = "Dry-run report of free NTEE-based priority tagging (saves nothing)."

    def add_arguments(self, parser):
        parser.add_argument("--sample", type=int, default=15)
        parser.add_argument("--seed", type=int, default=20260929)

    def handle(self, *args, **opts):
        orgs = defaultdict(list)
        for name, ein, ntee in Funder.objects.values_list("name", "ein", "ntee_code"):
            orgs[name_key(name)].append((name, ein, ntee))

        total = skipped = no_match = ambiguous = 0
        matched = []  # (grant id, recipient, org name, ntee, priority)
        for gid, recipient in PastGrant.objects.values_list("id", "recipient_name").iterator(chunk_size=5000):
            total += 1
            key = name_key(recipient)
            if key in SKIP_RECIPIENTS:
                skipped += 1
                continue
            candidates = orgs.get(key)
            if not candidates:
                no_match += 1
                continue
            if len(candidates) > 1:
                # Same name, several Michigan orgs (different EINs): not a sure match.
                ambiguous += 1
                continue
            name, _ein, ntee = candidates[0]
            matched.append((gid, recipient, name, ntee, priority_for(ntee)))

        checked = total - skipped
        tagged = [m for m in matched if m[4]]
        letters = Counter(ntee_letter(m[3]) or "(no NTEE code)" for m in matched)
        unmapped = Counter(
            ntee_letter(m[3]) or "(no NTEE code)" for m in matched if not m[4]
        )
        pct = lambda n, d: f"{n / d:.1%}" if d else "n/a"  # noqa: E731

        w = self.stdout.write
        w("FREE NTEE-BASED PRIORITY TAGGING — DRY RUN (nothing saved)")
        w(f"Total PastGrant rows              : {total}")
        w(f"Skipped ('(individual)' or blank) : {skipped}")
        w(f"Grants checked                    : {checked}")
        w(f"Matched an org by exact name      : {len(matched)}  ({pct(len(matched), checked)} of checked)")
        w(f"Name matched 2+ orgs (not tagged) : {ambiguous}")
        w(f"No org with that exact name       : {no_match}")
        w("")
        w(f"Of the {len(matched)} matched:")
        w(f"  got a priority area             : {len(tagged)}  ({pct(len(tagged), len(matched))})")
        w(f"  unmapped letter / A / no code   : {len(matched) - len(tagged)}  ({pct(len(matched) - len(tagged), len(matched))})")
        w("  priority areas: " + ", ".join(f"{k} {v}" for k, v in Counter(m[4] for m in tagged).most_common()))
        w("")
        w("NTEE letters among matched recipients (all): "
          + ", ".join(f"{k} {v}" for k, v in sorted(letters.items())))
        w("Unmapped letters that appeared (incl. A = needs sub-code): "
          + ", ".join(f"{k} {v}" for k, v in unmapped.most_common()))
        w("  mapped letters for reference: " + ", ".join(
            f"{k}={v}" for k, v in NTEE_MAJOR_TO_PRIORITY.items()))
        w("")
        w(f"Random sample of {opts['sample']} matched grants:")
        rng = random.Random(opts["seed"])
        for gid, recipient, name, ntee, priority in rng.sample(matched, min(opts["sample"], len(matched))):
            w(f"  #{gid:<7} {recipient[:38]:38} -> {name[:38]:38} | {ntee or '-':6} | {priority or '(blank)'}")
