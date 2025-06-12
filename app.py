import os
import json
import time
import math
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging
import uuid
import hashlib

from flask import Flask, request, jsonify, render_template_string, redirect, session
from flask_cors import CORS
import requests
from dataclasses import dataclass, asdict
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'venuemapper-pro-2025')
CORS(app)

@dataclass
class User:
    id: str
    email: str
    name: str
    plan: str  # free, pro, enterprise
    api_requests: int
    created_at: str
    api_key: str = ""

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
    artist: str = ""
    user_id: str = ""
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

# In-memory storage (use database in production)
users_db = {}
venues_db = {}
artist_submissions = {}
usage_stats = {"total_venues": 0, "total_users": 0, "total_requests": 0}

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

class VenueMapperPro:
    def __init__(self, api_key: str):
        self.google_maps = GoogleMapsAPI(api_key)
        self.polygon_generator = PolygonGenerator()
    
    def create_user(self, email: str, name: str, plan: str = "free") -> User:
        user_id = str(uuid.uuid4())
        api_key = hashlib.md5(f"{user_id}{email}".encode()).hexdigest()
        
        user = User(
            id=user_id,
            email=email,
            name=name,
            plan=plan,
            api_requests=0,
            created_at=datetime.now().isoformat(),
            api_key=api_key
        )
        
        users_db[user_id] = user
        usage_stats["total_users"] += 1
        return user
    
    def get_user_limits(self, plan: str) -> Dict:
        limits = {
            "free": {"venues_per_month": 50, "api_requests": 1000, "features": ["basic_geocoding", "csv_export"]},
            "pro": {"venues_per_month": 500, "api_requests": 10000, "features": ["advanced_geocoding", "all_exports", "api_access", "live_events"]},
            "enterprise": {"venues_per_month": 5000, "api_requests": 100000, "features": ["unlimited", "priority_support", "custom_integrations"]}
        }
        return limits.get(plan, limits["free"])
    
    def process_venues_for_user(self, user_id: str, venues_data: List[Dict]) -> List[Venue]:
        user = users_db.get(user_id)
        if not user:
            raise ValueError("User not found")
        
        limits = self.get_user_limits(user.plan)
        if len(venues_data) > limits["venues_per_month"]:
            raise ValueError(f"Venue limit exceeded. {user.plan} plan allows {limits['venues_per_month']} venues per month")
        
        processed_venues = []
        
        for venue_data in venues_data:
            geocode_result = self.google_maps.geocode_venue(
                venue_data.get('name', ''), 
                venue_data.get('city', ''), 
                venue_data.get('state', '')
            )
            
            if not geocode_result:
                continue
            
            venue = Venue(
                name=venue_data.get('name', 'Unknown Venue'),
                address=geocode_result['formatted_address'],
                city=venue_data.get('city', ''),
                state=venue_data.get('state', ''),
                country=venue_data.get('country', 'USA'),
                latitude=geocode_result['latitude'],
                longitude=geocode_result['longitude'],
                date=venue_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                artist=venue_data.get('artist', ''),
                user_id=user_id
            )
            
            # Generate polygons
            venue.venue_polygon = self.polygon_generator.generate_venue_polygon(venue, 100)
            
            # Find parking areas
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
            venues_db[f"{user_id}_{len(venues_db)}"] = venue
            
            # Update usage stats
            user.api_requests += 1
            usage_stats["total_venues"] += 1
            usage_stats["total_requests"] += 1
            
            time.sleep(0.3)  # Rate limiting
        
        return processed_venues

