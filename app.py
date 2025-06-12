
import os
import json
import time
import math
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import asyncio
from urllib.parse import urljoin, urlparse

from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import requests
from dataclasses import dataclass, asdict
from bs4 import BeautifulSoup
import selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

@dataclass
class Show:
    artist: str
    venue_name: str
    address: str
    city: str
    state: str
    country: str
    date: str
    latitude: float = 0.0
    longitude: float = 0.0
    venue_polygon: Optional[Dict] = None
    parking_polygons: List[Dict] = None
    
    def __post_init__(self):
        if self.parking_polygons is None:
            self.parking_polygons = []

@dataclass
class ParkingArea:
    name: str
    type: str
    latitude: float
    longitude: float
    place_id: str = ""

class BandsinTownScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.driver = None
        self._setup_driver()
    
    def _setup_driver(self):
        """Setup Selenium WebDriver for dynamic content"""
        try:
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            # Try to initialize driver (will work in environments with Chrome)
            try:
                self.driver = webdriver.Chrome(options=chrome_options)
                logger.info("Selenium WebDriver initialized successfully")
            except Exception as e:
                logger.warning(f"Selenium not available, falling back to requests: {e}")
                self.driver = None
        except Exception as e:
            logger.error(f"Failed to setup WebDriver: {e}")
            self.driver = None
    
    def scrape_artist_shows(self, artist_url: str, load_all_dates: bool = True) -> List[Dict]:
        """Scrape all shows from a Bandsintown artist page"""
        try:
            logger.info(f"Scraping artist shows from: {artist_url}")
            
            if self.driver:
                return self._scrape_with_selenium(artist_url, load_all_dates)
            else:
                return self._scrape_with_requests(artist_url)
                
        except Exception as e:
            logger.error(f"Error scraping {artist_url}: {e}")
            return []
    
    def _scrape_with_selenium(self, artist_url: str, load_all_dates: bool) -> List[Dict]:
        """Scrape using Selenium for dynamic content"""
        shows = []
        
        try:
            self.driver.get(artist_url)
            time.sleep(3)
            
            # Get artist name
            artist_name = self._extract_artist_name_selenium()
            
            if load_all_dates:
                # Click "Show more dates" until no more shows load
                self._load_all_dates_selenium()
            
            # Extract all show elements
            show_elements = self.driver.find_elements(By.CSS_SELECTOR, 
                '.event-item, .show-item, [data-testid*="event"], .event-card, .concert-item')
            
            logger.info(f"Found {len(show_elements)} potential show elements")
            
            for element in show_elements:
                show_data = self._extract_show_data_selenium(element, artist_name)
                if show_data:
                    shows.append(show_data)
            
            # Fallback: try different selectors
            if not shows:
                shows = self._extract_shows_fallback_selenium(artist_name)
            
        except Exception as e:
            logger.error(f"Selenium scraping error: {e}")
        
        return shows
    
    def _scrape_with_requests(self, artist_url: str) -> List[Dict]:
        """Fallback scraping with requests"""
        shows = []
        
        try:
            response = self.session.get(artist_url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            artist_name = self._extract_artist_name_bs4(soup)
            
            # Look for show data in various formats
            show_elements = soup.find_all(['div', 'li'], class_=re.compile(r'event|show|concert', re.I))
            
            for element in show_elements:
                show_data = self._extract_show_data_bs4(element, artist_name)
                if show_data:
                    shows.append(show_data)
            
            # Try to find JSON data in script tags
            script_shows = self._extract_json_shows(soup, artist_name)
            shows.extend(script_shows)
            
        except Exception as e:
            logger.error(f"Requests scraping error: {e}")
        
        return shows
    
    def _load_all_dates_selenium(self):
        """Click 'Show more dates' button repeatedly to load all shows"""
        max_clicks = 20  # Prevent infinite loops
        clicks = 0
        
        while clicks < max_clicks:
            try:
                # Look for various "show more" button selectors
                show_more_selectors = [
                    'button[data-testid*="show-more"]',
                    'button:contains("Show more")',
                    'button:contains("Load more")',
                    'button:contains("See more")',
                    '.show-more-btn',
                    '.load-more',
                    '[data-action="load-more"]'
                ]
                
                button_found = False
                for selector in show_more_selectors:
                    try:
                        button = WebDriverWait(self.driver, 2).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                        self.driver.execute_script("arguments[0].click();", button)
                        button_found = True
                        clicks += 1
                        logger.info(f"Clicked 'show more' button {clicks} times")
                        time.sleep(2)  # Wait for content to load
                        break
                    except (TimeoutException, NoSuchElementException):
                        continue
                
                if not button_found:
                    logger.info("No more 'show more' buttons found")
                    break
                    
            except Exception as e:
                logger.info(f"Finished loading dates after {clicks} clicks: {e}")
                break
    
    def _extract_artist_name_selenium(self) -> str:
        """Extract artist name using Selenium"""
        try:
            # Try various selectors for artist name
            selectors = [
                'h1[data-testid*="artist"]',
                'h1.artist-name',
                '.artist-header h1',
                'h1',
                '[data-testid="artist-name"]'
            ]
            
            for selector in selectors:
                try:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    name = element.text.strip()
                    if name and len(name) > 2:
                        return name
                except:
                    continue
            
            # Fallback: extract from URL or page title
            return self._extract_artist_from_url_or_title()
            
        except Exception as e:
            logger.warning(f"Could not extract artist name: {e}")
            return "Unknown Artist"
    
    def _extract_artist_name_bs4(self, soup) -> str:
        """Extract artist name using BeautifulSoup"""
        try:
            # Try various selectors
            selectors = ['h1', '.artist-name', '[data-testid*="artist"]']
            
            for selector in selectors:
                element = soup.select_one(selector)
                if element and element.get_text(strip=True):
                    name = element.get_text(strip=True)
                    if len(name) > 2:
                        return name
            
            return self._extract_artist_from_url_or_title()
            
        except Exception as e:
            logger.warning(f"Could not extract artist name: {e}")
            return "Unknown Artist"
    
    def _extract_artist_from_url_or_title(self) -> str:
        """Extract artist name from URL or page title"""
        try:
            if self.driver:
                url = self.driver.current_url
                title = self.driver.title
            else:
                url = ""
                title = ""
            
            # Extract from URL pattern: /a/12345-artist-name
            url_match = re.search(r'/a/\d+-(.+)', url)
            if url_match:
                artist_name = url_match.group(1).replace('-', ' ').title()
                return artist_name
            
            # Extract from title
            if title and 'Bandsintown' in title:
                artist_name = title.replace('Bandsintown', '').strip(' -|')
                return artist_name
                
            return "Unknown Artist"
            
        except Exception as e:
            return "Unknown Artist"
    
    def _extract_show_data_selenium(self, element, artist_name: str) -> Optional[Dict]:
        """Extract show data from a Selenium WebElement"""
        try:
            # Extract venue name
            venue_selectors = ['.venue-name', '.location-name', '[data-testid*="venue"]']
            venue_name = self._find_text_by_selectors(element, venue_selectors)
            
            # Extract date
            date_selectors = ['.event-date', '.date', '[data-testid*="date"]', 'time']
            date_text = self._find_text_by_selectors(element, date_selectors)
            
            # Extract location
            location_selectors = ['.location', '.venue-location', '.address', '[data-testid*="location"]']
            location_text = self._find_text_by_selectors(element, location_selectors)
            
            if venue_name and (date_text or location_text):
                return {
                    'artist': artist_name,
                    'venue_name': venue_name,
                    'raw_location': location_text or '',
                    'raw_date': date_text or '',
                    'source_url': self.driver.current_url if self.driver else ''
                }
            
            return None
            
        except Exception as e:
            logger.debug(f"Error extracting show data: {e}")
            return None
    
    def _extract_show_data_bs4(self, element, artist_name: str) -> Optional[Dict]:
        """Extract show data from a BeautifulSoup element"""
        try:
            # Similar extraction logic for BeautifulSoup
            venue_name = self._find_text_in_element(element, [
                '.venue-name', '.location-name', 'h3', 'h4'
            ])
            
            date_text = self._find_text_in_element(element, [
                '.event-date', '.date', 'time', '.datetime'
            ])
            
            location_text = self._find_text_in_element(element, [
                '.location', '.venue-location', '.address'
            ])
            
            if venue_name and (date_text or location_text):
                return {
                    'artist': artist_name,
                    'venue_name': venue_name,
                    'raw_location': location_text or '',
                    'raw_date': date_text or '',
                    'source_url': ''
                }
            
            return None
            
        except Exception as e:
            return None
    
    def _find_text_by_selectors(self, element, selectors: List[str]) -> str:
        """Find text using multiple selectors with Selenium"""
        for selector in selectors:
            try:
                sub_element = element.find_element(By.CSS_SELECTOR, selector)
                text = sub_element.text.strip()
                if text:
                    return text
            except:
                continue
        return ""
    
    def _find_text_in_element(self, element, selectors: List[str]) -> str:
        """Find text using multiple selectors with BeautifulSoup"""
        for selector in selectors:
            try:
                sub_element = element.select_one(selector)
                if sub_element:
                    text = sub_element.get_text(strip=True)
                    if text:
                        return text
            except:
                continue
        return ""
    
    def _extract_json_shows(self, soup, artist_name: str) -> List[Dict]:
        """Extract show data from JSON in script tags"""
        shows = []
        
        try:
            # Look for JSON data in script tags
            scripts = soup.find_all('script', type='application/json')
            scripts.extend(soup.find_all('script', string=re.compile(r'events|shows|concerts')))
            
            for script in scripts:
                try:
                    if script.string:
                        # Try to parse JSON
                        data = json.loads(script.string)
                        # Process JSON data to extract shows
                        json_shows = self._process_json_data(data, artist_name)
                        shows.extend(json_shows)
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Error extracting JSON shows: {e}")
        
        return shows
    
    def _process_json_data(self, data: Dict, artist_name: str) -> List[Dict]:
        """Process JSON data to extract show information"""
        shows = []
        
        try:
            # Recursively search for event-like objects
            if isinstance(data, dict):
                for key, value in data.items():
                    if key.lower() in ['events', 'shows', 'concerts', 'performances']:
                        if isinstance(value, list):
                            for item in value:
                                show = self._extract_show_from_json_item(item, artist_name)
                                if show:
                                    shows.append(show)
                    elif isinstance(value, (dict, list)):
                        shows.extend(self._process_json_data(value, artist_name))
            elif isinstance(data, list):
                for item in data:
                    shows.extend(self._process_json_data(item, artist_name))
                    
        except Exception as e:
            logger.debug(f"Error processing JSON data: {e}")
        
        return shows
    
    def _extract_show_from_json_item(self, item: Dict, artist_name: str) -> Optional[Dict]:
        """Extract show data from a JSON item"""
        try:
            if not isinstance(item, dict):
                return None
            
            # Look for venue and date information
            venue_name = item.get('venue', {}).get('name', '') if isinstance(item.get('venue'), dict) else item.get('venue', '')
            if not venue_name:
                venue_name = item.get('location', {}).get('name', '') if isinstance(item.get('location'), dict) else item.get('location', '')
            
            date_text = item.get('date', '') or item.get('datetime', '') or item.get('start_time', '')
            location_text = item.get('address', '') or item.get('city', '')
            
            if venue_name and (date_text or location_text):
                return {
                    'artist': artist_name,
                    'venue_name': venue_name,
                    'raw_location': location_text,
                    'raw_date': str(date_text),
                    'source_url': ''
                }
            
            return None
            
        except Exception as e:
            return None
    
    def _extract_shows_fallback_selenium(self, artist_name: str) -> List[Dict]:
        """Fallback method to extract shows using broad selectors"""
        shows = []
        
        try:
            # Get all text content and try to parse it
            page_text = self.driver.find_element(By.TAG_NAME, 'body').text
            
            # Look for patterns like venue names and dates
            lines = page_text.split('\n')
            potential_venues = []
            
            for line in lines:
                line = line.strip()
                if len(line) > 5 and any(keyword in line.lower() for keyword in ['theater', 'arena', 'club', 'hall', 'center', 'stadium']):
                    potential_venues.append(line)
            
            # Create shows from potential venues
            for venue in potential_venues[:20]:  # Limit to prevent too many false positives
                shows.append({
                    'artist': artist_name,
                    'venue_name': venue,
                    'raw_location': '',
                    'raw_date': '',
                    'source_url': self.driver.current_url
                })
                
        except Exception as e:
            logger.debug(f"Fallback extraction error: {e}")
        
        return shows
    
    def close(self):
        """Close the WebDriver"""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass

class GoogleMapsAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://maps.googleapis.com/maps/api"
        self.session = requests.Session()
    
    def geocode_venue(self, venue_name: str, location: str = "") -> Optional[Dict]:
        """Geocode a venue with location"""
        query = venue_name
        if location:
            query += f", {location}"
        
        url = f"{self.base_url}/geocode/json"
        params = {
            'address': query,
            'key': self.api_key
        }
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            data = response.json()
            
            if data['status'] == 'OK' and data['results']:
                result = data['results'][0]
                location = result['geometry']['location']
                
                return {
                    'latitude': location['lat'],
                    'longitude': location['lng'],
                    'formatted_address': result['formatted_address'],
                    'place_id': result['place_id']
                }
            else:
                logger.warning(f"Geocoding failed for {query}: {data.get('status')}")
                return None
        except Exception as e:
            logger.error(f"Error geocoding {query}: {e}")
            return None
    
    def find_parking_areas(self, latitude: float, longitude: float, radius: int = 800) -> List[ParkingArea]:
        """Find parking areas around a venue"""
        url = f"{self.base_url}/place/nearbysearch/json"
        params = {
            'location': f"{latitude},{longitude}",
            'radius': radius,
            'types': 'parking',
            'key': self.api_key
        }
        
        parking_areas = []
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            data = response.json()
            
            if data['status'] == 'OK':
                for place in data.get('results', []):
                    parking_type = self._determine_parking_type(place.get('name', ''))
                    
                    parking_area = ParkingArea(
                        name=place.get('name', 'Unknown Parking'),
                        type=parking_type,
                        latitude=place['geometry']['location']['lat'],
                        longitude=place['geometry']['location']['lng'],
                        place_id=place.get('place_id', '')
                    )
                    parking_areas.append(parking_area)
            
            return parking_areas
        except Exception as e:
            logger.error(f"Error finding parking areas: {e}")
            return []
    
    def _determine_parking_type(self, name: str) -> str:
        """Determine parking type from name"""
        name_lower = name.lower()
        if 'garage' in name_lower or 'structure' in name_lower:
            return 'garage'
        elif 'lot' in name_lower:
            return 'lot'
        else:
            return 'street'

class PolygonGenerator:
    def __init__(self):
        self.earth_radius = 6371000  # Earth's radius in meters
    
    def generate_venue_polygon(self, show: Show, buffer_meters: int = 150) -> Dict:
        """Generate a polygon around the venue building"""
        try:
            coords = self._create_circular_polygon(
                show.latitude, show.longitude, buffer_meters, points=20
            )
            return {
                "type": "Polygon",
                "coordinates": [coords]
            }
        except Exception as e:
            logger.error(f"Error generating venue polygon: {e}")
            return None
    
    def generate_parking_polygon(self, parking: ParkingArea) -> Dict:
        """Generate parking area polygon"""
        try:
            if parking.type == 'garage':
                buffer_meters = 50
                points = 8
            elif parking.type == 'lot':
                buffer_meters = 80
                points = 12
            else:
                buffer_meters = 20
                points = 8
            
            coords = self._create_circular_polygon(
                parking.latitude, parking.longitude, buffer_meters, points
            )
            
            return {
                "type": "Polygon",
                "coordinates": [coords]
            }
        except Exception as e:
            logger.error(f"Error generating parking polygon: {e}")
            return None
    
    def _create_circular_polygon(self, lat: float, lng: float, radius_meters: int, points: int = 16) -> List[List[float]]:
        """Create a circular polygon around a point"""
        coords = []
        
        for i in range(points + 1):
            angle = (i * 2 * math.pi) / points
            delta_lat = (radius_meters * math.cos(angle)) / self.earth_radius * (180 / math.pi)
            delta_lng = (radius_meters * math.sin(angle)) / (self.earth_radius * math.cos(math.radians(lat))) * (180 / math.pi)
            
            new_lat = lat + delta_lat
            new_lng = lng + delta_lng
            
            coords.append([new_lng, new_lat])  # GeoJSON format: [longitude, latitude]
        
        return coords

class VenueProcessor:
    def __init__(self, google_api_key: str):
        self.google_maps = GoogleMapsAPI(google_api_key)
        self.polygon_generator = PolygonGenerator()
        self.scraper = BandsinTownScraper()
    
    def process_artist_urls(self, artist_urls: List[str], load_all_dates: bool = True) -> Tuple[List[Show], Dict]:
        """Process multiple artist URLs and generate venue polygons"""
        all_shows = []
        processing_stats = {
            "artists_processed": 0,
            "total_shows_found": 0,
            "geocoded_shows": 0,
            "failed_geocoding": 0,
            "total_polygons": 0
        }
        
        try:
            for url in artist_urls:
                logger.info(f"Processing artist URL: {url}")
                processing_stats["artists_processed"] += 1
                
                # Scrape shows from the artist page
                raw_shows = self.scraper.scrape_artist_shows(url, load_all_dates)
                processing_stats["total_shows_found"] += len(raw_shows)
                
                # Process each show
                for raw_show in raw_shows:
                    processed_show = self._process_raw_show(raw_show)
                    if processed_show:
                        all_shows.append(processed_show)
                        processing_stats["geocoded_shows"] += 1
                        
                        # Count polygons
                        if processed_show.venue_polygon:
                            processing_stats["total_polygons"] += 1
                        processing_stats["total_polygons"] += len(processed_show.parking_polygons)
                    else:
                        processing_stats["failed_geocoding"] += 1
                
                # Rate limiting between artists
                time.sleep(2)
            
        finally:
            # Clean up scraper
            self.scraper.close()
        
        return all_shows, processing_stats
    
    def _process_raw_show(self, raw_show: Dict) -> Optional[Show]:
        """Process a raw show dict into a Show object with polygons"""
        try:
            # Parse location
            city, state, country = self._parse_location(raw_show.get('raw_location', ''))
            
            # Parse date
            parsed_date = self._parse_date(raw_show.get('raw_date', ''))
            
            # Geocode the venue
            search_query = f"{raw_show['venue_name']}"
            if city:
                search_query += f", {city}"
            if state:
                search_query += f", {state}"
            
            geocode_result = self.google_maps.geocode_venue(raw_show['venue_name'], f"{city}, {state}" if city and state else "")
            
            if not geocode_result:
                logger.warning(f"Failed to geocode: {search_query}")
                return None
            
            # Create Show object
            show = Show(
                artist=raw_show['artist'],
                venue_name=raw_show['venue_name'],
                address=geocode_result['formatted_address'],
                city=city,
                state=state,
                country=country,
                date=parsed_date,
                latitude=geocode_result['latitude'],
                longitude=geocode_result['longitude']
            )
            
            # Generate venue polygon
            show.venue_polygon = self.polygon_generator.generate_venue_polygon(show, 150)
            
            # Find and generate parking polygons
            parking_areas = self.google_maps.find_parking_areas(show.latitude, show.longitude, 800)
            
            for parking in parking_areas[:15]:  # Limit to 15 parking areas per venue
                parking_polygon = self.polygon_generator.generate_parking_polygon(parking)
                if parking_polygon:
                    show.parking_polygons.append({
                        'geometry': parking_polygon,
                        'name': parking.name,
                        'parking_type': parking.type,
                        'place_id': parking.place_id
                    })
            
            return show
            
        except Exception as e:
            logger.error(f"Error processing show: {e}")
            return None
    
    def _parse_location(self, location_text: str) -> Tuple[str, str, str]:
        """Parse location text into city, state, country"""
        try:
            if not location_text:
                return "", "", "USA"
            
            # Common patterns: "City, ST" or "City, State" or "City, ST, Country"
            parts = [part.strip() for part in location_text.split(',')]
            
            city = parts[0] if parts else ""
            state = parts[1] if len(parts) > 1 else ""
            country = parts[2] if len(parts) > 2 else "USA"
            
            # Clean up state (convert full state names to abbreviations if needed)
            if len(state) > 2:
                state_abbrev = self._get_state_abbreviation(state)
                if state_abbrev:
                    state = state_abbrev
            
            return city, state, country
            
        except Exception as e:
            logger.warning(f"Error parsing location '{location_text}': {e}")
            return "", "", "USA"
    
    def _parse_date(self, date_text: str) -> str:
        """Parse date text into ISO format"""
        try:
            if not date_text:
                return datetime.now().strftime('%Y-%m-%d')
            
            # Try various date parsing patterns
            date_patterns = [
                '%Y-%m-%d',
                '%m/%d/%Y',
                '%d/%m/%Y',
                '%B %d, %Y',
                '%b %d, %Y',
                '%Y-%m-%d %H:%M:%S',
            ]
            
            # Clean the date text
            date_clean = re.sub(r'[^\w\s/:-]', '', date_text).strip()
            
            for pattern in date_patterns:
                try:
                    parsed_date = datetime.strptime(date_clean, pattern)
                    return parsed_date.strftime('%Y-%m-%d')
                except ValueError:
                    continue
            
            # If all parsing fails, try extracting year/month/day with regex
            year_match = re.search(r'20\d{2}', date_text)
            if year_match:
                return f"{year_match.group()}-01-01"  # Default to January 1st
            
            return datetime.now().strftime('%Y-%m-%d')
            
        except Exception as e:
            logger.warning(f"Error parsing date '{date_text}': {e}")
            return datetime.now().strftime('%Y-%m-%d')
    
    def _get_state_abbreviation(self, state_name: str) -> Optional[str]:
        """Convert full state name to abbreviation"""
        state_map = {
            'california': 'CA', 'new york': 'NY', 'texas': 'TX', 'florida': 'FL',
            'illinois': 'IL', 'pennsylvania': 'PA', 'ohio': 'OH', 'georgia': 'GA',
            'north carolina': 'NC', 'michigan': 'MI', 'new jersey': 'NJ', 'virginia': 'VA',
            'washington': 'WA', 'arizona': 'AZ', 'massachusetts': 'MA', 'tennessee': 'TN',
            'indiana': 'IN', 'missouri': 'MO', 'maryland': 'MD', 'wisconsin': 'WI',
            'colorado': 'CO', 'minnesota': 'MN', 'south carolina': 'SC', 'alabama': 'AL',
            'louisiana': 'LA', 'kentucky': 'KY', 'oregon': 'OR', 'oklahoma': 'OK',
            'connecticut': 'CT', 'utah': 'UT', 'iowa': 'IA', 'nevada': 'NV',
            'arkansas': 'AR', 'mississippi': 'MS', 'kansas': 'KS', 'new mexico': 'NM',
            'nebraska': 'NE', 'west virginia': 'WV', 'idaho': 'ID', 'hawaii': 'HI',
            'new hampshire': 'NH', 'maine': 'ME', 'montana': 'MT', 'rhode island': 'RI',
            'delaware': 'DE', 'south dakota': 'SD', 'north dakota': 'ND', 'alaska': 'AK',
            'vermont': 'VT', 'wyoming': 'WY'
        }
        return state_map.get(state_name.lower())
    
    def generate_geojson(self, shows: List[Show]) -> Dict:
        """Generate GeoJSON from processed shows"""
        geojson = {
            "type": "FeatureCollection",
            "features": [],
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_shows": len(shows),
                "source": "bandsintown_scraper"
            }
        }
        
        for show in shows:
            # Add venue polygon
            if show.venue_polygon:
                geojson["features"].append({
                    "type": "Feature",
                    "geometry": show.venue_polygon,
                    "properties": {
                        "type": "venue",
                        "artist": show.artist,
                        "venue_name": show.venue_name,
                        "address": show.address,
                        "city": show.city,
                        "state": show.state,
                        "date": show.date,
                        "coordinates": f"{show.latitude},{show.longitude}"
                    }
                })
            
            # Add parking polygons
            for parking in show.parking_polygons:
                geojson["features"].append({
                    "type": "Feature",
                    "geometry": parking['geometry'],
                    "properties": {
                        "type": "parking",
                        "parking_name": parking['name'],
                        "parking_type": parking['parking_type'],
                        "venue_name": show.venue_name,
                        "artist": show.artist,
                        "show_date": show.date
                    }
                })
        
        return geojson
    
    def generate_csv(self, shows: List[Show]) -> str:
        """Generate CSV from processed shows"""
        csv_lines = [
            "artist,venue_name,address,city,state,country,date,latitude,longitude,parking_count"
        ]
        
        for show in shows:
            csv_lines.append(
                f'"{show.artist}","{show.venue_name}","{show.address}",'
                f'"{show.city}","{show.state}","{show.country}","{show.date}",'
                f'{show.latitude},{show.longitude},{len(show.parking_polygons)}'
            )
        
        return "\n".join(csv_lines)

