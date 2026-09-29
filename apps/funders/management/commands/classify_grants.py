"""
AI priority-area classification for PastGrant, with a cost check before any API call.

    python manage.py classify_grants          # Steps 1, 3, 5: pick model, free rule, work list + cost estimate. No API calls.
    python manage.py classify_grants --run    # Step 6 (API, 75 pairs per call) then Steps 7-9.
    python manage.py classify_grants --apply  # Steps 7-9 only (no API): copy cache to PastGrant + reports.

Each distinct normalized (recipient_name, purpose) pair is classified once and
cached in GrantClassificationCache; re-running only sends pairs not yet cached.
"""

import csv
import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.funders.models import GrantClassificationCache, PastGrant
from apps.funders.ntee_priority import ntee_tags

LABELS = [
    "Arts", "Community Development", "Culture", "Downtown Development", "Economic Development",
    "Education", "Food Access/Food Rescue", "Health", "Housing", "Human Services", "Literacy",
    "Public Safety", "Recreation", "Workforce Development", "Youth Development", "Other", "Unclear",
]
BATCH = 75
TOKENS_IN_PER_ITEM, TOKENS_OUT_PER_ITEM = 31, 15  # measured on the 150-pair trial with this prompt

# OpenAI list prices, USD per 1M tokens (input, output). Only models whose price is
# known here can be chosen; others available on the key are listed but skipped.
# The Flex service tier bills half these rates.
PRICES = {
    "gpt-5.5": (5.00, 30.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
}
REASONING_MODELS = ("gpt-5", "o3", "o4")

SYSTEM = (
    "You label past foundation grants by their main focus, for a grant-matching tool. Each item has a "
    "recipient organization name and the purpose the funder wrote.\n\n"
    "Allowed labels:\n"
    "- One of these 15 focus areas: " + ", ".join(LABELS[:15]) + ".\n"
    '- "Other": the focus IS identifiable but is none of the 15. Use it for churches, mosques, synagogues, '
    "ministries and other religious work; animal welfare and animal rescue; environment and conservation; "
    "political, advocacy or international causes; donor-advised funds and pass-through charities.\n"
    '- "Unclear": neither the purpose nor the recipient name tells you the focus (for example '
    '"general support" or "charitable" given to an organization whose name does not say what it does).\n\n'
    "Rules: judge from the recipient name first when the purpose is vague or generic. A school, college or "
    "university is Education. A hospital or clinic is Health. A museum, theater or orchestra is Arts or Culture. "
    "A food bank or pantry is Food Access/Food Rescue. Do NOT force a grant into one of the 15 when the honest "
    "answer is Other or Unclear; a wrong focus area is worse than Other or Unclear. Return one answer per id. "
    'For each answer also copy the first word of that item\'s recipient into "first_word".'
)

SCHEMA = {
    "name": "grant_labels",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "integer"},
                        "first_word": {"type": "string"},  # echo, to catch an answer landing on the wrong item
                        "label": {"type": "string", "enum": LABELS},
                    },
                    "required": ["id", "first_word", "label"],
                },
            }
        },
        "required": ["results"],
    },
}


def norm(text):
    return " ".join((text or "").split()).casefold()


def echo_key(word):
    """Letters and digits only, so "(individual)" and "individual" compare equal."""
    return re.sub(r"[^a-z0-9]", "", norm(word))


def cache_key(recipient_norm, purpose_norm):
    return hashlib.sha256(f"{recipient_norm}\x1f{purpose_norm}".encode()).hexdigest()


def free_rule_q():
    """Step 3: no purpose, and no named recipient -> nothing to classify."""
    blank_purpose = Q(purpose__regex=r"^\s*$")
    no_recipient = Q(recipient_name__regex=r"^\s*$") | Q(recipient_name__iexact="(individual)")
    return blank_purpose & no_recipient


