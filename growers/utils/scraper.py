import requests
from bs4 import BeautifulSoup
import re # For cleaning up text
import getpass # For securely asking for password
import time
import urllib3

# Disable SSL warnings since we're ignoring SSL verification
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class TIMBScraper:
    """
    A scraper for the T.I.M.B Booking System to fetch grower analysis reports.
    """
    def __init__(self, username, password):
        self.base_url = "https://www.timb.co.zw/booking/" # Assuming this is the base path
        self.login_url = self.base_url + "index.php?module=login&item=card"
        self.analysis_form_url = self.base_url + "index.php?module=grower&item=analysis"
        self.username = username
        self.password = password
        self.session = requests.Session() # Use a session to maintain login cookies
        # Disable SSL verification for the entire session
        self.session.verify = False

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
            response.raise_for_status() # Raise an exception for bad status codes

            # Check for successful login by looking for a welcome message
            if "Welcome" in response.text and "Not logged in" not in response.text:
                print("Login successful.")
                return True
            else:
                print("Login failed. Check your credentials or the website's response.")
                return False
        except requests.exceptions.RequestException as e:
            print(f"An error occurred during login: {e}")
            return False
            
    def _clean_text(self, text):
        """Helper function to clean up scraped text."""
        return text.replace('\n', ' ').replace('\r', '').strip()

    def _parse_value(self, text, value_type='number'):
        """Helper function to parse and clean numeric and currency strings."""
        if not text or not text.strip():
            return None
        # Remove non-numeric characters except for the decimal point
        cleaned = re.sub(r'[^\d.]', '', text)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None # Return None if conversion fails

    def _parse_int(self, text):
        """Helper function to safely parse integers."""
        if not text or not text.strip():
            return None
        # Remove non-numeric characters
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
            print("Could not find a report for the specified grower/season.")
            return None

        soup = BeautifulSoup(html_content, 'html.parser')
        data = {
            'grower_info': {},
            'must_know_info': {},
            'sales_summary': {},
            'grade_analysis': [],
            'creditor_recoveries': []
        }

        # --- Parse Grower and Must Know Info ---
        # Find all table rows in the main content area
        info_rows = soup.select('td[valign="top"] table tr')
        current_section = 'grower_info'

        for row in info_rows:
            cells = row.find_all('td')
            if not cells or len(cells) < 2:
                # This could be a section header or a horizontal rule
                if 'Must know information' in row.text:
                    current_section = 'must_know_info'
                continue

            label = self._clean_text(cells[0].get_text())
            value = self._clean_text(cells[1].get_text(separator=' ', strip=True))

            if label:
                # Simple mapping from label to a clean key
                key = label.lower().replace(' ', '_').replace('(', '').replace(')', '')
                data[current_section][key] = value

        # --- Parse Accordion Sections ---
        # 1. Sales Summary
        sales_head = soup.find('div', class_='acc_head', string='Sales Summary')
        if sales_head:
            sales_content = sales_head.find_next_sibling('div', class_='acc_content')
            if sales_content:
                sales_table = sales_content.find('table')
                if sales_table:
                    rows = sales_table.find_all('tr')
                    if len(rows) > 1:  # Make sure we have data rows
                        cells = rows[1].find_all('td')  # Get first data row
                        if len(cells) >= 4:  # Make sure we have enough cells
                            data['sales_summary'] = {
                                'total_bales': self._parse_int(cells[0].get_text(strip=True)),
                                'total_mass_kg': self._parse_value(cells[1].get_text()),
                                'total_value_usd': self._parse_value(cells[2].get_text()),
                                'average_price_usd': self._parse_value(cells[3].get_text())
                            }

        # 2. Grade Analysis
        grade_head = soup.find('div', class_='acc_head', string='Growers Grade Analysis')
        if grade_head:
            grade_content = grade_head.find_next_sibling('div', class_='acc_content')
            if grade_content:
                grade_table = grade_content.find('table')
                if grade_table:
                    for row in grade_table.find_all('tr')[1:]: # Skip header
                        cells = row.find_all('td')
                        if len(cells) >= 4:  # Make sure we have enough cells
                            data['grade_analysis'].append({
                                'grade_name': self._clean_text(cells[0].get_text()),
                                'mass_kg': self._parse_value(cells[1].get_text()),
                                'value_usd': self._parse_value(cells[2].get_text()),
                                'average_price_usd': self._parse_value(cells[3].get_text())
                            })

        # 3. Creditor Recoveries
        creditor_head = soup.find('div', class_='acc_head', string='Creditor Recoveries')
        if creditor_head:
            creditor_content = creditor_head.find_next_sibling('div', class_='acc_content')
            if creditor_content:
                creditor_table = creditor_content.find('table')
                if creditor_table:
                    for row in creditor_table.find_all('tr')[1:]: # Skip header
                        cells = row.find_all('td')
                        if len(cells) >= 5:  # Make sure we have enough cells
                            recovery_text = cells[4].get_text(strip=True)
                            data['creditor_recoveries'].append({
                                'creditor_name': self._clean_text(cells[1].get_text()),
                                'total_owed_usd': self._parse_value(cells[2].get_text()),
                                'total_paid_usd': self._parse_value(cells[3].get_text()),
                                'recovery_percentage': self._parse_value(recovery_text),
                                'notes': recovery_text if not self._parse_value(recovery_text) else '' # Capture text like 'Percentage Stoporder...'
                            })
        return data

    def fetch_report(self, grower_id, season):
        """Fetches and returns the parsed data for a single grower and season."""
        print(f"Fetching report for Grower: {grower_id}, Season: {season}...")
        
      
        prefix = grower_id[0]
        number = grower_id[1:]
        
        payload = {
            'deleteprefix': prefix,
            'deletegnumber': number,
            'deletesuffix': '', # Assuming this is usually empty, adjust if needed
            'season': str(season),
            'download': 'Show Analysis'
        }
        
        try:
            response = self.session.post(self.analysis_form_url, data=payload, verify=False)
            response.raise_for_status()
            time.sleep(10) # Wait 30 seconds between requests
            return self.parse_report(response.text)
        except requests.exceptions.RequestException as e:
            print(f"An error occurred while fetching report for {grower_id}: {e}")
            return None

if __name__ == '__main__':
    # --- Example Usage ---
    username = input("Enter your TIMB username: ")
    password = getpass.getpass("Enter your TIMB password: ")

    scraper = TIMBScraper(username, password)
    
    if scraper.login():
        # Example: Scrape one grower for one season
        grower_to_check = 'V114260' 
        season_to_check = 2024
        
        report_data = scraper.fetch_report(grower_to_check, season_to_check)
        
        if report_data:
            import json
            print("\n--- SCRAPED DATA ---")
            # Print the data in a nicely formatted way
            print(json.dumps(report_data, indent=2))

