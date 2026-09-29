from django.db import models


class Funder(models.Model):
    ein = models.CharField(max_length=9, unique=True)
    name = models.CharField(max_length=255)
    sub_name = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    ntee_code = models.CharField(max_length=10, blank=True)
    subsection_code = models.IntegerField(null=True, blank=True)  # 3 = 501(c)(3)
    raw_data = models.JSONField(default=dict)   # the full API response
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PastGrant(models.Model):
    funder = models.ForeignKey(Funder, on_delete=models.CASCADE)
    recipient_name = models.CharField(max_length=255)
    recipient_city = models.CharField(max_length=100, blank=True)
    recipient_state = models.CharField(max_length=2, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    purpose = models.TextField(blank=True)
    tax_year = models.IntegerField(null=True)
    filing = models.ForeignKey("Filing", null=True, blank=True,
                               on_delete=models.CASCADE, related_name="grants")
    priority_area = models.CharField(max_length=50, blank=True)
    classified_at = models.DateTimeField(null=True)


class FunderProfile(models.Model):
    """What a funder gives to, summarized from its classified PastGrants."""
    funder = models.OneToOneField(Funder, on_delete=models.CASCADE, related_name="profile")
    # {category: {count, total_amount, median_amount, org_count, org_amount,
    #             org_median_amount, individual_count, individual_amount}}
    category_breakdown = models.JSONField(default=dict)
    top_categories = models.JSONField(default=list)      # top 3 by total amount, excluding Unclear / Other
    total_grants = models.IntegerField(default=0)
    total_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    avg_grant_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    michigan_grants_count = models.IntegerField(default=0)
    michigan_grants_pct = models.FloatField(default=0)
    years_with_data = models.JSONField(default=list)     # tax years present
    last_updated = models.DateTimeField(auto_now=True)


class GrantClassificationCache(models.Model):
    """One AI label per distinct (recipient_name, purpose), reused by every grant with that pair."""
    cache_key = models.CharField(max_length=64, unique=True)  # sha256 of normalized recipient + purpose
    recipient_norm = models.CharField(max_length=255)
    purpose_norm = models.TextField(blank=True)
    label = models.CharField(max_length=50)
    model_used = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)


class Filing(models.Model):
    funder = models.ForeignKey(Funder, on_delete=models.CASCADE)
    return_id = models.CharField(max_length=50, unique=True)
    object_id = models.CharField(max_length=50, blank=True)
    tax_period = models.CharField(max_length=10, blank=True)
    batch_id = models.CharField(max_length=50, blank=True)  # which IRS ZIP holds it
    processed = models.BooleanField(default=False)
    only_preselected = models.BooleanField(null=True)
    grant_count = models.IntegerField(default=0)


class FunderContact(models.Model):
    funder = models.ForeignKey(Funder, on_delete=models.CASCADE)
    contact_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    accepts_unsolicited = models.BooleanField(null=True)
    notes = models.TextField(blank=True)
    filing = models.ForeignKey("Filing", null=True, blank=True,
                               on_delete=models.CASCADE, related_name="contacts")
