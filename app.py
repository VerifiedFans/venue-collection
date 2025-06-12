import os
import json
import csv
import io
import zipfile
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, send_file
import googlemaps
import requests
from bs4 import BeautifulSoup

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Google Maps API configuration
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')
gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY) if GOOGLE_MAPS_API_KEY else None

# HTML Dashboard Template
DASHBOARD_HTML = r'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎵 Concert Venue Data Collection</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            margin: 0;
            padding: 20px;
            min-height: 100vh;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
            border-bottom: 2px solid #667eea;
            padding-bottom: 20px;
        }
        .header h1 {
            color: #333;
            margin: 0;
            font-size: 2.5em;
        }
        .subtitle {
            color: #666;
            margin: 10px 0;
            font-size: 1.2em;
        }
        .status-section {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 20px;
            margin: 20px 0;
            border-left: 5px solid #667eea;
        }
        .controls {
            display: flex;
            gap: 15px;
            margin: 20px 0;
            flex-wrap: wrap;
        }
        .btn {
            background: linear-gradient(45deg, #667eea, #764ba2);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 16px;
            transition: all 0.3s ease;
            text-decoration: none;
            display: inline-block;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        .status-indicator {
            display: inline-block;
            padding: 8px 16px;
            border-radius: 20px;
            font-weight: bold;
            margin: 10px 0;
        }
        .status-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .status-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }
        .info-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .form-section {
            background: white;
            border-radius: 15px;
            padding: 25px;
            margin: 20px 0;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .form-group {
            margin: 15px 0;
        }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: #333;
        }
        .form-group input {
            width: 100%;
            padding: 10px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s ease;
        }
        .form-group input:focus {
            outline: none;
            border-color: #667eea;
        }
        #result {
            margin-top: 20px;
            padding: 15px;
            border-radius: 8px;
            display: none;
        }
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-right: 10px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎵 Concert Venue Data Collection</h1>
            <p class="subtitle">Production System - Live on Railway</p>
        </div>

        <div class="status-section">
            <h2>🚀 System Status & Controls</h2>
            <div class="controls">
                <button class="btn" onclick="testAPI()">🧪 Test Google Maps API</button>
                <button class="btn" onclick="generateSample()">🎯 Generate Sample Venues</button>
            </div>
            <div id="api-status">
                <span class="status-indicator status-error" id="status-text">
                    {{ status_message }}
                </span>
            </div>
        </div>

        <div class="info-grid">
            <div class="info-card">
                <h3>ℹ️ System Information</h3>
                <p><strong>Platform:</strong> Railway.app (Cloud Hosted)</p>
                <p><strong>APIs:</strong> Google Maps Geocoding + Places</p>
                <p><strong>Outputs:</strong> GeoJSON, CSV, JSON formats</p>
                <p><strong>Status:</strong> {{ system_status }}</p>
            </div>
        </div>

        <div class="form-section">
            <h3>🎤 Artist Venue Collection</h3>
            <form id="venue-form">
                <div class="form-group">
                    <label for="artist-url">Bandsintown Artist URL:</label>
                    <input type="url" id="artist-url" name="artist_url" 
                           placeholder="https://www.bandsintown.com/a/artist-name" required>
                </div>
                <button type="submit" class="btn">🔍 Collect Venue Data</button>
            </form>
        </div>

        <div class="form-section">
            <h3>📍 Manual Venue Lookup</h3>
            <form id="manual-venue-form">
                <div class="form-group">
                    <label for="venue-name">Venue Name:</label>
                    <input type="text" id="venue-name" name="venue_name" 
                           placeholder="Madison Square Garden" required>
                </div>
                <div class="form-group">
                    <label for="venue-city">City:</label>
                    <input type="text" id="venue-city" name="venue_city" 
                           placeholder="New York, NY" required>
                </div>
                <button type="submit" class="btn">📍 Find Venue</button>
            </form>
        </div>

        <div id="result"></div>
    </div>

    <script>
        // Test Google Maps API
        async function testAPI() {
            const statusElement = document.getElementById('status-text');
            statusElement.innerHTML = '<div class="loading"></div>Testing API...';
            statusElement.className = 'status-indicator';
            
            try {
                const response = await fetch('/test-api', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    statusElement.textContent = '✅ System operational! Google Maps API is working perfectly.';
                    statusElement.className = 'status-indicator status-success';
                } else {
                    statusElement.textContent = '❌ API Test Failed: ' + data.error;
                    statusElement.className = 'status-indicator status-error';
                }
            } catch (error) {
                statusElement.textContent = '❌ API Test Failed: Connection error';
                statusElement.className = 'status-indicator status-error';
            }
        }

        // Generate sample venues
        async function generateSample() {
            const resultDiv = document.getElementById('result');
            resultDiv.style.display = 'block';
            resultDiv.innerHTML = '<div class="loading"></div>Generating sample venues...';
            resultDiv.className = 'status-indicator';
            
            try {
                const response = await fetch('/generate-sample', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    resultDiv.innerHTML = '<h4>✅ Sample venues generated successfully!</h4>' +
                                        '<pre>' + JSON.stringify(data.venues, null, 2) + '</pre>';
                    resultDiv.className = 'status-indicator status-success';
                } else {
                    resultDiv.innerHTML = '❌ Failed to generate sample venues: ' + data.error;
                    resultDiv.className = 'status-indicator status-error';
                }
            } catch (error) {
                resultDiv.innerHTML = '❌ Error generating sample venues';
                resultDiv.className = 'status-indicator status-error';
            }
        }

        // Handle venue form submission
        document.getElementById('venue-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData(e.target);
            const resultDiv = document.getElementById('result');
            
            resultDiv.style.display = 'block';
            resultDiv.innerHTML = '<div class="loading"></div>Collecting venue data...';
            resultDiv.className = 'status-indicator';
            
            try {
                const response = await fetch('/collect-venues', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                
                if (data.success) {
                    resultDiv.innerHTML = '<h4>✅ Venue data collected successfully!</h4>' +
                                        '<p>Found ' + data.venue_count + ' venues</p>' +
                                        '<a href="/download/' + data.download_id + '" class="btn">📥 Download GeoJSON</a>';
                    resultDiv.className = 'status-indicator status-success';
                } else {
                    resultDiv.innerHTML = '❌ Failed to collect venues: ' + data.error;
                    resultDiv.className = 'status-indicator status-error';
                }
            } catch (error) {
                resultDiv.innerHTML = '❌ Error collecting venue data';
                resultDiv.className = 'status-indicator status-error';
            }
        });

        // Handle manual venue form submission
        document.getElementById('manual-venue-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData(e.target);
            const resultDiv = document.getElementById('result');
            
            resultDiv.style.display = 'block';
            resultDiv.innerHTML = '<div class="loading"></div>Looking up venue...';
            resultDiv.className = 'status-indicator';
            
            try {
                const response = await fetch('/lookup-venue', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                
                if (data.success) {
                    resultDiv.innerHTML = '<h4>✅ Venue found!</h4>' +
                                        '<p><strong>Name:</strong> ' + data.venue.name + '</p>' +
                                        '<p><strong>Address:</strong> ' + data.venue.address + '</p>' +
                                        '<p><strong>Coordinates:</strong> ' + data.venue.lat + ', ' + data.venue.lng + '</p>';
                    resultDiv.className = 'status-indicator status-success';
                } else {
                    resultDiv.innerHTML = '❌ Venue not found: ' + data.error;
                    resultDiv.className = 'status-indicator status-error';
                }
            } catch (error) {
                resultDiv.innerHTML = '❌ Error looking up venue';
                resultDiv.className = 'status-indicator status-error';
            }
        });

        // Test API on page load
        window.onload = function() {
            testAPI();
        };
    </script>
