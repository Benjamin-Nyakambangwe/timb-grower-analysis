import time
import getpass
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.management.base import BaseCommand
from django.db import transaction

from growers.utils.threaded_scraper import ThreadedTIMBScraper
from growers.models import Grower, Contractor, Creditor, SeasonalReport, GradeAnalysis, CreditorRecovery

# Configuration
GROWER_ID_START = 100000
GROWER_ID_END = 120000
BATCH_SIZE = 200
MAX_WORKERS = 6  # Number of threads
REQUEST_DELAY = 5  # Seconds between requests


class Command(BaseCommand):
    help = 'Threaded version: Discovers and scrapes grower data using multithreading'

    def add_arguments(self, parser):
        parser.add_argument('--start', type=int, default=GROWER_ID_START, help='Starting grower ID')
        parser.add_argument('--end', type=int, default=GROWER_ID_END, help='Ending grower ID')
        parser.add_argument('--batch-size', type=int, default=BATCH_SIZE, help='Batch size')
        parser.add_argument('--workers', type=int, default=MAX_WORKERS, help='Number of worker threads')
        parser.add_argument('--delay', type=float, default=REQUEST_DELAY, help='Delay between requests')
        parser.add_argument('--discover-only', action='store_true', help='Only discover, don\'t fetch all seasons')
        parser.add_argument('--resume', action='store_true', help='Skip existing growers')

    def handle(self, *args, **options):
        start_id = options['start']
        end_id = options['end']
        batch_size = options['batch_size']
        workers = options['workers']
        delay = options['delay']
        discover_only = options['discover_only']
        resume = options['resume']

        self.stdout.write(
            self.style.SUCCESS(
                f"🚀 Starting threaded discovery for V{start_id} to V{end_id}"
            )
        )
        self.stdout.write(f"⚙️  Using {workers} threads with {delay}s delay")

        # username = input("Enter your TIMB username: ")
        # password = getpass.getpass("Enter your TIMB password: ")
        username = 'DSAMAKANDE'
        password = 'D@654321s'
        

        scraper = ThreadedTIMBScraper(username, password, max_workers=workers, request_delay=delay)
        
        if not scraper.login():
            self.stdout.write(self.style.ERROR('❌ Login failed. Aborting.'))
            return

        start_time = time.time()
        total_discovered = 0
        total_reports_saved = 0

        # Process in batches
        for batch_start in range(start_id, end_id + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, end_id)
            
            discovered, reports_saved = self.process_batch(
                scraper, batch_start, batch_end, discover_only, resume
            )
            
            total_discovered += discovered
            total_reports_saved += reports_saved
            
            # Progress update
            progress = ((batch_end - start_id + 1) / (end_id - start_id + 1)) * 100
            self.stdout.write(f"📊 Progress: {progress:.1f}% complete")

        # Final summary
        elapsed_time = time.time() - start_time
        scraper.print_stats()
        
        self.stdout.write(
            self.style.SUCCESS(
                f"🎉 Process complete in {elapsed_time:.2f} seconds!\n"
                f"   📈 Discovered {total_discovered} valid growers\n"
                f"   💾 Saved {total_reports_saved} seasonal reports"
            )
        )

    def process_batch(self, scraper, start_id, end_id, discover_only, resume):
        """Process a batch of grower IDs"""
        self.stdout.write(f"🔄 Processing batch: V{start_id} to V{end_id}")
        
        # Generate grower IDs
        grower_ids = [f"V{i}" for i in range(start_id, end_id + 1)]
        
        # Filter existing growers if resuming
        if resume:
            existing_growers = self.get_existing_grower_ids(grower_ids)
            grower_ids = [gid for gid in grower_ids if gid not in existing_growers]
            if existing_growers:
                self.stdout.write(f"   ⏭️  Skipping {len(existing_growers)} existing growers")

        if not grower_ids:
            return 0, 0

        # Discover growers with threading
        discovered_growers = scraper.discover_growers_threaded(grower_ids)
        
        if not discovered_growers:
            self.stdout.write("   📭 No valid growers found")
            return 0, 0

        # Save discovered growers and their first season
        self.save_discovered_growers(discovered_growers)

        reports_saved = 0
        
        if not discover_only:
            # Fetch remaining seasons for each grower
            reports_saved = self.fetch_remaining_seasons(scraper, discovered_growers)

        return len(discovered_growers), reports_saved

    def save_discovered_growers(self, discovered_growers):
        """Save discovered growers to database"""
        for grower_id, first_season, report_data in discovered_growers:
            try:
                with transaction.atomic():
                    grower = self.create_grower_from_report(report_data)
                    self.create_seasonal_data(grower, first_season, report_data)
                
                self.stdout.write(f"   💾 Saved {grower_id} (first season: {first_season})")
                
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f"   ⚠️  Failed to save {grower_id}: {e}")
                )

    def fetch_remaining_seasons(self, scraper, discovered_growers):
        """Fetch remaining seasons for discovered growers"""
        total_saved = 0
        
        for grower_id, first_season, _ in discovered_growers:
            # Get existing seasons
            existing_seasons = self.get_existing_seasons_for_grower(grower_id)
            
            # Determine missing seasons (excluding first season which we already have)
            missing_seasons = []
            for season in range(first_season + 1, 2026):
                if season not in existing_seasons:
                    missing_seasons.append(season)
            
            if not missing_seasons:
                continue

            self.stdout.write(f"   📅 Fetching {len(missing_seasons)} additional seasons for {grower_id}")
            
            # Fetch missing seasons using threads
            grower_season_pairs = [(grower_id, season) for season in missing_seasons]
            results = scraper.fetch_multiple_reports_threaded(grower_season_pairs)
            
            # Save results
            grower = self.get_grower_by_id(grower_id)
            if grower:
                for _, season, report_data in results:
                    try:
                        with transaction.atomic():
                            self.create_seasonal_data(grower, season, report_data)
                        total_saved += 1
                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(f"   ⚠️  Failed to save {grower_id} season {season}: {e}")
                        )

        return total_saved

    def get_existing_grower_ids(self, grower_ids):
        """Get existing grower IDs from database"""
        existing = Grower.objects.filter(
            grower_number__in=grower_ids
        ).values_list('grower_number', flat=True)
        return list(existing)

    def get_existing_seasons_for_grower(self, grower_id):
        """Get existing seasons for a grower"""
        try:
            seasons = SeasonalReport.objects.filter(
                grower__grower_number=grower_id
            ).values_list('season_year', flat=True)
            return list(seasons)
        except Exception:
            return []

    def get_grower_by_id(self, grower_id):
        """Get grower by ID"""
        try:
            return Grower.objects.get(grower_number=grower_id)
        except Grower.DoesNotExist:
            return None

    def create_grower_from_report(self, report_data):
        """Create grower from report data"""
        grower_info = report_data['grower_info']
        must_know_info = report_data['must_know_info']
        
        grower, created = Grower.objects.get_or_create(
            grower_number=grower_info.get('grower_number'),
            defaults={
                'name': grower_info.get('name', ''),
                'surname': grower_info.get('surname', ''),
                'national_id': grower_info.get('national_id', ''),
                'farming_province': grower_info.get('farming_province', ''),
                'farm_name': grower_info.get('farm_name', ''),
                'address': grower_info.get('address', ''),
                'first_sales_year': int(must_know_info.get('first_sales_record_found')) if must_know_info.get('first_sales_record_found') else None
            }
        )
        return grower

    def create_seasonal_data(self, grower, season, report_data):
        """Create seasonal data"""
        # Skip if already exists
        if SeasonalReport.objects.filter(grower=grower, season_year=season).exists():
            return

        # Handle contractor
        contractor = None
        must_know_info = report_data['must_know_info']
        contractor_name = must_know_info.get(f'{season}_contractor') or must_know_info.get('contractor')
        
        if contractor_name and contractor_name.strip():
            contractor, _ = Contractor.objects.get_or_create(name=contractor_name.strip())
        
        # Create seasonal report
        seasonal_report = SeasonalReport.objects.create(
            grower=grower,
            season_year=season,
            contractor=contractor,
            total_bales=report_data['sales_summary'].get('total_bales'),
            total_mass_kg=report_data['sales_summary'].get('total_mass_kg'),
            total_value_usd=report_data['sales_summary'].get('total_value_usd'),
            average_price_usd=report_data['sales_summary'].get('average_price_usd'),
        )
        
        # Create grade analysis
        for item in report_data.get('grade_analysis', []):
            GradeAnalysis.objects.create(seasonal_report=seasonal_report, **item)
            
        # Create creditor recoveries
        for item in report_data.get('creditor_recoveries', []):
            creditor_name = item.get('creditor_name', '').strip()
            if creditor_name:
                creditor, _ = Creditor.objects.get_or_create(name=creditor_name)
                
                CreditorRecovery.objects.create(
                    seasonal_report=seasonal_report,
                    creditor=creditor,
                    total_owed_usd=item.get('total_owed_usd'),
                    total_paid_usd=item.get('total_paid_usd'),
                    recovery_percentage=item.get('recovery_percentage'),
                ) 
