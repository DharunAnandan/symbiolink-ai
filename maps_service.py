"""
Google Maps integration service for accurate distance calculations.

Replaces the simple Euclidean distance calculations with real-world
driving distances using Google Maps Distance Matrix API.
"""

import os
import requests
from config import Config

class MapsService:
    def __init__(self):
        self.api_key = getattr(Config, 'GOOGLE_MAPS_API_KEY', None)
        self.base_url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        self.api_available = bool(self.api_key and self.api_key != 'your_google_maps_api_key')

        if not self.api_available:
            print("Info: Google Maps API key not configured. Using Euclidean distance fallback.")
    
    def get_distance(self, origin, destination, mode='driving'):
        """
        Get distance between two points using Google Maps API.

        Args:
            origin: Tuple (lat, lng) or address string
            destination: Tuple (lat, lng) or address string
            mode: 'driving', 'walking', 'bicycling', 'transit'

        Returns:
            dict: {
                'distance_km': float,
                'duration_minutes': float,
                'status': str
            }
        """
        if not self.api_available:
            # Fallback to Euclidean distance
            return self._euclidean_distance(origin, destination)
        
        try:
            # Convert coordinates to strings if needed
            if isinstance(origin, (tuple, list)):
                origin = f"{origin[0]},{origin[1]}"
            if isinstance(destination, (tuple, list)):
                destination = f"{destination[0]},{destination[1]}"
            
            params = {
                'origins': origin,
                'destinations': destination,
                'mode': mode,
                'key': self.api_key
            }
            
            response = requests.get(self.base_url, params=params)
            data = response.json()
            
            if data['status'] == 'OK':
                element = data['rows'][0]['elements'][0]
                if element['status'] == 'OK':
                    distance_m = element['distance']['value']
                    duration_s = element['duration']['value']
                    
                    return {
                        'distance_km': distance_m / 1000,
                        'duration_minutes': duration_s / 60,
                        'status': 'OK'
                    }
                else:
                    print(f"Google Maps error: {element['status']}")
                    return self._euclidean_distance(origin, destination)
            else:
                print(f"Google Maps API error: {data['status']}")
                return self._euclidean_distance(origin, destination)
                
        except Exception as e:
            print(f"Error calling Google Maps API: {e}")
            return self._euclidean_distance(origin, destination)
    
    def _euclidean_distance(self, origin, destination):
        """Fallback to Euclidean distance if Google Maps fails."""
        try:
            if isinstance(origin, str):
                # If addresses were provided, use a simple estimate
                return {
                    'distance_km': 5.0,  # Default estimate
                    'duration_minutes': 10.0,
                    'status': 'FALLBACK'
                }
            
            # Calculate Euclidean distance for coordinates
            import math
            (x1, y1), (x2, y2) = origin, destination
            distance = math.hypot(x2 - x1, y2 - y1)
            
            return {
                'distance_km': distance,
                'duration_minutes': distance * 2,  # Rough estimate
                'status': 'FALLBACK'
            }
        except:
            return {
                'distance_km': 5.0,
                'duration_minutes': 10.0,
                'status': 'ERROR'
            }
    
    def get_distance_matrix(self, origins, destinations, mode='driving'):
        """
        Get distance matrix for multiple origins and destinations.
        Useful for optimizing logistics and pooling calculations.

        Args:
            origins: List of (lat, lng) tuples or addresses
            destinations: List of (lat, lng) tuples or addresses
            mode: Travel mode

        Returns:
            2D array of distances in km
        """
        if not self.api_available:
            # Fallback to pairwise Euclidean distances
            return self._euclidean_distance_matrix(origins, destinations)
        
        try:
            # Convert to comma-separated strings
            origin_str = '|'.join([
                f"{o[0]},{o[1]}" if isinstance(o, (tuple, list)) else o 
                for o in origins
            ])
            dest_str = '|'.join([
                f"{d[0]},{d[1]}" if isinstance(d, (tuple, list)) else d 
                for d in destinations
            ])
            
            params = {
                'origins': origin_str,
                'destinations': dest_str,
                'mode': mode,
                'key': self.api_key
            }
            
            response = requests.get(self.base_url, params=params)
            data = response.json()
            
            if data['status'] == 'OK':
                matrix = []
                for row in data['rows']:
                    row_distances = []
                    for element in row['elements']:
                        if element['status'] == 'OK':
                            row_distances.append(element['distance']['value'] / 1000)
                        else:
                            row_distances.append(float('inf'))
                    matrix.append(row_distances)
                return matrix
            else:
                return self._euclidean_distance_matrix(origins, destinations)
                
        except Exception as e:
            print(f"Error getting distance matrix: {e}")
            return self._euclidean_distance_matrix(origins, destinations)
    
    def _euclidean_distance_matrix(self, origins, destinations):
        """Fallback to Euclidean distance matrix."""
        import math
        matrix = []
        for origin in origins:
            if isinstance(origin, str):
                row = [5.0] * len(destinations)  # Default estimate
            else:
                row = []
                for dest in destinations:
                    if isinstance(dest, str):
                        row.append(5.0)
                    else:
                        distance = math.hypot(dest[0] - origin[0], dest[1] - origin[1])
                        row.append(distance)
            matrix.append(row)
        return matrix
    
    def geocode_address(self, address):
        """
        Convert address to coordinates using Google Maps Geocoding API.

        Args:
            address: String address

        Returns:
            dict: {'lat': float, 'lng': float, 'formatted_address': str}
        """
        if not self.api_available:
            return None
        
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                'address': address,
                'key': self.api_key
            }
            
            response = requests.get(url, params=params)
            data = response.json()
            
            if data['status'] == 'OK' and data['results']:
                location = data['results'][0]['geometry']['location']
                return {
                    'lat': location['lat'],
                    'lng': location['lng'],
                    'formatted_address': data['results'][0]['formatted_address']
                }
            else:
                return None
                
        except Exception as e:
            print(f"Error geocoding address: {e}")
            return None
    
    def reverse_geocode(self, lat, lng):
        """
        Convert coordinates to address using Google Maps Reverse Geocoding API.

        Args:
            lat: Latitude
            lng: Longitude

        Returns:
            str: Formatted address
        """
        if not self.api_available:
            return f"({lat}, {lng})"
        
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                'latlng': f"{lat},{lng}",
                'key': self.api_key
            }
            
            response = requests.get(url, params=params)
            data = response.json()
            
            if data['status'] == 'OK' and data['results']:
                return data['results'][0]['formatted_address']
            else:
                return f"({lat}, {lng})"
                
        except Exception as e:
            print(f"Error reverse geocoding: {e}")
            return f"({lat}, {lng})"

# Create global service instance
maps_service = MapsService()