# Personal Dashboard HTML
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Personal Bandsintown Venue Scraper</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; background: white; border-radius: 20px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); overflow: hidden; }
        .header { background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); color: white; padding: 30px; text-align: center; }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; font-weight: 300; }
        .main-content { padding: 40px; }
        .section { background: #f8f9fa; padding: 30px; border-radius: 15px; margin-bottom: 30px; border-left: 5px solid #667eea; }
        .btn { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 15px 30px; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s ease; margin: 10px 5px; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 10px 25px rgba(102, 126, 234, 0.3); }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
        .btn-success { background: linear-gradient(135deg, #28a745 0%, #20c997 100%); }
        .btn-danger { background: linear-gradient(135deg, #dc3545 0%, #fd7e14 100%); }
        .url-input { width: 100%; padding: 15px; border: 2px solid #e9ecef; border-radius: 10px; font-size: 16px; margin: 10px 0; }
        .url-list { background: white; padding: 15px; border-radius: 10px; margin: 10px 0; max-height: 300px; overflow-y: auto; }
        .url-item { padding: 15px; background: #f8f9fa; margin: 5px 0; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; border-left: 4px solid #667eea; }
        .remove-btn { background: #dc3545; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; font-size: 14px; }
        .status-box { padding: 15px; border-radius: 10px; margin: 15px 0; }
        .status-box.success { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }
        .status-box.error { background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }
        .status-box.info { background: #d1ecf1; border: 1px solid #bee5eb; color: #0c5460; }
        .status-box.warning { background: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }
        .hidden { display: none; }
        .progress-bar { width: 100%; height: 20px; background: #e9ecef; border-radius: 10px; overflow: hidden; margin: 10px 0; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); transition: width 0.5s ease; width: 0%; }
        .show-item { background: white; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #28a745; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
        .stat-card { background: white; padding: 20px; border-radius: 10px; text-align: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .stat-number { font-size: 2em; font-weight: bold; color: #667eea; }
        .checkbox-option { margin: 10px 0; }
        .checkbox-option input { margin-right: 10px; }
        .download-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin: 20px 0; }
        .download-card { background: white; padding: 20px; border-radius: 10px; border: 2px solid #e9ecef; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎵 Personal Bandsintown Venue Scraper</h1>
            <p>Extract venue data and generate polygons from artist tour pages</p>
        </div>
        <div class="main-content">
            
            <div class="section">
                <h2>🔗 Add Artist URLs</h2>
                <p>Enter Bandsintown artist URLs to scrape their tour venues:</p>
                <input type="url" id="urlInput" class="url-input" placeholder="https://www.bandsintown.com/a/12526716-the-new-dove-brothers">
                <div style="display: flex; gap: 10px; align-items: center; margin: 10px 0;">
                    <button class="btn" onclick="addUrl()">➕ Add URL</button>
                    <button class="btn btn-danger" onclick="clearAllUrls()">🗑️ Clear All</button>
                </div>
                
                <div class="checkbox-option">
                    <label>
                        <input type="checkbox" id="loadAllDates" checked>
                        📅 Load all dates (click "Show more dates" automatically)
                    </label>
                </div>
                <div class="checkbox-option">
                    <label>
                        <input type="checkbox" id="includeParking" checked>
                        🅿️ Include parking area polygons
                    </label>
                </div>
                
                <div id="urlList" class="url-list hidden"></div>
                <button class="btn btn-success" onclick="startScraping()" id="scrapeBtn" style="font-size: 18px; padding: 20px 40px;">
                    🚀 Start Scraping & Generate Polygons
                </button>
            </div>

            <div id="processingSection" class="section hidden">
                <h2>⚡ Processing Status</h2>
                <div id="status" class="status-box info">Ready to process...</div>
                <div id="progress" class="progress-bar hidden">
                    <div id="progressFill" class="progress-fill"></div>
                </div>
                <div id="processingDetails" class="hidden"></div>
            </div>

            <div id="resultsSection" class="section hidden">
                <h2>📊 Processing Results</h2>
                <div id="statsGrid" class="stats-grid"></div>
                <div id="showsList" class="hidden"></div>
            </div>

            <div id="downloadSection" class="section hidden">
                <h2>📥 Download Your Data</h2>
                <div class="download-grid">
                    <div class="download-card">
                        <h4>🗺️ GeoJSON File</h4>
                        <p>Venue & parking polygons for mapping</p>
                        <button class="btn btn-success" onclick="downloadGeojson()">Download GeoJSON</button>
                    </div>
                    <div class="download-card">
                        <h4>📊 CSV Data</h4>
                        <p>Venue data for spreadsheets</p>
                        <button class="btn btn-success" onclick="downloadCSV()">Download CSV</button>
                    </div>
                    <div class="download-card">
                        <h4>📋 JSON Report</h4>
                        <p>Complete processing report</p>
                        <button class="btn btn-success" onclick="downloadJSON()">Download JSON</button>
                    </div>
                </div>
            </div>

            <div class="section">
                <h2>ℹ️ How It Works</h2>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin: 20px 0;">
                    <div style="background: white; padding: 20px; border-radius: 10px;">
                        <h4>1. 🕷️ Web Scraping</h4>
                        <p>Automatically scrapes Bandsintown artist pages, clicking "Show more dates" to load all historical shows</p>
                    </div>
                    <div style="background: white; padding: 20px; border-radius: 10px;">
                        <h4>2. 📍 Geocoding</h4>
                        <p>Converts venue names and addresses to precise GPS coordinates using Google Maps API</p>
                    </div>
                    <div style="background: white; padding: 20px; border-radius: 10px;">
                        <h4>3. 🗺️ Polygon Generation</h4>
                        <p>Creates venue building polygons and finds nearby parking areas with their own polygons</p>
                    </div>
                    <div style="background: white; padding: 20px; border-radius: 10px;">
                        <h4>4. 📤 Data Export</h4>
                        <p>Delivers GeoJSON files ready for mapping software, plus CSV for analysis</p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let artistUrls = [];
        let processedData = null;

        function addUrl() {
            const input = document.getElementById('urlInput');
            const url = input.value.trim();
            
            if (!url) {
                alert('Please enter a URL');
                return;
            }
            
            if (!url.includes('bandsintown.com/a/')) {
                if (!confirm('This doesn\'t look like a Bandsintown artist URL. Add anyway?')) {
                    return;
                }
            }
            
            if (artistUrls.includes(url)) {
                alert('This URL is already in the list');
                return;
            }
            
            artistUrls.push(url);
            input.value = '';
            updateUrlDisplay();
            showStatus(`Added artist URL. Total: ${artistUrls.length}`, 'info');
        }
        
        function removeUrl(index) {
            artistUrls.splice(index, 1);
            updateUrlDisplay();
            showStatus(`Removed URL. Total: ${artistUrls.length}`, 'info');
        }
        
        function clearAllUrls() {
            if (artistUrls.length === 0) return;
            if (confirm(`Clear all ${artistUrls.length} URLs?`)) {
                artistUrls = [];
                updateUrlDisplay();
                showStatus('All URLs cleared', 'info');
            }
        }
        
        function updateUrlDisplay() {
            const container = document.getElementById('urlList');
            if (artistUrls.length === 0) {
                container.classList.add('hidden');
                return;
            }
            
            container.classList.remove('hidden');
            container.innerHTML = artistUrls.map((url, index) => {
                // Extract artist name from URL
                const match = url.match(/\/a\/\d+-(.+)/);
                const artistName = match ? match[1].replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase()) : 'Unknown Artist';
                
                return `
                    <div class="url-item">
                        <div>
                            <strong>${artistName}</strong><br>
                            <small>${url}</small>
                        </div>
                        <button class="remove-btn" onclick="removeUrl(${index})">Remove</button>
                    </div>
                `;
            }).join('');
        }

        function showStatus(message, type) {
            const status = document.getElementById('status');
            status.className = `status-box ${type}`;
            status.innerHTML = message;
            document.getElementById('processingSection').classList.remove('hidden');
        }
        
        function updateProgress(percentage) {
            const progress = document.getElementById('progress');
            const fill = document.getElementById('progressFill');
            progress.classList.remove('hidden');
            fill.style.width = percentage + '%';
        }
        
        async function startScraping() {
            if (artistUrls.length === 0) {
                alert('Please add at least one artist URL');
                return;
            }
            
            const loadAllDates = document.getElementById('loadAllDates').checked;
            const includeParking = document.getElementById('includeParking').checked;
            
            showStatus('🕷️ Starting web scraping process...', 'info');
            updateProgress(10);
            
            try {
                const response = await fetch('/api/scrape-artists', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        urls: artistUrls,
                        load_all_dates: loadAllDates,
                        include_parking: includeParking
                    })
                });
                
                updateProgress(30);
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                updateProgress(100);
                
                if (data.success) {
                    processedData = data;
                    showStatus(`🎉 Success! Processed ${data.stats.total_shows_found} shows from ${data.stats.artists_processed} artists`, 'success');
                    displayResults(data);
                    document.getElementById('downloadSection').classList.remove('hidden');
                } else {
                    showStatus('❌ Scraping failed: ' + data.error, 'error');
                }
            } catch (error) {
                showStatus('❌ Error: ' + error.message, 'error');
                updateProgress(0);
            }
        }
        
        function displayResults(data) {
            // Show statistics
            const statsGrid = document.getElementById('statsGrid');
            statsGrid.innerHTML = `
                <div class="stat-card">
                    <div class="stat-number">${data.stats.artists_processed}</div>
                    <div>Artists Processed</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${data.stats.total_shows_found}</div>
                    <div>Shows Found</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${data.stats.geocoded_shows}</div>
                    <div>Successfully Geocoded</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number">${data.stats.total_polygons}</div>
                    <div>Total Polygons</div>
                </div>
            `;
            
            // Show individual shows
            const showsList = document.getElementById('showsList');
            if (data.shows && data.shows.length > 0) {
                showsList.innerHTML = '<h3>📍 Processed Shows:</h3>' + 
                    data.shows.slice(0, 10).map(show => `
                        <div class="show-item">
                            <strong>${show.artist}</strong> at <strong>${show.venue_name}</strong><br>
                            📍 ${show.address}<br>
                            📅 ${show.date}<br>
                            🗺️ ${show.latitude.toFixed(6)}, ${show.longitude.toFixed(6)}<br>
                            🅿️ ${show.parking_count} parking areas found
                        </div>
                    `).join('');
                
                if (data.shows.length > 10) {
                    showsList.innerHTML += `<p><em>... and ${data.shows.length - 10} more shows</em></p>`;
                }
                
                showsList.classList.remove('hidden');
            }
            
            document.getElementById('resultsSection').classList.remove('hidden');
        }
        
        function downloadGeojson() {
            if (!processedData) return alert('No data to download');
            
            const blob = new Blob([JSON.stringify(processedData.geojson, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `bandsintown_venues_${new Date().toISOString().slice(0,10)}.geojson`;
            a.click();
            URL.revokeObjectURL(url);
        }
        
        function downloadCSV() {
            if (!processedData) return alert('No data to download');
            
            const blob = new Blob([processedData.csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `bandsintown_venues_${new Date().toISOString().slice(0,10)}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        }
        
        function downloadJSON() {
            if (!processedData) return alert('No data to download');
            
            const report = {
                metadata: {
                    generated_at: new Date().toISOString(),
                    source: 'bandsintown_scraper',
                    total_artists: processedData.stats.artists_processed,
                    total_shows: processedData.stats.geocoded_shows
                },
                statistics: processedData.stats,
                shows: processedData.shows
            };
            
            const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `bandsintown_report_${new Date().toISOString().slice(0,10)}.json`;
            a.click();
            URL.revokeObjectURL(url);
        }
        
        // Allow Enter key to add URLs
        document.getElementById('urlInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                addUrl();
            }
        });
    </script>
</body>
</html>
'''

# Flask Routes
@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/scrape-artists', methods=['POST'])
def scrape_artists():
    try:
        api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
        if not api_key:
            return jsonify({'success': False, 'error': 'Google Maps API key not configured'})
        
        data = request.get_json()
        urls = data.get('urls', [])
        load_all_dates = data.get('load_all_dates', True)
        include_parking = data.get('include_parking', True)
        
        if not urls:
            return jsonify({'success': False, 'error': 'No URLs provided'})
        
        # Process the artist URLs
        processor = VenueProcessor(api_key)
        shows, stats = processor.process_artist_urls(urls, load_all_dates)
        
        if not shows:
            return jsonify({'success': False, 'error': 'No shows could be processed from the provided URLs'})
        
        # Generate outputs
        geojson = processor.generate_geojson(shows)
        csv_content = processor.generate_csv(shows)
        
        return jsonify({
            'success': True,
            'stats': stats,
            'shows': [
                {
                    'artist': show.artist,
                    'venue_name': show.venue_name,
                    'address': show.address,
                    'city': show.city,
                    'state': show.state,
                    'date': show.date,
                    'latitude': show.latitude,
                    'longitude': show.longitude,
                    'parking_count': len(show.parking_polygons)
                }
                for show in shows
            ],
            'geojson': geojson,
            'csv': csv_content
        })
        
    except Exception as e:
        logger.error(f"Scraping error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
