import requests
from bs4 import BeautifulSoup
import re
import getpass
import time
import urllib3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional, Dict, Any

# Disable SSL warnings since we're ignoring SSL verification
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ThreadedTIMBScraper:
    """
    Thread-safe version of TIMBScraper with concurrent request capabilities.
    """
    def __init__(self, username, password, max_workers=5, request_delay=5):
        self.base_url = "https://www.timb.co.zw/booking/"
        self.login_url = self.base_url + "index.php?module=login&item=card"
        self.analysis_form_url = self.base_url + "index.php?module=grower&item=analysis"
        self.username = username
        self.password = password
        self.max_workers = max_workers
        self.request_delay = request_delay
        
        # Create a session that will be shared across threads (requests.Session is thread-safe)
        self.session = requests.Session()
        self.session.verify = False
        
        # Thread-safe statistics
        self.stats_lock = threading.Lock()
        self.stats = {
            'requests_made': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'growers_discovered': 0
        }

    def login(self):
        """Logs into the system and returns True on success."""
        print("Attempting to log in...")
        payload = {
            'name': self.username,
            'password': self.password,
            'login': 'Login'
        }
        try:
            response = self.session.post(self.login_url, data=payload, verify=False)
            response.raise_for_status()

            if "Welcome" in response.text and "Not logged in" not in response.text:
                print("✅ Login successful.")
                return True
            else:
                print("❌ Login failed. Check your credentials.")
                return False
        except requests.exceptions.RequestException as e:
            print(f"❌ An error occurred during login: {e}")
            return False

    def _update_stats(self, stat_name, increment=1):
        """Thread-safe statistics update"""
        with self.stats_lock:
            self.stats[stat_name] += increment

    def print_stats(self):
        """Print current statistics"""
        with self.stats_lock:
            print(f"\n📊 Scraping Statistics:")
            print(f"  Total requests: {self.stats['requests_made']}")
            print(f"  Successful: {self.stats['successful_requests']}")
            print(f"  Failed: {self.stats['failed_requests']}")
            print(f"  Growers discovered: {self.stats['growers_discovered']}")
            if self.stats['requests_made'] > 0:
                success_rate = (self.stats['successful_requests'] / self.stats['requests_made']) * 100
                print(f"  Success rate: {success_rate:.1f}%")

    def _clean_text(self, text):
        """Helper function to clean up scraped text."""
        return text.replace('\n', ' ').replace('\r', '').strip()

    def _parse_value(self, text, value_type='number'):
        """Helper function to parse and clean numeric and currency strings."""
        if not text or not text.strip():
            return None
        cleaned = re.sub(r'[^\d.]', '', text)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    def _parse_int(self, text):
        """Helper function to safely parse integers."""
        if not text or not text.strip():
            return None
        cleaned = re.sub(r'[^\d]', '', text)
        if not cleaned:
            return None
        try:
            return int(cleaned)
        except (ValueError, TypeError):
            return None

    def parse_report(self, html_content):
        """Parses the HTML of a single grower analysis report."""
        if "Please specify the grower number" in html_content:
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        data = {
            'grower_info': {},
            'must_know_info': {},
            'sales_summary': {},
            'grade_analysis': [],
            'creditor_recoveries': []
        }

        # Parse grower and must know info
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
                key = label.lower().replace(' ', '_').replace('(', '').replace(')', '')
                data[current_section][key] = value

        # Parse accordion sections
        self._parse_sales_summary(soup, data)
        self._parse_grade_analysis(soup, data)
        self._parse_creditor_recoveries(soup, data)

        return data

    def _parse_sales_summary(self, soup, data):
        """Parse sales summary section"""
        sales_head = soup.find('div', class_='acc_head', string='Sales Summary')
        if sales_head:
            sales_content = sales_head.find_next_sibling('div', class_='acc_content')
            if sales_content:
                sales_table = sales_content.find('table')
                if sales_table:
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

    def _parse_grade_analysis(self, soup, data):
        """Parse grade analysis section"""
        grade_head = soup.find('div', class_='acc_head', string='Growers Grade Analysis')
        if grade_head:
            grade_content = grade_head.find_next_sibling('div', class_='acc_content')
            if grade_content:
                grade_table = grade_content.find('table')
                if grade_table:
                    for row in grade_table.find_all('tr')[1:]:
                        cells = row.find_all('td')
                        if len(cells) >= 4:
                            data['grade_analysis'].append({
                                'grade_name': self._clean_text(cells[0].get_text()),
                                'mass_kg': self._parse_value(cells[1].get_text()),
                                'value_usd': self._parse_value(cells[2].get_text()),
                                'average_price_usd': self._parse_value(cells[3].get_text())
                            })

    def _parse_creditor_recoveries(self, soup, data):
        """Parse creditor recoveries section"""
        creditor_head = soup.find('div', class_='acc_head', string='Creditor Recoveries')
        if creditor_head:
            creditor_content = creditor_head.find_next_sibling('div', class_='acc_content')
            if creditor_content:
                creditor_table = creditor_content.find('table')
                if creditor_table:
                    for row in creditor_table.find_all('tr')[1:]:
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

    def fetch_report(self, grower_id, season):
        """Fetches and returns the parsed data for a single grower and season."""
        prefix = grower_id[0]
        number = grower_id[1:]
        
        payload = {
            'deleteprefix': prefix,
            'deletegnumber': number,
            'deletesuffix': '',
            'season': str(season),
            'download': 'Show Analysis'
        }
        
        try:
            self._update_stats('requests_made')
            
            # Add rate limiting
            time.sleep(self.request_delay)
            
            response = self.session.post(self.analysis_form_url, data=payload, verify=False)
            response.raise_for_status()
            
            report_data = self.parse_report(response.text)
            
            if report_data:
                self._update_stats('successful_requests')
            else:
                self._update_stats('failed_requests')
                
            return report_data
            
        except requests.exceptions.RequestException as e:
            self._update_stats('failed_requests')
            print(f"⚠️  Error fetching {grower_id} season {season}: {e}")
            return None

    def discover_grower_first_season(self, grower_id: str) -> Optional[Tuple[str, int, Dict[str, Any]]]:
        """
        Discover a grower's first season by searching from 2018 onwards.
        Returns (grower_id, first_season, first_report_data) or None.
        """
        for year in range(2018, 2026):  # 2018 to 2025
            report_data = self.fetch_report(grower_id, year)
            
            if report_data and report_data.get('grower_info', {}).get('name', '').strip():
                print(f"✅ Found {grower_id} starting from {year}")
                self._update_stats('growers_discovered')
                return (grower_id, year, report_data)
        
        return None

    def discover_growers_threaded(self, grower_ids: List[str]) -> List[Tuple[str, int, Dict[str, Any]]]:
        """
        Discover multiple growers using threads.
        Returns list of (grower_id, first_season, first_report_data) tuples.
        """
        print(f"🔍 Discovering {len(grower_ids)} growers using {self.max_workers} threads...")
        start_time = time.time()
        
        discovered_growers = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all discovery tasks
            future_to_grower = {
                executor.submit(self.discover_grower_first_season, grower_id): grower_id 
                for grower_id in grower_ids
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_grower):
                grower_id = future_to_grower[future]
                try:
                    result = future.result()
                    if result:
                        discovered_growers.append(result)
                except Exception as e:
                    print(f"⚠️  Error discovering {grower_id}: {e}")
        
        elapsed_time = time.time() - start_time
        print(f"🎯 Discovery complete: {len(discovered_growers)}/{len(grower_ids)} growers found in {elapsed_time:.2f}s")
        
        return discovered_growers

    def fetch_all_seasons_for_grower(self, grower_id: str, start_season: int) -> List[Tuple[str, int, Dict[str, Any]]]:
        """
        Fetch all seasons for a grower from start_season to 2025 using threads.
        """
        seasons_to_fetch = list(range(start_season, 2026))
        print(f"📅 Fetching {len(seasons_to_fetch)} seasons for {grower_id} using threads...")
        
        results = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all season fetch tasks
            future_to_season = {
                executor.submit(self.fetch_report, grower_id, season): season 
                for season in seasons_to_fetch
            }
            
            # Collect results
            for future in as_completed(future_to_season):
                season = future_to_season[future]
                try:
                    report_data = future.result()
                    if report_data:
                        results.append((grower_id, season, report_data))
                except Exception as e:
                    print(f"⚠️  Error fetching {grower_id} season {season}: {e}")
        
        return results

    def fetch_multiple_reports_threaded(self, grower_season_pairs: List[Tuple[str, int]]) -> List[Tuple[str, int, Dict[str, Any]]]:
        """
        Fetch multiple specific (grower_id, season) pairs using threads.
        """
        print(f"📊 Fetching {len(grower_season_pairs)} reports using {self.max_workers} threads...")
        start_time = time.time()
        
        results = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all fetch tasks
            future_to_pair = {
                executor.submit(self.fetch_report, grower_id, season): (grower_id, season)
                for grower_id, season in grower_season_pairs
            }
            
            # Collect results
            for future in as_completed(future_to_pair):
                grower_id, season = future_to_pair[future]
                try:
                    report_data = future.result()
                    if report_data:
                        results.append((grower_id, season, report_data))
                except Exception as e:
                    print(f"⚠️  Error fetching {grower_id} season {season}: {e}")
        
        elapsed_time = time.time() - start_time
        print(f"✅ Fetched {len(results)}/{len(grower_season_pairs)} reports in {elapsed_time:.2f}s")
        
        return results


if __name__ == '__main__':
    # Example usage
    username = input("Enter your TIMB username: ")
    password = getpass.getpass("Enter your TIMB password: ")

    scraper = ThreadedTIMBScraper(username, password, max_workers=5, request_delay=5)
    
    if scraper.login():
        # Test with a small range
        grower_ids = [f"V{i}" for i in range(114260, 114270)]
        
        # Discover growers
        discovered = scraper.discover_growers_threaded(grower_ids)
        
        # Fetch all seasons for first discovered grower
        if discovered:
            grower_id, first_season, _ = discovered[0]
            all_seasons = scraper.fetch_all_seasons_for_grower(grower_id, first_season)
            print(f"Got {len(all_seasons)} seasons for {grower_id}")
        
        scraper.print_stats() 