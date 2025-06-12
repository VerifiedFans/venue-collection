import os
import json
import time
import math
from datetime import datetime
from typing import Dict, List, Optional
import logging

from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import requests
from dataclasses import dataclass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app - THIS IS CRITICAL
app = Flask(__name__)
CORS(app)

@dataclass
class Venue:
    name: str
    address: str
    city: str
    state: str
    country: str
    latitude: float
    longitude: float
    date: str
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

class GoogleMapsAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://maps.googleapis.com/maps/api"
        self.session = requests.Session()
    
    def test_api_key(self) -> bool:
        try:
            url = f"{self.base_url}/geocode/json"
            params = {
                'address': 'Madison Square Garden, New York, NY',
                'key': self.api_key
            }
            response = self.session.get(url, params=params, timeout=10)
            data = response.json()
            return data['status'] == 'OK'
        except Exception as e:
            logger.error(f"API key test error: {e}")
            return False
    
    def geocode_venue(self, venue_name: str, city: str, state: str = "", country: str = "USA") -> Optional[Dict]:
        query = f"{venue_name}, {city}"
        if state:
            query += f", {state}"
        if country:
            query += f", {country}"
        
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
    
    def find_parking_areas(self, latitude: float, longitude: float, radius: int = 500) -> List[ParkingArea]:
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
        name_lower = name.lower()
        if 'garage' in name_lower or 'structure' in name_lower:
            return 'garage'
        elif 'lot' in name_lower:
            return 'lot'
        else:
            return 'street'

class PolygonGenerator:
    def __init__(self):
        self.earth_radius = 6371000
    
    def generate_venue_polygon(self, venue: Venue, buffer_meters: int = 100) -> Dict:
        try:
            coords = self._create_circular_polygon(
                venue.latitude, venue.longitude, buffer_meters, points=16
            )
            return {
                "type": "Polygon",
                "coordinates": [coords]
            }
        except Exception as e:
            logger.error(f"Error generating venue polygon: {e}")
            return None
    
    def generate_parking_polygon(self, parking: ParkingArea) -> Dict:
        try:
            if parking.type == 'garage':
                buffer_meters = 40
                points = 8
            elif parking.type == 'lot':
                buffer_meters = 60
                points = 12
            else:
                buffer_meters = 15
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
        coords = []
        
        for i in range(points + 1):
            angle = (i * 2 * math.pi) / points
            delta_lat = (radius_meters * math.cos(angle)) / self.earth_radius * (180 / math.pi)
            delta_lng = (radius_meters * math.sin(angle)) / (self.earth_radius * math.cos(math.radians(lat))) * (180 / math.pi)
            
            new_lat = lat + delta_lat
            new_lng = lng + delta_lng
            
            coords.append([new_lng, new_lat])
        
        return coords

class VenueProcessor:
    def __init__(self, api_key: str):
        self.google_maps = GoogleMapsAPI(api_key)
        self.polygon_generator = PolygonGenerator()
        
        self.sample_venues = [
            {"name": "Madison Square Garden", "city": "New York", "state": "NY", "date": "2024-03-15"},
            {"name": "Staples Center", "city": "Los Angeles", "state": "CA", "date": "2024-02-28"},
            {"name": "United Center", "city": "Chicago", "state": "IL", "date": "2024-01-20"},
            {"name": "TD Garden", "city": "Boston", "state": "MA", "date": "2023-12-10"},
            {"name": "American Airlines Center", "city": "Dallas", "state": "TX", "date": "2023-11-25"}
        ]
    
    def process_sample_venues(self, max_venues: int = 5) -> List[Venue]:
        processed_venues = []
        
        for venue_data in self.sample_venues[:max_venues]:
            logger.info(f"Processing venue: {venue_data['name']}")
            
            geocode_result = self.google_maps.geocode_venue(
                venue_data['name'], venue_data['city'], venue_data['state']
            )
            
            if not geocode_result:
                logger.warning(f"Skipping {venue_data['name']} - geocoding failed")
                continue
            
            venue = Venue(
                name=venue_data['name'],
                address=geocode_result['formatted_address'],
                city=venue_data['city'],
                state=venue_data['state'],
                country="USA",
                latitude=geocode_result['latitude'],
                longitude=geocode_result['longitude'],
                date=venue_data['date']
            )
            
            venue.venue_polygon = self.polygon_generator.generate_venue_polygon(venue, 100)
            
            parking_areas = self.google_maps.find_parking_areas(venue.latitude, venue.longitude, 500)
            
            for parking in parking_areas[:10]:
                parking_polygon = self.polygon_generator.generate_parking_polygon(parking)
                if parking_polygon:
                    venue.parking_polygons.append({
                        'geometry': parking_polygon,
                        'name': parking.name,
                        'parking_type': parking.type,
                        'place_id': parking.place_id
                    })
            
            processed_venues.append(venue)
            time.sleep(0.5)
        
        return processed_venues