class Command(BaseCommand):
    help = "Classify PastGrant priority areas with AI (cached per distinct recipient+purpose)."

    def add_arguments(self, parser):
        parser.add_argument("--run", action="store_true", help="Call the API for uncached pairs (Step 6), then apply + report.")
        parser.add_argument("--apply", action="store_true", help="Only apply the cache to PastGrant and report (Steps 7-9).")
        parser.add_argument("--limit", type=int, default=0, help="Classify at most this many pairs in this run.")
        parser.add_argument("--workers", type=int, default=6, help="Parallel API calls.")
        parser.add_argument("--model", default="", help="Override the automatic cheapest-model choice.")
        parser.add_argument("--reasoning", default="minimal", help="reasoning_effort for reasoning models.")
        parser.add_argument("--flex", action="store_true", help="Use the Flex service tier (half price, slower).")
        parser.add_argument("--report-dir", default=tempfile.gettempdir())

    # ── Step 1 ───────────────────────────────────────────────────────────────
    def client(self):
        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv(".env")
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise CommandError("OPENAI_API_KEY is not set in .env; stopping.")
        self.stdout.write("OPENAI_API_KEY: set")
        return OpenAI(max_retries=0, timeout=180)

    def choose_model(self, client, override):
        available = {m.id for m in client.models.list().data}
        small = sorted(i for i in available if ("nano" in i or "mini" in i) and "-20" not in i
                       and not any(k in i for k in ("audio", "tts", "transcribe", "realtime", "search", "image", "codex", "research")))
        self.stdout.write("Small models on this key: " + ", ".join(small))
        if override:
            if override not in available or override not in PRICES:
                raise CommandError(f"--model {override} is not available or has no known price.")
            self.stdout.write(self.style.SUCCESS(
                f"Model (chosen with --model): {override}  ${PRICES[override][0]:.2f} in / "
                f"${PRICES[override][1]:.2f} out per 1M tokens"))
            return override
        priced = [m for m in small if m in PRICES]
        skipped = [m for m in small if m not in PRICES]
        if skipped:
            self.stdout.write("  (skipped, price not known here: " + ", ".join(skipped) + ")")
        per_item = lambda m: TOKENS_IN_PER_ITEM * PRICES[m][0] + TOKENS_OUT_PER_ITEM * PRICES[m][1]  # noqa: E731
        for m in sorted(priced, key=per_item):
            self.stdout.write(f"  {m:14} ${PRICES[m][0]:.2f} in / ${PRICES[m][1]:.2f} out per 1M tokens")
        chosen = min(priced, key=per_item)
        self.stdout.write(self.style.SUCCESS(f"Chosen model (cheapest per item): {chosen}"))
        return chosen

    # ── Step 3 ───────────────────────────────────────────────────────────────
    def free_rule(self):
        qs = PastGrant.objects.filter(free_rule_q())
        updated = qs.exclude(priority_area="Unclear").update(priority_area="Unclear")
        self.stdout.write(f"Step 3 free rule (blank purpose + individual/blank recipient -> Unclear): "
                          f"{qs.count()} grants ({updated} newly set)")

    # ── Step 5 ───────────────────────────────────────────────────────────────
    def work_list(self):
        pairs = {}
        for recipient, purpose in (PastGrant.objects.exclude(free_rule_q())
                                   .values_list("recipient_name", "purpose").iterator(chunk_size=5000)):
            r, p = norm(recipient), norm(purpose)
            pairs.setdefault(cache_key(r, p), (r, p))
        cached = set(GrantClassificationCache.objects.values_list("cache_key", flat=True))
        todo = [(k, r, p) for k, (r, p) in pairs.items() if k not in cached]
        return pairs, todo

    def estimate(self, model, n, flex):
        price_in, price_out = PRICES[model]
        tin, tout = n * TOKENS_IN_PER_ITEM, n * TOKENS_OUT_PER_ITEM
        cost = (tin * price_in + tout * price_out) / 1e6 * (0.5 if flex else 1)
        calls = -(-n // BATCH)
        self.stdout.write(f"Estimated tokens: {tin:,} in / {tout:,} out in {calls:,} calls of {BATCH}")
        self.stdout.write(self.style.SUCCESS(
            f"Estimated cost with {model}{' (Flex, half price)' if flex else ''}: ${cost:.2f}"))

    # ── Step 6 ───────────────────────────────────────────────────────────────
    def classify_batch(self, client, model, batch, reasoning, flex):
        """One call for up to 75 pairs -> ({index: label}, usage, service tier, echo mismatches)."""
        items = [{"id": i, "recipient": r, "purpose": p or "(none given)"} for i, (_, r, p) in enumerate(batch)]
        extra = {"reasoning_effort": reasoning} if model.startswith(REASONING_MODELS) else {}
        if flex:
            extra["service_tier"] = "flex"
        last = None
        for attempt in range(1, 4):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    response_format={"type": "json_schema", "json_schema": SCHEMA},
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": json.dumps(items)}],
                    **extra,
                )
                answers, mismatches = {}, 0
                for r in json.loads(resp.choices[0].message.content)["results"]:
                    i = r["id"]
                    if not 0 <= i < len(batch):
                        continue
                    # Keep an answer only if it echoes the right recipient, so a label
                    # can never land on the wrong grant.
                    if echo_key(r["first_word"]) != echo_key((batch[i][1].split() or [""])[0]):
                        mismatches += 1
                        continue
                    answers[i] = r["label"]
                return answers, resp.usage, getattr(resp, "service_tier", None), mismatches
            except Exception as exc:  # noqa: BLE001 - transient API/network/JSON errors: retry
                last = exc
                time.sleep(10 * attempt)
        raise RuntimeError(f"batch failed 3 times: {last}")

    def run_api(self, client, model, todo, workers, reasoning, flex):
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        tokens_in = tokens_out = cached_in = saved = failed = mismatched = 0
        cost = 0.0
        tiers = Counter()
        price_in, price_out = PRICES[model]
        started = time.time()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.classify_batch, client, model, b, reasoning, flex): b for b in batches}
            for n, fut in enumerate(as_completed(futures), 1):
                batch = futures[fut]
                try:
                    answers, usage, tier, mismatches = fut.result()
                except RuntimeError as exc:
                    failed += 1
                    self.stderr.write(f"  gave up on a batch of {len(batch)}: {exc}")
                    continue
                tokens_in += usage.prompt_tokens
                tokens_out += usage.completion_tokens
                details = getattr(usage, "prompt_tokens_details", None)
                cached_in += getattr(details, "cached_tokens", 0) or 0
                tiers[tier or "unknown"] += 1
                # Billed at the tier the API says it used (Flex = half price).
                cost += (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / 1e6 \
                    * (0.5 if tier == "flex" else 1)
                mismatched += mismatches
                rows = [GrantClassificationCache(cache_key=k, recipient_norm=r[:255], purpose_norm=p,
                                                 label=answers[i], model_used=model)
                        for i, (k, r, p) in enumerate(batch) if answers.get(i) in LABELS]
                GrantClassificationCache.objects.bulk_create(rows, ignore_conflicts=True)
                saved += len(rows)
                if n % 20 == 0 or n == len(batches):
                    self.stdout.write(f"  {n}/{len(batches)} calls, {saved} pairs cached, "
                                      f"${cost:.2f} so far ({time.time() - started:.0f}s)")
        self.stdout.write(self.style.SUCCESS(
            f"Step 6 done: {saved} pairs cached, {failed} batches failed, "
            f"{mismatched} answers rejected by the echo check (re-run to retry them).\n"
            f"API usage: {tokens_in:,} in ({cached_in:,} of them cached) / {tokens_out:,} out tokens; "
            f"service tiers {dict(tiers)}; cost ${cost:.2f} (list price, cached-input discount not subtracted)"))
        return cost

    # ── Step 7 ───────────────────────────────────────────────────────────────
    def apply_cache(self):
        cache = {k: (label, at) for k, label, at in
                 GrantClassificationCache.objects.values_list("cache_key", "label", "created_at")}
        changed = []
        for g in (PastGrant.objects.exclude(free_rule_q())
                  .only("id", "recipient_name", "purpose", "priority_area", "classified_at").iterator(chunk_size=5000)):
            hit = cache.get(cache_key(norm(g.recipient_name), norm(g.purpose)))
            if hit and (g.priority_area, g.classified_at) != hit:
                g.priority_area, g.classified_at = hit
                changed.append(g)
        for i in range(0, len(changed), 2000):
            PastGrant.objects.bulk_update(changed[i:i + 2000], ["priority_area", "classified_at"])
        self.stdout.write(f"Step 7: applied the cache to {len(changed)} grants")

    # ── Steps 8-9 ────────────────────────────────────────────────────────────
    def cross_check(self, report_dir):
        tags = ntee_tags()
        grants = PastGrant.objects.filter(id__in=tags).exclude(priority_area="").only(
            "id", "recipient_name", "purpose", "priority_area")
        rows = [(g, tags[g.id]) for g in grants]
        agree = [x for x in rows if x[0].priority_area == x[1]]
        disagree = [x for x in rows if x[0].priority_area != x[1]]
        self.stdout.write(f"Step 8: {len(tags)} grants have an NTEE-based tag; {len(rows)} of them have an AI label")
        if rows:
            self.stdout.write(f"  agreement: {len(agree)}/{len(rows)} = {len(agree) / len(rows):.1%}")
        path = os.path.join(report_dir, "ntee_vs_ai_disagreements.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["pastgrant_id", "recipient", "purpose", "ntee_tag", "ai_label"])
            for g, tag in disagree:
                w.writerow([g.id, g.recipient_name, g.purpose, tag, g.priority_area])
        self.stdout.write(f"  {len(disagree)} disagreements listed in {path}")
        for g, tag in disagree:
            self.stdout.write(f"   - {g.recipient_name[:40]:40} | {g.purpose[:50]:50} | NTEE {tag} | AI {g.priority_area}")

    def final_report(self):
        counts = Counter(PastGrant.objects.values_list("priority_area", flat=True))
        unclassified = counts.pop("", 0)
        self.stdout.write("Step 9: grants per label:")
        for label, n in counts.most_common():
            self.stdout.write(f"  {label:26} {n}")
        self.stdout.write(f"  still unclassified: {unclassified}")

    def handle(self, *args, **opts):
        if opts["apply"]:
            self.apply_cache()
            self.cross_check(opts["report_dir"])
            self.final_report()
            return

        client = self.client()
        model = self.choose_model(client, opts["model"])
        self.free_rule()
        pairs, todo = self.work_list()
        self.stdout.write(f"Step 5: {len(pairs):,} distinct (recipient, purpose) pairs; "
                          f"{len(todo):,} not yet in the cache")
        if opts["limit"]:
            todo = todo[:opts["limit"]]
            self.stdout.write(f"  --limit: this run sends {len(todo):,}")
        self.estimate(model, len(todo), opts["flex"])

        if not opts["run"]:
            self.stdout.write(self.style.WARNING("STOP: no API calls made. Re-run with --run to classify."))
            return

        self.run_api(client, model, todo, opts["workers"], opts["reasoning"], opts["flex"])
        self.apply_cache()
        self.cross_check(opts["report_dir"])
        self.final_report()
