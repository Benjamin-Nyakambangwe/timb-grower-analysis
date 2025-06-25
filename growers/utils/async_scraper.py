import asyncio
import aiohttp
from bs4 import BeautifulSoup
import re
import time
from typing import List, Optional, Dict, Any, Tuple
import logging
from dataclasses import dataclass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class ScrapingConfig:
    """Configuration for scraping parameters"""
    max_concurrent_requests: int = 8
    request_delay: float = 0.3
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0

@dataclass
class GrowerDiscoveryResult:
    """Result of grower discovery"""
    grower_id: str
    first_season: int
    first_report_data: Dict[str, Any]

class AsyncTIMBScraper:
    """
    High-performance async scraper for TIMB with sequential year discovery.
    """
    
    def __init__(self, username: str, password: str, config: ScrapingConfig = None):
        self.username = username
        self.password = password
        self.config = config or ScrapingConfig()
        
        # URLs
        self.base_url = "https://www.timb.co.zw/booking/"
        self.login_url = self.base_url + "index.php?module=login&item=card"
        self.analysis_form_url = self.base_url + "index.php?module=grower&item=analysis"
        
        # Session and concurrency controls
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)
        self.is_logged_in = False
        
        # Discovery configuration
        self.start_year = 2018
        self.current_year = 2025  # Update as needed
        self.all_seasons = list(range(self.start_year, self.current_year + 1))
        
        # Stats tracking
        self.stats = {
            'requests_made': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'retries': 0,
            'growers_discovered': 0
        }

    async def __aenter__(self):
        """Async context manager entry"""
        connector = aiohttp.TCPConnector(
            ssl=False,
            limit=100,
            limit_per_host=50,
            keepalive_timeout=60,
            enable_cleanup_closed=True
        )
        
        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
        self.print_stats()

    def print_stats(self):
        """Print scraping statistics"""
        logger.info(f"Scraping Stats:")
        logger.info(f"  Total requests: {self.stats['requests_made']}")
        logger.info(f"  Successful: {self.stats['successful_requests']}")
        logger.info(f"  Failed: {self.stats['failed_requests']}")
        logger.info(f"  Retries: {self.stats['retries']}")
        logger.info(f"  Growers discovered: {self.stats['growers_discovered']}")
        if self.stats['requests_made'] > 0:
            success_rate = (self.stats['successful_requests'] / self.stats['requests_made']) * 100
            logger.info(f"  Success rate: {success_rate:.1f}%")

    async def login(self) -> bool:
        """Login to TIMB system"""
        logger.info("Attempting to log in to TIMB...")
        
        payload = {
            'name': self.username,
            'password': self.password,
            'login': 'Login'
        }
        
        try:
            async with self.session.post(self.login_url, data=payload) as response:
                response.raise_for_status()
                text = await response.text()
                
                if "Welcome" in text and "Not logged in" not in text:
                    logger.info("✅ Login successful!")
                    self.is_logged_in = True
                    return True
                else:
                    logger.error("❌ Login failed - check credentials")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Login error: {e}")
            return False

    def _clean_text(self, text: str) -> str:
        """Clean up scraped text"""
        if not text:
            return ""
        return text.replace('\n', ' ').replace('\r', '').strip()

    def _parse_value(self, text: str) -> Optional[float]:
        """Parse numeric values from text"""
        if not text or not text.strip():
            return None
        cleaned = re.sub(r'[^\d.]', '', text)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    def _parse_int(self, text: str) -> Optional[int]:
        """Parse integer values from text"""
        if not text or not text.strip():
            return None
        cleaned = re.sub(r'[^\d]', '', text)
        if not cleaned:
            return None
        try:
            return int(cleaned)
        except (ValueError, TypeError):
            return None

    def parse_report(self, html_content: str) -> Optional[Dict[str, Any]]:
        """Parse HTML report into structured data"""
        if not html_content or "Please specify the grower number" in html_content:
            return None

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            data = {
                'grower_info': {},
                'must_know_info': {},
                'sales_summary': {},
                'grade_analysis': [],
                'creditor_recoveries': []
            }

            # Parse main info sections
            self._parse_info_sections(soup, data)
            
            # Parse accordion sections
            self._parse_sales_summary(soup, data)
            self._parse_grade_analysis(soup, data)
            self._parse_creditor_recoveries(soup, data)

            return data
            
        except Exception as e:
            logger.error(f"Error parsing report HTML: {e}")
            return None

    def _parse_info_sections(self, soup: BeautifulSoup, data: Dict[str, Any]):
        """Parse grower info and must-know info sections"""
        info_rows = soup.select('td[valign="top"] table tr')
        current_section = 'grower_info'

        for row in info_rows:
            cells = row.find_all('td')
            if not cells or len(cells) < 2:
                if 'Must know information' in row.text:
                    current_section = 'must_know_info'
                continue

            label = self._clean_text(cells[0].get_text())
            value = self._clean_text(cells[1].get_text(separator=' ', strip=True))

            if label:
                key = label.lower().replace(' ', '_').replace('(', '').replace(')', '').replace(':', '')
                data[current_section][key] = value

    def _parse_sales_summary(self, soup: BeautifulSoup, data: Dict[str, Any]):
        """Parse sales summary section"""
        sales_head = soup.find('div', class_='acc_head', string='Sales Summary')
        if not sales_head:
            return

        sales_content = sales_head.find_next_sibling('div', class_='acc_content')
        if not sales_content:
            return

        sales_table = sales_content.find('table')
        if not sales_table:
            return

        rows = sales_table.find_all('tr')
        if len(rows) > 1:
            cells = rows[1].find_all('td')
            if len(cells) >= 4:
                data['sales_summary'] = {
                    'total_bales': self._parse_int(cells[0].get_text(strip=True)),
                    'total_mass_kg': self._parse_value(cells[1].get_text()),
                    'total_value_usd': self._parse_value(cells[2].get_text()),
                    'average_price_usd': self._parse_value(cells[3].get_text())
                }

    def _parse_grade_analysis(self, soup: BeautifulSoup, data: Dict[str, Any]):
        """Parse grade analysis section"""
        grade_head = soup.find('div', class_='acc_head', string='Growers Grade Analysis')
        if not grade_head:
            return

        grade_content = grade_head.find_next_sibling('div', class_='acc_content')
        if not grade_content:
            return

        grade_table = grade_content.find('table')
        if not grade_table:
            return

        for row in grade_table.find_all('tr')[1:]:  # Skip header
            cells = row.find_all('td')
            if len(cells) >= 4:
                data['grade_analysis'].append({
                    'grade_name': self._clean_text(cells[0].get_text()),
                    'mass_kg': self._parse_value(cells[1].get_text()),
                    'value_usd': self._parse_value(cells[2].get_text()),
                    'average_price_usd': self._parse_value(cells[3].get_text())
                })

    def _parse_creditor_recoveries(self, soup: BeautifulSoup, data: Dict[str, Any]):
        """Parse creditor recoveries section"""
        creditor_head = soup.find('div', class_='acc_head', string='Creditor Recoveries')
        if not creditor_head:
            return

        creditor_content = creditor_head.find_next_sibling('div', class_='acc_content')
        if not creditor_content:
            return

        creditor_table = creditor_content.find('table')
        if not creditor_table:
            return

        for row in creditor_table.find_all('tr')[1:]:  # Skip header
            cells = row.find_all('td')
            if len(cells) >= 5:
                recovery_text = cells[4].get_text(strip=True)
                data['creditor_recoveries'].append({
                    'creditor_name': self._clean_text(cells[1].get_text()),
                    'total_owed_usd': self._parse_value(cells[2].get_text()),
                    'total_paid_usd': self._parse_value(cells[3].get_text()),
                    'recovery_percentage': self._parse_value(recovery_text),
                    'notes': recovery_text if not self._parse_value(recovery_text) else ''
                })

    async def fetch_report_with_retry(self, grower_id: str, season: int) -> Optional[Dict[str, Any]]:
        """Fetch a single report with retry logic"""
        for attempt in range(self.config.max_retries + 1):
            try:
                result = await self._fetch_single_report(grower_id, season)
                if result is not None:
                    self.stats['successful_requests'] += 1
                    return result
                
                if attempt < self.config.max_retries:
                    self.stats['retries'] += 1
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                    
            except Exception as e:
                logger.debug(f"Attempt {attempt + 1} failed for {grower_id} season {season}: {e}")
                if attempt < self.config.max_retries:
                    self.stats['retries'] += 1
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    self.stats['failed_requests'] += 1
                    logger.error(f"All retry attempts failed for {grower_id} season {season}")
        
        self.stats['failed_requests'] += 1
        return None

    async def _fetch_single_report(self, grower_id: str, season: int) -> Optional[Dict[str, Any]]:
        """Fetch a single report (internal method)"""
        async with self.semaphore:  # Control concurrency
            try:
                # Rate limiting
                await asyncio.sleep(self.config.request_delay)
                
                prefix = grower_id[0] if grower_id else ''
                number = grower_id[1:] if len(grower_id) > 1 else ''
                
                payload = {
                    'deleteprefix': prefix,
                    'deletegnumber': number,
                    'deletesuffix': '',
                    'season': str(season),
                    'download': 'Show Analysis'
                }
                
                self.stats['requests_made'] += 1
                
                async with self.session.post(self.analysis_form_url, data=payload) as response:
                    response.raise_for_status()
                    html_content = await response.text()
                    return self.parse_report(html_content)
                    
            except asyncio.TimeoutError:
                logger.warning(f"Timeout for {grower_id} season {season}")
                raise
            except Exception as e:
                logger.debug(f"Request failed for {grower_id} season {season}: {e}")
                raise

    async def discover_grower_first_season(self, grower_id: str) -> Optional[GrowerDiscoveryResult]:
        """
        Discover a grower by searching from 2018 onwards until finding first valid season.
        Returns the first season where the grower has data and the report data.
        """
        for year in range(self.start_year, self.current_year + 1):
            report_data = await self.fetch_report_with_retry(grower_id, year)
            
            if report_data and report_data.get('grower_info', {}).get('name', '').strip():
                logger.info(f"✅ Found {grower_id} starting from {year}")
                self.stats['growers_discovered'] += 1
                return GrowerDiscoveryResult(
                    grower_id=grower_id,
                    first_season=year,
                    first_report_data=report_data
                )
        
        # No data found for this grower
        logger.debug(f"❌ No data found for {grower_id}")
        return None

    async def discover_growers_batch(self, grower_ids: List[str]) -> List[GrowerDiscoveryResult]:
        """
        Discover multiple growers concurrently.
        For each grower, finds their first active season.
        """
        logger.info(f"🔍 Discovering {len(grower_ids)} growers (searching from {self.start_year} onwards)...")
        
        # Create discovery tasks
        discovery_tasks = [
            self.discover_grower_first_season(grower_id)
            for grower_id in grower_ids
        ]
        
        # Execute discovery tasks
        results = await asyncio.gather(*discovery_tasks, return_exceptions=True)
        
        # Collect successful discoveries
        discovered_growers = []
        for result in results:
            if not isinstance(result, Exception) and result is not None:
                discovered_growers.append(result)
        
        logger.info(f"🎯 Discovery complete: {len(discovered_growers)}/{len(grower_ids)} growers found")
        return discovered_growers

    async def fetch_all_seasons_for_grower(self, grower_id: str, start_season: int) -> List[Tuple[str, int, Dict[str, Any]]]:
        """
        Fetch all seasons for a grower from their first season to current year.
        """
        seasons_to_fetch = list(range(start_season, self.current_year + 1))
        
        # Create fetch tasks for all seasons
        fetch_tasks = [
            self._fetch_with_metadata(grower_id, season)
            for season in seasons_to_fetch
        ]
        
        # Execute all tasks
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        
        # Filter successful results
        successful_results = []
        for result in results:
            if not isinstance(result, Exception) and result is not None:
                successful_results.append(result)
        
        return successful_results

    async def fetch_multiple_reports(self, grower_season_pairs: List[Tuple[str, int]]) -> List[Tuple[str, int, Dict[str, Any]]]:
        """Fetch multiple specific reports concurrently"""
        logger.info(f"📊 Fetching {len(grower_season_pairs)} reports concurrently...")
        start_time = time.time()
        
        # Create fetch tasks
        fetch_tasks = [
            self._fetch_with_metadata(grower_id, season)
            for grower_id, season in grower_season_pairs
        ]
        
        # Execute all tasks
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        
        # Filter successful results
        successful_results = []
        for result in results:
            if not isinstance(result, Exception) and result is not None:
                successful_results.append(result)
        
        elapsed_time = time.time() - start_time
        logger.info(f"✅ Fetched {len(successful_results)}/{len(grower_season_pairs)} reports in {elapsed_time:.2f} seconds")
        
        return successful_results

    async def _fetch_with_metadata(self, grower_id: str, season: int) -> Optional[Tuple[str, int, Dict[str, Any]]]:
        """Fetch report and return with metadata"""
        report_data = await self.fetch_report_with_retry(grower_id, season)
        if report_data:
            return (grower_id, season, report_data)
        return None