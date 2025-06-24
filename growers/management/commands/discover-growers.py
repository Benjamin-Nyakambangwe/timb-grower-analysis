import time
import getpass
from django.core.management.base import BaseCommand
from django.db import transaction

from growers.utils.scraper import TIMBScraper
from growers.models import Grower, Contractor, Creditor, SeasonalReport, GradeAnalysis, CreditorRecovery

# Define the range of growers and seasons to check
GROWER_ID_START = 100000
GROWER_ID_END = 400000 # The loop will go up to and include this number
SEASONS = [2024, 2023, 2022, 2021, 2020, 2019, 2018, 2025] # Put most recent first
PROBE_SEASON = 2024 # The season to use for discovery
# GROWER_FIRST_SEASON = 2018

class Command(BaseCommand):
    help = 'Discovers and scrapes grower analysis data from the TIMB website within a given range.'

    def handle(self, *args, **options):
        username = input("Enter your TIMB username: ")
        password = getpass.getpass("Enter your TIMB password: ")

        scraper = TIMBScraper(username, password)
        if not scraper.login():
            self.stdout.write(self.style.ERROR('Login failed. Aborting.'))
            return

        self.stdout.write(self.style.SUCCESS('Login successful. Starting discovery...'))

        for i in range(GROWER_ID_START, GROWER_ID_END + 1):
            grower_id = f"V{i}"

            # --- RESUMABILITY CHECK ---
            # If we've already found and created this grower, we can check for missing seasons.
            # If not, we do a full probe.
            if Grower.objects.filter(grower_number=grower_id).exists():
                self.stdout.write(self.style.NOTICE(f"Grower {grower_id} already exists. Checking for missing seasons."))
                grower = Grower.objects.get(grower_number=grower_id)
                self.scrape_all_seasons_for_grower(scraper, grower)
                continue

            # --- DISCOVERY PROBE ---
            for season in SEASONS:
                self.stdout.write(f"Probing for new grower: {grower_id} in season {season}...")
                report_data = scraper.fetch_report(grower_id, season)
                if report_data['grower_info']['name'] != '':
                    print("--------------------------------")
                    print(report_data['grower_info']['name'])
                    print("--------------------------------")
                    self.stdout.write(self.style.SUCCESS(f"SUCCESS! Discovered new grower: {grower_id}"))
                    GROWER_FIRST_SEASON = season
                    break
                else:
                    self.stdout.write(f"No report for {grower_id} in season {season}. Assuming ID is invalid.")
                    
            if report_data:
                # Use a transaction to ensure all data for a grower is saved together
                with transaction.atomic():
                    grower = self.create_grower_from_report(report_data)
                    # self.create_seasonal_data(grower, PROBE_SEASON, report_data)
                
                # Now that we know the grower is real, get the rest of their seasons
                self.scrape_all_seasons_for_grower(scraper, grower)
            
            # self.stdout.write(f"Probing for new grower: {grower_id} in season {PROBE_SEASON}...")
            #
            # report_data = scraper.fetch_report(grower_id, PROBE_SEASON)

            # if report_data:
            #     # --- GROWER DISCOVERED! ---
            #     self.stdout.write(self.style.SUCCESS(f"SUCCESS! Discovered new grower: {grower_id}"))
                
            #     # Use a transaction to ensure all data for a grower is saved together
            #     with transaction.atomic():
            #         grower = self.create_grower_from_report(report_data)
            #         # self.create_seasonal_data(grower, PROBE_SEASON, report_data)
                
            #     # Now that we know the grower is real, get the rest of their seasons
            #     self.scrape_all_seasons_for_grower(scraper, grower)
            # else:
            #     # --- GROWER NOT FOUND ---
            #     self.stdout.write(f"No report for {grower_id} in probe season. Assuming ID is invalid.")
            #     # We do nothing and move to the next ID, saving time.

        self.stdout.write(self.style.SUCCESS('Discovery and scraping process complete.'))

    def scrape_all_seasons_for_grower(self, scraper, grower):
        """Scrapes all defined seasons for a known grower, skipping existing records."""
        for season in SEASONS:
            # Check if we already have this report
            if SeasonalReport.objects.filter(grower=grower, season_year=season).exists():
                self.stdout.write(f"  - Skipping season {season} for {grower.grower_number} (already in DB).")
                continue

            self.stdout.write(f"  - Fetching season {season} for existing grower {grower.grower_number}...")
            report_data = scraper.fetch_report(grower.grower_number, season)
            if report_data:
                with transaction.atomic():
                    self.create_seasonal_data(grower, season, report_data)
                self.stdout.write(self.style.SUCCESS(f"    -> Saved season {season} for {grower.grower_number}."))

    def create_grower_from_report(self, report_data):
        """Creates and saves a Grower object from scraped data."""
        grower_info = report_data['grower_info']
        grower, created = Grower.objects.get_or_create(
            grower_number=grower_info.get('grower_number'),
            defaults={
                'name': grower_info.get('name', ''),
                'surname': grower_info.get('surname', ''),
                'national_id': grower_info.get('national_id', ''),
                'farming_province': grower_info.get('farming_province', ''),
                'farm_name': grower_info.get('farm_name', ''),
                'address': grower_info.get('address', ''),
                'first_sales_year': int(report_data['must_know_info'].get('first_sales_record_found')) if report_data['must_know_info'].get('first_sales_record_found') else None
                # Add registered_since if you parse the date
            }
        )
        return grower

    def create_seasonal_data(self, grower, season, report_data):
        """Creates the SeasonalReport and its related objects."""
        
        # Handle contractor - get or create if contractor name exists
        contractor = None
        
        # Look for contractor with year prefix (e.g., "2024_contractor")
        contractor_key = f"{season}_contractor"
        contractor_name = report_data['must_know_info'].get(contractor_key)
        
        # If not found with year prefix, try just "contractor" as fallback
        if not contractor_name:
            contractor_name = report_data['must_know_info'].get('contractor')
        
        if contractor_name and contractor_name.strip():
            contractor, created = Contractor.objects.get_or_create(name=contractor_name.strip())
            if created:
                self.stdout.write(f"    -> Created new contractor: {contractor_name}")
        
        # Create SeasonalReport
        seasonal_report = SeasonalReport.objects.create(
            grower=grower,
            season_year=season,
            contractor=contractor,
            total_bales=report_data['sales_summary'].get('total_bales'),
            total_mass_kg=report_data['sales_summary'].get('total_mass_kg'),
            total_value_usd=report_data['sales_summary'].get('total_value_usd'),
            average_price_usd=report_data['sales_summary'].get('average_price_usd'),
        )
        
        # Create GradeAnalysis items
        for item in report_data.get('grade_analysis', []):
            GradeAnalysis.objects.create(seasonal_report=seasonal_report, **item)
            
        # Create CreditorRecovery items
        for item in report_data.get('creditor_recoveries', []):
            creditor_name = item.get('creditor_name')
            if creditor_name and creditor_name.strip():
                # Get or create creditor
                creditor, created = Creditor.objects.get_or_create(name=creditor_name.strip())
                if created:
                    self.stdout.write(f"    -> Created new creditor: {creditor_name}")
                
                # Remove creditor_name from item dict and add creditor object
                item_copy = item.copy()
                item_copy.pop('creditor_name', None)
                item_copy.pop('notes', None)  # Remove notes if your model doesn't have it
                item_copy['creditor'] = creditor
                
                CreditorRecovery.objects.create(seasonal_report=seasonal_report, **item_copy)