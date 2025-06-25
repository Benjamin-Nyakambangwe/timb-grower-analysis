import asyncio
import getpass
import time
from typing import List, Tuple, Optional
from django.core.management.base import BaseCommand
from django.db import transaction
from asgiref.sync import sync_to_async

from growers.utils.async_scraper import AsyncTIMBScraper, ScrapingConfig, GrowerDiscoveryResult
from growers.models import Grower, Contractor, Creditor, SeasonalReport, GradeAnalysis, CreditorRecovery

# Configuration constants
DEFAULT_START_ID = 100000
DEFAULT_END_ID = 120000
DEFAULT_BATCH_SIZE = 100  # Smaller batches since we're doing sequential year discovery
DEFAULT_CONCURRENT_REQUESTS = 6  # More conservative since each grower needs multiple sequential requests
DEFAULT_REQUEST_DELAY = 0.4


class Command(BaseCommand):
    help = 'Async grower discovery starting from 2018, finding each grower\'s first active season'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start', 
            type=int, 
            default=DEFAULT_START_ID,
            help=f'Starting grower ID number (default: {DEFAULT_START_ID})'
        )
        parser.add_argument(
            '--end', 
            type=int, 
            default=DEFAULT_END_ID,
            help=f'Ending grower ID number (default: {DEFAULT_END_ID})'
        )
        parser.add_argument(
            '--batch-size', 
            type=int, 
            default=DEFAULT_BATCH_SIZE,
            help=f'Number of growers to process in each batch (default: {DEFAULT_BATCH_SIZE})'
        )
        parser.add_argument(
            '--concurrent', 
            type=int, 
            default=DEFAULT_CONCURRENT_REQUESTS,
            help=f'Maximum concurrent requests (default: {DEFAULT_CONCURRENT_REQUESTS})'
        )
        parser.add_argument(
            '--delay', 
            type=float, 
            default=DEFAULT_REQUEST_DELAY,
            help=f'Delay between requests in seconds (default: {DEFAULT_REQUEST_DELAY})'
        )
        parser.add_argument(
            '--discover-only', 
            action='store_true',
            help='Only discover growers and their first seasons, don\'t fetch all seasons'
        )
        parser.add_argument(
            '--resume', 
            action='store_true',
            help='Resume from where we left off (skip existing growers)'
        )

    def handle(self, *args, **options):
        """Main command handler"""
        start_id = options['start']
        end_id = options['end']
        batch_size = options['batch_size']
        concurrent = options['concurrent']
        delay = options['delay']
        discover_only = options['discover_only']
        resume = options['resume']

        # Create scraping configuration
        config = ScrapingConfig(
            max_concurrent_requests=concurrent,
            request_delay=delay,
            timeout=30,
            max_retries=2,
            retry_delay=1.0
        )

        # Run the async process
        asyncio.run(self.async_main(
            start_id, end_id, batch_size, config, discover_only, resume
        ))

    async def async_main(self, start_id: int, end_id: int, batch_size: int, 
                        config: ScrapingConfig, discover_only: bool, resume: bool):
        """Main async execution function"""
        self.stdout.write(
            self.style.SUCCESS(
                f"🚀 Starting grower discovery (2018 onwards) for range V{start_id} to V{end_id}"
            )
        )
        self.stdout.write(f"⚙️  Config: {config.max_concurrent_requests} concurrent, {config.request_delay}s delay")
        
        # Get credentials
        username = input("Enter your TIMB username: ")
        password = getpass.getpass("Enter your TIMB password: ")

        # Initialize scraper
        async with AsyncTIMBScraper(username, password, config) as scraper:
            if not await scraper.login():
                self.stdout.write(self.style.ERROR('❌ Login failed. Aborting.'))
                return

            start_time = time.time()
            
            # Process in batches
            total_discovered = 0
            total_reports_saved = 0
            
            for batch_start in range(start_id, end_id + 1, batch_size):
                batch_end = min(batch_start + batch_size - 1, end_id)
                
                discovered, reports_saved = await self.process_batch(
                    scraper, batch_start, batch_end, discover_only, resume
                )
                
                total_discovered += discovered
                total_reports_saved += reports_saved
                
                # Progress update
                progress = ((batch_end - start_id + 1) / (end_id - start_id + 1)) * 100
                self.stdout.write(f"📊 Progress: {progress:.1f}% complete")

            # Final summary
            elapsed_time = time.time() - start_time
            self.stdout.write(
                self.style.SUCCESS(
                    f"🎉 Process complete in {elapsed_time:.2f} seconds!\n"
                    f"   📈 Discovered {total_discovered} valid growers\n"
                    f"   💾 Saved {total_reports_saved} seasonal reports"
                )
            )

    async def process_batch(self, scraper: AsyncTIMBScraper, start_id: int, end_id: int,
                           discover_only: bool, resume: bool) -> Tuple[int, int]:
        """Process a batch of grower IDs"""
        self.stdout.write(f"🔄 Processing batch: V{start_id} to V{end_id}")
        
        # Generate grower IDs for this batch
        grower_ids = [f"V{i}" for i in range(start_id, end_id + 1)]
        
        # Filter out existing growers if resuming
        if resume:
            existing_growers = await self.get_existing_grower_ids(grower_ids)
            grower_ids = [gid for gid in grower_ids if gid not in existing_growers]
            if existing_growers:
                self.stdout.write(f"   ⏭️  Skipping {len(existing_growers)} existing growers")

        if not grower_ids:
            self.stdout.write("   ✅ All growers in batch already exist")
            return 0, 0

        # Discover growers with their first seasons
        discovery_results = await scraper.discover_growers_batch(grower_ids)
        
        if not discovery_results:
            self.stdout.write("   📭 No valid growers found in this batch")
            return 0, 0

        # Save discovered growers to database
        await self.save_discovered_growers(discovery_results)

        reports_saved = 0
        
        if not discover_only:
            # Fetch all seasons for discovered growers
            reports_saved = await self.fetch_all_seasons_for_discovered_growers(
                scraper, discovery_results
            )

        return len(discovery_results), reports_saved

    async def save_discovered_growers(self, discovery_results: List[GrowerDiscoveryResult]):
        """Save discovered growers and their first season data to database"""
        for result in discovery_results:
            try:
                # Create grower from first season data
                grower = await self.get_or_create_grower(result.first_report_data)
                
                # Create the first season's data
                await self.create_seasonal_data(grower, result.first_season, result.first_report_data)
                
                self.stdout.write(f"   💾 Saved {result.grower_id} (first season: {result.first_season})")
                
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f"   ⚠️  Failed to save {result.grower_id}: {e}")
                )

    async def fetch_all_seasons_for_discovered_growers(self, scraper: AsyncTIMBScraper, 
                                                      discovery_results: List[GrowerDiscoveryResult]) -> int:
        """Fetch all seasons for discovered growers"""
        self.stdout.write(f"📅 Fetching all seasons for {len(discovery_results)} discovered growers...")
        
        total_reports_saved = 0
        
        for result in discovery_results:
            # Get existing seasons for this grower
            existing_seasons = await self.get_existing_seasons_for_grower(result.grower_id)
            
            # Determine which seasons we need to fetch (excluding first season which we already have)
            seasons_to_fetch = []
            for season in range(result.first_season + 1, 2026):  # From next season to current
                if season not in existing_seasons:
                    seasons_to_fetch.append(season)
            
            if not seasons_to_fetch:
                self.stdout.write(f"   ✅ All seasons already exist for {result.grower_id}")
                continue

            # Fetch remaining seasons for this grower
            self.stdout.write(f"   🎯 Fetching {len(seasons_to_fetch)} additional seasons for {result.grower_id}")
            
            grower_season_pairs = [(result.grower_id, season) for season in seasons_to_fetch]
            seasonal_results = await scraper.fetch_multiple_reports(grower_season_pairs)
            
            # Save the results
            grower = await self.get_grower_by_id(result.grower_id)
            if grower:
                for grower_id, season, report_data in seasonal_results:
                    try:
                        await self.create_seasonal_data(grower, season, report_data)
                        total_reports_saved += 1
                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(f"   ⚠️  Failed to save {grower_id} season {season}: {e}")
                        )

        return total_reports_saved

    @sync_to_async
    def get_existing_grower_ids(self, grower_ids: List[str]) -> List[str]:
        """Get list of grower IDs that already exist"""
        existing = Grower.objects.filter(
            grower_number__in=grower_ids
        ).values_list('grower_number', flat=True)
        return list(existing)

    @sync_to_async
    def get_existing_seasons_for_grower(self, grower_id: str) -> List[int]:
        """Get existing seasons for a grower"""
        try:
            seasons = SeasonalReport.objects.filter(
                grower__grower_number=grower_id
            ).values_list('season_year', flat=True)
            return list(seasons)
        except Exception:
            return []

    @sync_to_async
    def get_grower_by_id(self, grower_id: str) -> Optional[Grower]:
        """Get grower by ID"""
        try:
            return Grower.objects.get(grower_number=grower_id)
        except Grower.DoesNotExist:
            return None

    @sync_to_async
    def get_or_create_grower(self, report_data: dict) -> Grower:
        """Get or create grower from report data"""
        grower_info = report_data.get('grower_info', {})
        must_know_info = report_data.get('must_know_info', {})
        
        grower_number = grower_info.get('grower_number', '')
        
        grower, created = Grower.objects.get_or_create(
            grower_number=grower_number,
            defaults={
                'name': grower_info.get('name', ''),
                'surname': grower_info.get('surname', ''),
                'national_id': grower_info.get('national_id', ''),
                'farming_province': grower_info.get('farming_province', ''),
                'farm_name': grower_info.get('farm_name', ''),
                'address': grower_info.get('address', ''),
                'first_sales_year': self._safe_int_convert(
                    must_know_info.get('first_sales_record_found')
                )
            }
        )
        return grower

    @sync_to_async
    def create_seasonal_data(self, grower: Grower, season: int, report_data: dict):
        """Create seasonal report and related data"""
        with transaction.atomic():
            # Check if this season already exists
            if SeasonalReport.objects.filter(grower=grower, season_year=season).exists():
                return  # Skip if already exists
            
            # Handle contractor
            contractor = self._get_or_create_contractor(report_data, season)
            
            # Create seasonal report
            seasonal_report = SeasonalReport.objects.create(
                grower=grower,
                season_year=season,
                contractor=contractor,
                total_bales=report_data.get('sales_summary', {}).get('total_bales'),
                total_mass_kg=report_data.get('sales_summary', {}).get('total_mass_kg'),
                total_value_usd=report_data.get('sales_summary', {}).get('total_value_usd'),
                average_price_usd=report_data.get('sales_summary', {}).get('average_price_usd'),
            )
            
            # Create grade analysis records
            for item in report_data.get('grade_analysis', []):
                GradeAnalysis.objects.create(
                    seasonal_report=seasonal_report,
                    **item
                )
            
            # Create creditor recovery records
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

    def _get_or_create_contractor(self, report_data: dict, season: int) -> Optional[Contractor]:
        """Get or create contractor from report data"""
        must_know_info = report_data.get('must_know_info', {})
        
        # Try season-specific contractor first
        contractor_name = must_know_info.get(f'{season}_contractor')
        if not contractor_name:
            contractor_name = must_know_info.get('contractor')
        
        if contractor_name and contractor_name.strip():
            contractor, _ = Contractor.objects.get_or_create(
                name=contractor_name.strip()
            )
            return contractor
        return None

    def _safe_int_convert(self, value) -> Optional[int]:
        """Safely convert value to int"""
        if not value:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None