</body>
</html>
'''

# Routes
@app.route('/')
def dashboard():
    """Main dashboard route"""
    api_configured = GOOGLE_MAPS_API_KEY is not None
    if api_configured:
        status_message = "❌ API Test Failed: Google Maps API key not configured"
        system_status = "Error ❌"
    else:
        status_message = "❌ API Test Failed: Google Maps API key not configured"
        system_status = "Error ❌"
    
    return render_template_string(DASHBOARD_HTML, 
                                status_message=status_message,
                                system_status=system_status)

@app.route('/health')
def health_check():
    """Health check endpoint for Railway"""
    return {'status': 'healthy'}, 200

@app.route('/test-api', methods=['POST'])
def test_api():
    """Test Google Maps API connectivity"""
    try:
        if not GOOGLE_MAPS_API_KEY:
            return jsonify({
                'success': False,
                'error': 'Google Maps API key not configured'
            })
        
        if not gmaps:
            return jsonify({
                'success': False,
                'error': 'Google Maps client not initialized'
            })
        
        # Test geocoding with a simple query
        test_result = gmaps.geocode('Madison Square Garden, New York')
        
        if test_result:
            return jsonify({
                'success': True,
                'message': 'Google Maps API is working correctly',
                'test_location': test_result[0]['formatted_address']
            })
        else:
            return jsonify({
                'success': False,
                'error': 'API test failed - no results returned'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'API test failed: {str(e)}'
        })

@app.route('/generate-sample', methods=['POST'])
def generate_sample():
    """Generate sample venue data"""
    try:
        if not gmaps:
            return jsonify({
                'success': False,
                'error': 'Google Maps API not configured'
            })
        
        # Sample venues for testing
        sample_venues = [
            {'name': 'Madison Square Garden', 'city': 'New York, NY'},
            {'name': 'Red Rocks Amphitheatre', 'city': 'Morrison, CO'},
            {'name': 'The Fillmore', 'city': 'San Francisco, CA'}
        ]
        
        venues_with_data = []
        
        for venue in sample_venues:
            try:
                # Geocode the venue
                geocode_result = gmaps.geocode(f"{venue['name']}, {venue['city']}")
                
                if geocode_result:
                    location = geocode_result[0]
                    venue_data = {
                        'name': venue['name'],
                        'address': location['formatted_address'],
                        'lat': location['geometry']['location']['lat'],
                        'lng': location['geometry']['location']['lng'],
                        'place_id': location['place_id']
                    }
                    venues_with_data.append(venue_data)
                    
            except Exception as e:
                print(f"Error geocoding {venue['name']}: {str(e)}")
                continue
        
        return jsonify({
            'success': True,
            'venues': venues_with_data,
            'count': len(venues_with_data)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to generate sample venues: {str(e)}'
        })

@app.route('/lookup-venue', methods=['POST'])
def lookup_venue():
    """Look up a specific venue"""
    try:
        if not gmaps:
            return jsonify({
                'success': False,
                'error': 'Google Maps API not configured'
            })
        
        venue_name = request.form.get('venue_name')
        venue_city = request.form.get('venue_city')
        
        if not venue_name or not venue_city:
            return jsonify({
                'success': False,
                'error': 'Both venue name and city are required'
            })
        
        # Search for the venue
        search_query = f"{venue_name}, {venue_city}"
        geocode_result = gmaps.geocode(search_query)
        
        if not geocode_result:
            return jsonify({
                'success': False,
                'error': 'Venue not found'
            })
        
        location = geocode_result[0]
        venue_data = {
            'name': venue_name,
            'address': location['formatted_address'],
            'lat': location['geometry']['location']['lat'],
            'lng': location['geometry']['location']['lng'],
            'place_id': location['place_id']
        }
        
        return jsonify({
            'success': True,
            'venue': venue_data
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Venue lookup failed: {str(e)}'
        })

@app.route('/collect-venues', methods=['POST'])
def collect_venues():
    """Collect venues from Bandsintown (placeholder for future implementation)"""
    try:
        artist_url = request.form.get('artist_url')
        
        if not artist_url:
            return jsonify({
                'success': False,
                'error': 'Artist URL is required'
            })
        
        # Placeholder for Bandsintown scraping
        # This would be implemented with selenium and BeautifulSoup
        return jsonify({
            'success': False,
            'error': 'Bandsintown scraping not yet implemented. Use manual venue lookup for now.'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Venue collection failed: {str(e)}'
        })

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# Run the application
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