# Professional Dashboard HTML
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VenueMapper Pro - Professional Venue Data Platform</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #f8fafc; min-height: 100vh; }
        
        .navbar { background: #1e293b; color: white; padding: 1rem 2rem; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .logo { font-size: 1.5rem; font-weight: bold; }
        .nav-items { display: flex; gap: 2rem; align-items: center; }
        .nav-link { color: #cbd5e1; text-decoration: none; transition: color 0.2s; }
        .nav-link:hover { color: white; }
        
        .container { max-width: 1200px; margin: 0 auto; padding: 2rem; }
        .hero { text-align: center; margin: 3rem 0; }
        .hero h1 { font-size: 3rem; font-weight: 700; color: #1e293b; margin-bottom: 1rem; }
        .hero p { font-size: 1.25rem; color: #64748b; margin-bottom: 2rem; }
        
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem; margin: 2rem 0; }
        .stat-card { background: white; padding: 2rem; border-radius: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #3b82f6; }
        .stat-number { font-size: 2.5rem; font-weight: bold; color: #3b82f6; }
        .stat-label { color: #64748b; font-weight: 500; margin-top: 0.5rem; }
        
        .section { background: white; padding: 2rem; border-radius: 1rem; margin: 2rem 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .section h2 { font-size: 1.5rem; font-weight: 600; color: #1e293b; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem; }
        
        .btn { background: #3b82f6; color: white; border: none; padding: 0.75rem 1.5rem; border-radius: 0.5rem; font-weight: 600; cursor: pointer; transition: all 0.2s; text-decoration: none; display: inline-block; }
        .btn:hover { background: #2563eb; transform: translateY(-1px); }
        .btn-success { background: #10b981; }
        .btn-success:hover { background: #059669; }
        .btn-warning { background: #f59e0b; }
        .btn-warning:hover { background: #d97706; }
        .btn-danger { background: #ef4444; }
        .btn-danger:hover { background: #dc2626; }
        
        .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1rem; margin: 1rem 0; }
        .form-control { padding: 0.75rem; border: 1px solid #d1d5db; border-radius: 0.5rem; font-size: 1rem; }
        .form-control:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.1); }
        
        .pricing-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 2rem; margin: 2rem 0; }
        .pricing-card { background: white; border: 2px solid #e5e7eb; border-radius: 1rem; padding: 2rem; text-align: center; position: relative; }
        .pricing-card.featured { border-color: #3b82f6; transform: scale(1.05); }
        .pricing-card.featured::before { content: "MOST POPULAR"; position: absolute; top: -12px; left: 50%; transform: translateX(-50%); background: #3b82f6; color: white; padding: 0.5rem 1rem; border-radius: 1rem; font-size: 0.875rem; font-weight: 600; }
        .price { font-size: 3rem; font-weight: bold; color: #1e293b; }
        .price-period { color: #64748b; }
        
        .feature-list { list-style: none; margin: 1.5rem 0; }
        .feature-list li { padding: 0.5rem 0; display: flex; align-items: center; gap: 0.5rem; }
        .feature-list li::before { content: "✓"; color: #10b981; font-weight: bold; }
        
        .api-section { background: #f1f5f9; padding: 2rem; border-radius: 1rem; margin: 1rem 0; }
        .code-block { background: #1e293b; color: #cbd5e1; padding: 1rem; border-radius: 0.5rem; font-family: 'Monaco', 'Menlo', monospace; font-size: 0.875rem; overflow-x: auto; }
        
        .status-indicator { display: inline-block; width: 0.75rem; height: 0.75rem; border-radius: 50%; margin-right: 0.5rem; }
        .status-online { background: #10b981; }
        .status-offline { background: #ef4444; }
        
        .hidden { display: none; }
        .alert { padding: 1rem; border-radius: 0.5rem; margin: 1rem 0; }
        .alert-success { background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }
        .alert-error { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
        .alert-info { background: #eff6ff; color: #1e40af; border: 1px solid #dbeafe; }
        
        .venue-item { background: #f8fafc; padding: 1.5rem; margin: 1rem 0; border-radius: 0.5rem; border-left: 4px solid #3b82f6; }
        .progress-bar { width: 100%; height: 0.5rem; background: #e5e7eb; border-radius: 0.25rem; overflow: hidden; margin: 1rem 0; }
        .progress-fill { height: 100%; background: #3b82f6; transition: width 0.5s ease; width: 0%; }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">🗺️ VenueMapper Pro</div>
        <div class="nav-items">
            <a href="#features" class="nav-link">Features</a>
            <a href="#pricing" class="nav-link">Pricing</a>
            <a href="#api" class="nav-link">API</a>
            <a href="#dashboard" class="nav-link">Dashboard</a>
            <button class="btn" onclick="showSignup()">Get Started</button>
        </div>
    </nav>

    <div class="container">
        <div class="hero">
            <h1>Professional Venue Data Platform</h1>
            <p>Transform venue URLs into actionable geographic data with parking polygons, coordinates, and comprehensive analytics</p>
            <button class="btn" onclick="showDemo()" style="font-size: 1.125rem; padding: 1rem 2rem;">Start Free Trial</button>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-number" id="totalVenues">0</div>
                <div class="stat-label">Venues Processed</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="totalUsers">0</div>
                <div class="stat-label">Active Users</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="totalRequests">0</div>
                <div class="stat-label">API Requests</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">99.9%</div>
                <div class="stat-label">Uptime</div>
            </div>
        </div>

        <div id="pricing" class="section">
            <h2>💰 Pricing Plans</h2>
            <div class="pricing-grid">
                <div class="pricing-card">
                    <h3>Free</h3>
                    <div class="price">$0<span class="price-period">/month</span></div>
                    <ul class="feature-list">
                        <li>50 venues per month</li>
                        <li>Basic geocoding</li>
                        <li>CSV export</li>
                        <li>Email support</li>
                    </ul>
                    <button class="btn" onclick="selectPlan('free')">Start Free</button>
                </div>
                <div class="pricing-card featured">
                    <h3>Pro</h3>
                    <div class="price">$49<span class="price-period">/month</span></div>
                    <ul class="feature-list">
                        <li>500 venues per month</li>
                        <li>Advanced geocoding</li>
                        <li>All export formats</li>
                        <li>API access</li>
                        <li>Live event detection</li>
                        <li>Priority support</li>
                    </ul>
                    <button class="btn" onclick="selectPlan('pro')">Start Pro Trial</button>
                </div>
                <div class="pricing-card">
                    <h3>Enterprise</h3>
                    <div class="price">$199<span class="price-period">/month</span></div>
                    <ul class="feature-list">
                        <li>5000+ venues per month</li>
                        <li>Unlimited API requests</li>
                        <li>Custom integrations</li>
                        <li>Dedicated support</li>
                        <li>SLA guarantee</li>
                    </ul>
                    <button class="btn" onclick="selectPlan('enterprise')">Contact Sales</button>
                </div>
            </div>
        </div>

        <div id="dashboard" class="section">
            <h2>🎛️ Dashboard</h2>
            <div class="alert alert-info" id="status">
                <span class="status-indicator status-online"></span>
                System Status: <span id="systemStatus">Checking...</span>
            </div>
            
            <div class="form-grid">
                <button class="btn" onclick="testSystem()">🧪 Test API Connection</button>
                <button class="btn btn-success" onclick="showVenueUpload()">📤 Upload Venues</button>
                <button class="btn btn-warning" onclick="showArtistPortal()">🎤 Artist Portal</button>
                <button class="btn" onclick="generateAPIKey()">🔑 Generate API Key</button>
            </div>

            <div id="venueUpload" class="hidden api-section">
                <h3>📤 Bulk Venue Upload</h3>
                <p>Upload CSV or manually enter venue data:</p>
                <div class="form-grid">
                    <input type="file" id="csvFile" class="form-control" accept=".csv">
                    <button class="btn" onclick="processCsvFile()">Process CSV</button>
                </div>
                <div class="form-grid">
                    <input type="text" id="venueName" class="form-control" placeholder="Venue Name">
                    <input type="text" id="venueCity" class="form-control" placeholder="City, State">
                    <input type="date" id="venueDate" class="form-control">
                    <button class="btn btn-success" onclick="addSingleVenue()">Add Venue</button>
                </div>
                <div id="venueList" class="hidden"></div>
                <button class="btn btn-success" onclick="processAllVenues()" id="processBtn" class="hidden">🚀 Process All Venues</button>
            </div>

            <div id="results" class="hidden"></div>
            <div id="downloadSection" class="hidden">
                <h3>📥 Download Results</h3>
                <div class="form-grid">
                    <button class="btn btn-success" onclick="downloadGeojson()">🗺️ GeoJSON</button>
                    <button class="btn btn-success" onclick="downloadCSV()">📊 CSV</button>
                    <button class="btn btn-success" onclick="downloadJSON()">📋 JSON</button>
                </div>
            </div>

            <div class="progress-bar hidden" id="progress">
                <div class="progress-fill" id="progressFill"></div>
            </div>
        </div>

        <div id="api" class="section">
            <h2>🔌 API Documentation</h2>
            <div class="api-section">
                <h3>Authentication</h3>
                <p>Include your API key in the header:</p>
                <div class="code-block">
                    <pre>curl -H "X-API-Key: your_api_key_here" \\
     -H "Content-Type: application/json" \\
     -d '{"venues": [{"name": "Madison Square Garden", "city": "New York, NY"}]}' \\
     https://your-domain.com/api/process-venues</pre>
                </div>
            </div>
            
            <div class="api-section">
                <h3>Endpoints</h3>
                <ul class="feature-list">
                    <li><strong>POST /api/process-venues</strong> - Process venue data</li>
                    <li><strong>GET /api/user-stats</strong> - Get usage statistics</li>
                    <li><strong>POST /api/live-events</strong> - Find live events</li>
                    <li><strong>GET /api/export/{format}</strong> - Export data</li>
                </ul>
            </div>
        </div>
    </div>

    <script>
        let venueQueue = [];
        let processedData = null;
        let currentUser = null;

        async function testSystem() {
            updateStatus('🔍 Testing system connection...', 'info');
            
            try {
                const response = await fetch('/api/system-status');
                const data = await response.json();
                
                if (data.success) {
                    updateStatus('✅ System operational', 'success');
                    document.getElementById('systemStatus').textContent = 'Online';
                } else {
                    updateStatus('❌ System error: ' + data.error, 'error');
                    document.getElementById('systemStatus').textContent = 'Error';
                }
            } catch (error) {
                updateStatus('❌ Connection failed: ' + error.message, 'error');
                document.getElementById('systemStatus').textContent = 'Offline';
            }
        }

        function updateStatus(message, type) {
            const status = document.getElementById('status');
            status.className = `alert alert-${type}`;
            status.innerHTML = `<span class="status-indicator status-${type === 'success' ? 'online' : 'offline'}"></span>${message}`;
        }

        function updateProgress(percentage) {
            const progress = document.getElementById('progress');
            const fill = document.getElementById('progressFill');
            progress.classList.remove('hidden');
            fill.style.width = percentage + '%';
        }

        function showDemo() {
            document.getElementById('dashboard').scrollIntoView({ behavior: 'smooth' });
            updateStatus('👋 Welcome! Test the platform with the buttons below.', 'info');
        }

        function showSignup() {
            const email = prompt('Enter your email to get started:');
            const name = prompt('Enter your name:');
            
            if (email && name) {
                signupUser(email, name, 'free');
            }
        }

        async function signupUser(email, name, plan) {
            try {
                const response = await fetch('/api/signup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, name, plan })
                });
                
                const data = await response.json();
                if (data.success) {
                    currentUser = data.user;
                    updateStatus(`🎉 Welcome ${name}! Your ${plan} account is ready.`, 'success');
                    loadStats();
                } else {
                    updateStatus('❌ Signup failed: ' + data.error, 'error');
                }
            } catch (error) {
                updateStatus('❌ Signup error: ' + error.message, 'error');
            }
        }

        function selectPlan(plan) {
            const email = prompt('Enter your email:');
            const name = prompt('Enter your name:');
            
            if (email && name) {
                signupUser(email, name, plan);
            }
        }

        function showVenueUpload() {
            document.getElementById('venueUpload').classList.remove('hidden');
        }

        function addSingleVenue() {
            const name = document.getElementById('venueName').value.trim();
            const city = document.getElementById('venueCity').value.trim();
            const date = document.getElementById('venueDate').value;
            
            if (!name || !city) {
                alert('Please enter venue name and city');
                return;
            }
            
            venueQueue.push({ name, city, date: date || new Date().toISOString().slice(0, 10) });
            updateVenueList();
            
            // Clear form
            document.getElementById('venueName').value = '';
            document.getElementById('venueCity').value = '';
            document.getElementById('venueDate').value = '';
        }

        function updateVenueList() {
            const container = document.getElementById('venueList');
            if (venueQueue.length === 0) {
                container.classList.add('hidden');
                document.getElementById('processBtn').classList.add('hidden');
                return;
            }
            
            container.classList.remove('hidden');
            document.getElementById('processBtn').classList.remove('hidden');
            
            container.innerHTML = '<h4>📋 Venues to Process:</h4>' + 
                venueQueue.map((venue, index) => `
                    <div class="venue-item">
                        <strong>${venue.name}</strong> - ${venue.city} (${venue.date})
                        <button class="btn btn-danger" onclick="removeVenue(${index})" style="float: right; padding: 0.25rem 0.5rem;">Remove</button>
                    </div>
                `).join('');
        }

        function removeVenue(index) {
            venueQueue.splice(index, 1);
            updateVenueList();
        }

        async function processAllVenues() {
            if (venueQueue.length === 0) {
                alert('No venues to process');
                return;
            }

            updateStatus(`🚀 Processing ${venueQueue.length} venues...`, 'info');
            updateProgress(20);

            try {
                const response = await fetch('/api/process-venues', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        venues: venueQueue,
                        user_id: currentUser?.id 
                    })
                });

                updateProgress(80);
                const data = await response.json();
                updateProgress(100);

                if (data.success) {
                    processedData = data;
                    updateStatus(`🎉 Success! Processed ${data.venues.length} venues with ${data.total_polygons} polygons.`, 'success');
                    displayResults(data.venues);
                    document.getElementById('downloadSection').classList.remove('hidden');
                    venueQueue = [];
                    updateVenueList();
                    loadStats();
                } else {
                    updateStatus('❌ Processing failed: ' + data.error, 'error');
                }
            } catch (error) {
                updateStatus('❌ Processing error: ' + error.message, 'error');
            }
        }

        function displayResults(venues) {
            const results = document.getElementById('results');
            results.innerHTML = '<h3>📍 Processed Venues:</h3>';
            
            venues.forEach(venue => {
                const item = document.createElement('div');
                item.className = 'venue-item';
                item.innerHTML = `
                    <strong>${venue.name}</strong><br>
                    📍 ${venue.address}<br>
                    🗺️ (${venue.latitude.toFixed(6)}, ${venue.longitude.toFixed(6)})<br>
                    🅿️ ${venue.parking_count} parking areas<br>
                    📅 ${venue.date}
                `;
                results.appendChild(item);
            });
            
            results.classList.remove('hidden');
        }

        async function loadStats() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();
                
                if (data.success) {
                    document.getElementById('totalVenues').textContent = data.stats.total_venues;
                    document.getElementById('totalUsers').textContent = data.stats.total_users;
                    document.getElementById('totalRequests').textContent = data.stats.total_requests;
                }
            } catch (error) {
                console.error('Failed to load stats:', error);
            }
        }

        function downloadGeojson() {
            if (!processedData) return alert('No data to download');
            
            const blob = new Blob([JSON.stringify(processedData.geojson, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `venues_${new Date().toISOString().slice(0,10)}.geojson`;
            a.click();
            URL.revokeObjectURL(url);
        }

        function downloadCSV() {
            if (!processedData) return alert('No data to download');
            
            const blob = new Blob([processedData.csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `venues_${new Date().toISOString().slice(0,10)}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        }

        function downloadJSON() {
            if (!processedData) return alert('No data to download');
            
            const blob = new Blob([JSON.stringify(processedData, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `venues_${new Date().toISOString().slice(0,10)}.json`;
            a.click();
            URL.revokeObjectURL(url);
        }

        function generateAPIKey() {
            if (!currentUser) {
                alert('Please sign up first');
                return;
            }
            
            updateStatus(`🔑 Your API Key: ${currentUser.api_key}`, 'success');
        }

        function showArtistPortal() {
            window.open(window.location.origin + '/artist-portal', '_blank');
        }

        // Load stats on page load
        window.addEventListener('load', () => {
            setTimeout(() => {
                testSystem();
                loadStats();
            }, 1000);
        });
    </script>
</body>
</html>
'''

# API Routes
@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/artist-portal')
def artist_portal():
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Artist Portal - VenueMapper Pro</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 2rem; }
            .container { max-width: 800px; margin: 0 auto; background: white; border-radius: 1rem; box-shadow: 0 20px 40px rgba(0,0,0,0.1); overflow: hidden; }
            .header { background: #1e293b; color: white; padding: 2rem; text-align: center; }
            .form-section { padding: 2rem; }
            .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0; }
            .form-control { padding: 0.75rem; border: 1px solid #d1d5db; border-radius: 0.5rem; font-size: 1rem; }
            .btn { background: #3b82f6; color: white; border: none; padding: 0.75rem 1.5rem; border-radius: 0.5rem; font-weight: 600; cursor: pointer; }
            .btn:hover { background: #2563eb; }
            .venue-item { background: #f8fafc; padding: 1rem; margin: 0.5rem 0; border-radius: 0.5rem; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎤 Artist Tour Submission</h1>
                <p>Submit your tour venues for professional mapping</p>
            </div>
            <div class="form-section">
                <h3>Artist Information</h3>
                <div class="form-grid">
                    <input type="text" id="artistName" class="form-control" placeholder="Artist/Band Name" required>
                    <input type="email" id="artistEmail" class="form-control" placeholder="Contact Email" required>
                </div>
                
                <h3 style="margin-top: 2rem;">Tour Venues</h3>
                <div class="form-grid">
                    <input type="text" id="venueName" class="form-control" placeholder="Venue Name">
                    <input type="text" id="venueCity" class="form-control" placeholder="City, State">
                </div>
                <div class="form-grid">
                    <input type="datetime-local" id="showDate" class="form-control">
                    <button class="btn" onclick="addVenue()">Add Venue</button>
                </div>
                
                <div id="venueList"></div>
                
                <div style="text-align: center; margin-top: 2rem;">
                    <button class="btn" onclick="submitTour()" style="font-size: 1.125rem; padding: 1rem 2rem;">🚀 Submit Tour</button>
                </div>
            </div>
        </div>

        <script>
            let tourVenues = [];

            function addVenue() {
                const name = document.getElementById('venueName').value.trim();
                const city = document.getElementById('venueCity').value.trim();
                const date = document.getElementById('showDate').value;

                if (!name || !city || !date) {
                    alert('Please fill all venue fields');
                    return;
                }

                tourVenues.push({ name, city, date });
                updateVenueDisplay();
                
                // Clear form
                document.getElementById('venueName').value = '';
                document.getElementById('venueCity').value = '';
                document.getElementById('showDate').value = '';
            }

            function updateVenueDisplay() {
                const container = document.getElementById('venueList');
                container.innerHTML = tourVenues.map((venue, index) => `
                    <div class="venue-item">
                        <strong>${venue.name}</strong> - ${venue.city}<br>
                        📅 ${new Date(venue.date).toLocaleDateString()} at ${new Date(venue.date).toLocaleTimeString()}
                        <button onclick="removeVenue(${index})" style="float: right;">Remove</button>
                    </div>
                `).join('');
            }

            function removeVenue(index) {
                tourVenues.splice(index, 1);
                updateVenueDisplay();
            }

            async function submitTour() {
                const artistName = document.getElementById('artistName').value.trim();
                const artistEmail = document.getElementById('artistEmail').value.trim();

                if (!artistName || !artistEmail) {
                    alert('Please enter artist name and email');
                    return;
                }

                if (tourVenues.length === 0) {
                    alert('Please add at least one venue');
                    return;
                }

                try {
                    const response = await fetch('/api/artist-submission', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            artist: { name: artistName, email: artistEmail },
                            venues: tourVenues
                        })
                    });

                    const data = await response.json();
                    if (data.success) {
                        alert(`Thank you ${artistName}! Your tour has been submitted successfully.`);
                        // Reset form
                        document.getElementById('artistName').value = '';
                        document.getElementById('artistEmail').value = '';
                        tourVenues = [];
                        updateVenueDisplay();
                    } else {
                        alert('Submission failed: ' + data.error);
                    }
                } catch (error) {
                    alert('Error: ' + error.message);
                }
            }
        </script>
    </body>
    </html>
    ''')

@app.route('/api/signup', methods=['POST'])
def signup():
    try:
        data = request.get_json()
        email = data.get('email')
        name = data.get('name') 
        plan = data.get('plan', 'free')
        
        if not email or not name:
            return jsonify({'success': False, 'error': 'Email and name required'})
        
        # Check if user exists
        for user in users_db.values():
            if user.email == email:
                return jsonify({'success': False, 'error': 'Email already registered'})
        
        mapper = VenueMapperPro(os.environ.get('GOOGLE_MAPS_API_KEY', ''))
        user = mapper.create_user(email, name, plan)
        
        return jsonify({
            'success': True,
            'user': {
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'plan': user.plan,
                'api_key': user.api_key
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/system-status', methods=['GET'])
def system_status():
    try:
        api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
        if not api_key:
            return jsonify({'success': False, 'error': 'API key not configured'})
        
        mapper = VenueMapperPro(api_key)
        if mapper.google_maps.test_api_key():
            return jsonify({'success': True, 'status': 'operational'})
        else:
            return jsonify({'success': False, 'error': 'API key validation failed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    return jsonify({'success': True, 'stats': usage_stats})

@app.route('/api/process-venues', methods=['POST'])
def process_venues():
    try:
        api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
        if not api_key:
            return jsonify({'success': False, 'error': 'API key not configured'})
        
        data = request.get_json()
        venues_data = data.get('venues', [])
        user_id = data.get('user_id', 'anonymous')
        
        if not venues_data:
            return jsonify({'success': False, 'error': 'No venues provided'})
        
        mapper = VenueMapperPro(api_key)
        
        # Create anonymous user if needed
        if user_id == 'anonymous':
            user = mapper.create_user('anonymous@example.com', 'Anonymous User', 'free')
            user_id = user.id
        
        venues = mapper.process_venues_for_user(user_id, venues_data)
        
        if not venues:
            return jsonify({'success': False, 'error': 'No venues could be processed'})
        
        # Generate outputs
        geojson_data = {
            "type": "FeatureCollection",
            "features": [],
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "venue_count": len(venues),
                "user_id": user_id
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
        
        # Generate CSV
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

@app.route('/api/artist-submission', methods=['POST'])
def artist_submission():
    try:
        data = request.get_json()
        artist = data.get('artist', {})
        venues = data.get('venues', [])
        
        if not artist.get('name') or not artist.get('email'):
            return jsonify({'success': False, 'error': 'Artist name and email required'})
        
        if not venues:
            return jsonify({'success': False, 'error': 'At least one venue required'})
        
        submission_id = str(uuid.uuid4())
        artist_submissions[submission_id] = {
            'id': submission_id,
            'artist': artist,
            'venues': venues,
            'submitted_at': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        logger.info(f"Artist submission from {artist['name']} with {len(venues)} venues")
        
        return jsonify({
            'success': True,
            'submission_id': submission_id,
            'venue_count': len(venues)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