# HTML Template
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Concert Venue Data Collection - Live System</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1000px; margin: 0 auto; background: white; border-radius: 20px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); overflow: hidden; }
        .header { background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); color: white; padding: 30px; text-align: center; }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; font-weight: 300; }
        .main-content { padding: 40px; }
        .step-section { background: #f8f9fa; padding: 30px; border-radius: 15px; margin-bottom: 30px; border-left: 5px solid #667eea; }
        .btn { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 15px 30px; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s ease; margin: 10px 5px; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 10px 25px rgba(102, 126, 234, 0.3); }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
        .btn-success { background: linear-gradient(135deg, #28a745 0%, #20c997 100%); }
        .status-box { padding: 15px; border-radius: 10px; margin: 15px 0; }
        .status-box.success { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }
        .status-box.error { background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }
        .status-box.info { background: #d1ecf1; border: 1px solid #bee5eb; color: #0c5460; }
        .hidden { display: none; }
        .progress-bar { width: 100%; height: 20px; background: #e9ecef; border-radius: 10px; overflow: hidden; margin: 10px 0; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); transition: width 0.5s ease; width: 0%; }
        .venue-item { background: white; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #667eea; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .file-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin: 20px 0; }
        .file-card { background: white; padding: 20px; border-radius: 10px; border: 2px solid #e9ecef; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎵 Concert Venue Data Collection</h1>
            <p>Production System - Live on Railway</p>
        </div>
        <div class="main-content">
            <div class="step-section">
                <h2>🚀 System Status & Controls</h2>
                <button class="btn" onclick="testSystem()">🧪 Test Google Maps API</button>
                <button class="btn" onclick="generateSampleData()">🎯 Generate Sample Venues</button>
                <div id="status" class="status-box hidden"></div>
                <div id="progress" class="progress-bar hidden">
                    <div id="progressFill" class="progress-fill"></div>
                </div>
                <div id="results" class="hidden"></div>
                <div id="downloadSection" class="hidden">
                    <h3>📥 Download Generated Files</h3>
                    <div class="file-grid">
                        <div class="file-card">
                            <h4>🗺️ GeoJSON File</h4>
                            <p>Venue & parking polygons</p>
                            <button class="btn btn-success" onclick="downloadGeojson()">Download</button>
                        </div>
                        <div class="file-card">
                            <h4>📊 CSV Summary</h4>
                            <p>Venue data spreadsheet</p>
                            <button class="btn btn-success" onclick="downloadCSV()">Download</button>
                        </div>
                        <div class="file-card">
                            <h4>📋 JSON Report</h4>
                            <p>Complete processing report</p>
                            <button class="btn btn-success" onclick="downloadJSON()">Download</button>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="step-section">
                <h2>ℹ️ System Information</h2>
                <p><strong>Platform:</strong> Railway.app (Cloud Hosted)</p>
                <p><strong>APIs:</strong> Google Maps Geocoding + Places</p>
                <p><strong>Outputs:</strong> GeoJSON, CSV, JSON formats</p>
                <p><strong>Status:</strong> <span id="systemStatus">Ready</span></p>
            </div>
        </div>
    </div>

    <script>
        let processedData = null;
        
        function showStatus(message, type) {
            const status = document.getElementById('status');
            status.className = `status-box ${type}`;
            status.innerHTML = message;
            status.classList.remove('hidden');
        }
        
        function updateProgress(percentage) {
            const progress = document.getElementById('progress');
            const fill = document.getElementById('progressFill');
            progress.classList.remove('hidden');
            fill.style.width = percentage + '%';
        }
        
        async function testSystem() {
            showStatus('🔍 Testing Google Maps API connection...', 'info');
            document.getElementById('systemStatus').textContent = 'Testing...';
            
            try {
                const response = await fetch('/api/test');
                const data = await response.json();
                
                if (data.success) {
                    showStatus('✅ System operational! Google Maps API is working perfectly.', 'success');
                    document.getElementById('systemStatus').textContent = 'Operational ✅';
                } else {
                    showStatus('❌ API Test Failed: ' + data.error, 'error');
                    document.getElementById('systemStatus').textContent = 'Error ❌';
                }
            } catch (error) {
                showStatus('❌ Connection Error: ' + error.message, 'error');
                document.getElementById('systemStatus').textContent = 'Error ❌';
            }
        }
        
        async function generateSampleData() {
            showStatus('🚀 Processing sample venues with real Google Maps data...', 'info');
            updateProgress(20);
            
            try {
                const response = await fetch('/api/process', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ max_venues: 3 })
                });
                
                updateProgress(70);
                const data = await response.json();
                updateProgress(100);
                
                if (data.success) {
                    processedData = data;
                    showStatus(`🎉 Success! Generated ${data.venues.length} venues with ${data.total_polygons} total polygons.`, 'success');
                    displayResults(data.venues);
                    document.getElementById('downloadSection').classList.remove('hidden');
                } else {
                    showStatus('❌ Processing Failed: ' + data.error, 'error');
                }
            } catch (error) {
                showStatus('❌ Processing Error: ' + error.message, 'error');
            }
        }
        
        function displayResults(venues) {
            const results = document.getElementById('results');
            results.innerHTML = '<h3>📍 Generated Venues:</h3>';
            
            venues.forEach(venue => {
                const item = document.createElement('div');
                item.className = 'venue-item';
                item.innerHTML = `
                    <strong>${venue.name}</strong><br>
                    📍 ${venue.address}<br>
                    🗺️ (${venue.latitude.toFixed(6)}, ${venue.longitude.toFixed(6)})<br>
                    🅿️ ${venue.parking_count} parking areas detected<br>
                    📅 ${venue.date}
                `;
                results.appendChild(item);
            });
            
            results.classList.remove('hidden');
        }
        
        function downloadGeojson() {
            if (!processedData) return alert('Generate venues first');
            
            const blob = new Blob([JSON.stringify(processedData.geojson, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `venue_data_${new Date().toISOString().slice(0,10)}.geojson`;
            a.click();
            URL.revokeObjectURL(url);
        }
        
        function downloadCSV() {
            if (!processedData) return alert('Generate venues first');
            
            const blob = new Blob([processedData.csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `venue_summary_${new Date().toISOString().slice(0,10)}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        }
        
        function downloadJSON() {
            if (!processedData) return alert('Generate venues first');
            
            const report = {
                summary: {
                    total_venues: processedData.venues.length,
                    total_polygons: processedData.total_polygons,
                    generated_at: new Date().toISOString()
                },
                venues: processedData.venues
            };
            
            const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `venue_report_${new Date().toISOString().slice(0,10)}.json`;
            a.click();
            URL.revokeObjectURL(url);
        }
        
        // Auto-test on load
        window.addEventListener('load', () => setTimeout(testSystem, 1000));
    </script>
</body>
</html>
'''

# Flask Routes
@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/test', methods=['GET'])
def test_api():
    try:
        api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
        if not api_key:
            return jsonify({'success': False, 'error': 'Google Maps API key not configured'})
        
        processor = VenueProcessor(api_key)
        if processor.google_maps.test_api_key():
            return jsonify({'success': True, 'message': 'Google Maps API working'})
        else:
            return jsonify({'success': False, 'error': 'API key validation failed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/process', methods=['POST'])
def process_venues():
    try:
        api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
        if not api_key:
            return jsonify({'success': False, 'error': 'Google Maps API key not configured'})
        
        data = request.get_json()
        max_venues = data.get('max_venues', 3)
        
        processor = VenueProcessor(api_key)
        venues = processor.process_sample_venues(max_venues)
        
        if not venues:
            return jsonify({'success': False, 'error': 'No venues could be processed'})
        
        geojson_data = {
            "type": "FeatureCollection",
            "features": [],
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "venue_count": len(venues)
            }
        }
        
        total_polygons = 0
        
        for venue in venues:
            if venue.venue_polygon:
                geojson_data["features"].append({
                    "type": "Feature",
                    "geometry": venue.venue_polygon,
                    "properties": {
                        "type": "venue",
                        "name": venue.name,
                        "address": venue.address,
                        "city": venue.city,
                        "state": venue.state,
                        "date": venue.date
                    }
                })
                total_polygons += 1
            
            for parking in venue.parking_polygons:
                geojson_data["features"].append({
                    "type": "Feature",
                    "geometry": parking['geometry'],
                    "properties": {
                        "type": "parking",
                        "name": parking['name'],
                        "parking_type": parking['parking_type'],
                        "venue_name": venue.name
                    }
                })
                total_polygons += 1
        
        csv_lines = ["venue_name,address,city,state,latitude,longitude,date,parking_count"]
        for venue in venues:
            csv_lines.append(f'"{venue.name}","{venue.address}","{venue.city}","{venue.state}",'
                           f'{venue.latitude},{venue.longitude},"{venue.date}",{len(venue.parking_polygons)}')
        csv_content = "\n".join(csv_lines)
        
        return jsonify({
            'success': True,
            'venues': [
                {
                    'name': v.name,
                    'address': v.address,
                    'city': v.city,
                    'state': v.state,
                    'latitude': v.latitude,
                    'longitude': v.longitude,
                    'date': v.date,
                    'parking_count': len(v.parking_polygons)
                }
                for v in venues
            ],
            'total_polygons': total_polygons,
            'geojson': geojson_data,
            'csv': csv_content
        })
        
    except Exception as e:
        logger.error(f"Processing error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

# This is critical for Railway deployment
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
