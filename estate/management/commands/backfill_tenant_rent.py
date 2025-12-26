from django.core.management.base import BaseCommand
from estate.models import Tenant, TenantRent
from datetime import date


class Command(BaseCommand):
    help = "Backfill TenantRent records from Tenant.monthly_rent"

    def handle(self, *args, **options):
        created = 0
        skipped = 0

        for tenant in Tenant.objects.all():
            # Normalize to first day of start month
            effective_from = tenant.start_date.replace(day=1)

            exists = TenantRent.objects.filter(
                tenant=tenant,
                effective_from=effective_from,
            ).exists()

            if exists:
                skipped += 1
                continue

            TenantRent.objects.create(
                tenant=tenant,
                rent_amount=tenant.monthly_rent,
                effective_from=effective_from,
            )
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"TenantRent backfill complete: {created} created, {skipped} skipped"
            )
        )