"""
Razorpay payment integration service for SymbioLink AI.

Handles payment processing, order payments, refunds, and payment verification.
Integrates with Razorpay API for secure payment processing.
"""

import os
import hmac
import hashlib
import json
import requests
from config import Config

class PaymentService:
    # .env.example ships these two as literal placeholder text, not blanks --
    # so an un-edited .env has a *non-empty* RAZORPAY_KEY_ID/SECRET that a
    # naive truthiness check would treat as "configured". That mismatch is
    # exactly what broke checkout: create_order() below would try the real
    # Razorpay API with these fake creds, get a 401, and quietly fall back to
    # a simulated order -- but self.key_id (still the placeholder string,
    # still truthy) got handed to the frontend anyway, which then opened the
    # *real* Razorpay Checkout widget with a fake key and a fake order id,
    # guaranteeing "Payment Failed". Same placeholder guard maps_service.py
    # already uses for GOOGLE_MAPS_API_KEY -- applied here too.
    _PLACEHOLDER_VALUES = {'your_razorpay_key_id', 'your_razorpay_key_secret', ''}

    def __init__(self):
        self.key_id = getattr(Config, 'RAZORPAY_KEY_ID', None)
        self.key_secret = getattr(Config, 'RAZORPAY_KEY_SECRET', None)
        self.base_url = "https://api.razorpay.com/v1"

        if self.key_id in self._PLACEHOLDER_VALUES:
            self.key_id = None
        if self.key_secret in self._PLACEHOLDER_VALUES:
            self.key_secret = None

        if not self.key_id or not self.key_secret:
            print("Warning: Razorpay credentials not configured. Payment service will be in simulation mode.")
    
    def create_order(self, amount_rs, currency="INR", receipt=None, notes=None):
        """
        Create a Razorpay order.
        
        Args:
            amount_rs: Amount in rupees (will be converted to paise)
            currency: Currency code (default: INR)
            receipt: Receipt ID for your reference
            notes: Additional notes as dict
        
        Returns:
            dict: Razorpay order details or simulated order
        """
        if not self.key_id or not self.key_secret:
            return self._create_simulated_order(amount_rs, currency, receipt)
        
        try:
            amount_paise = int(amount_rs * 100)  # Convert to paise
            
            payload = {
                "amount": amount_paise,
                "currency": currency,
                "receipt": receipt,
                "notes": notes or {},
                "payment_capture": 1  # Auto-capture payment
            }
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Basic {self._get_auth_header()}"
            }
            
            response = requests.post(
                f"{self.base_url}/orders",
                json=payload,
                headers=headers,
                timeout=8
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Razorpay error: {response.status_code} - {response.text}")
                return self._create_simulated_order(amount_rs, currency, receipt)
                
        except Exception as e:
            print(f"Error creating Razorpay order: {e}")
            return self._create_simulated_order(amount_rs, currency, receipt)
    
    def verify_payment(self, razorpay_order_id, razorpay_payment_id, razorpay_signature):
        """
        Verify payment signature to ensure payment authenticity.
        
        Args:
            razorpay_order_id: Order ID from Razorpay
            razorpay_payment_id: Payment ID from Razorpay
            razorpay_signature: Signature from Razorpay
        
        Returns:
            bool: True if signature is valid
        """
        if not self.key_secret:
            # In simulation mode, always return True
            return True
        
        try:
            generated_signature = hmac.new(
                self.key_secret.encode('utf-8'),
                f"{razorpay_order_id}|{razorpay_payment_id}".encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(generated_signature, razorpay_signature)
            
        except Exception as e:
            print(f"Error verifying payment signature: {e}")
            return False
    
    def get_payment_details(self, payment_id):
        """
        Fetch payment details from Razorpay.
        
        Args:
            payment_id: Razorpay payment ID
        
        Returns:
            dict: Payment details
        """
        if not self.key_id or not self.key_secret:
            return self._get_simulated_payment_details(payment_id)
        
        try:
            headers = {
                "Authorization": f"Basic {self._get_auth_header()}"
            }
            
            response = requests.get(
                f"{self.base_url}/payments/{payment_id}",
                headers=headers,
                timeout=8
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error fetching payment details: {response.status_code}")
                return self._get_simulated_payment_details(payment_id)
                
        except Exception as e:
            print(f"Error fetching payment details: {e}")
            return self._get_simulated_payment_details(payment_id)
    
    def refund_payment(self, payment_id, amount_rs=None):
        """
        Process a refund for a payment.
        
        Args:
            payment_id: Razorpay payment ID
            amount_rs: Amount to refund in rupees (None for full refund)
        
        Returns:
            dict: Refund details
        """
        if not self.key_id or not self.key_secret:
            return self._create_simulated_refund(payment_id, amount_rs)
        
        try:
            payload = {}
            if amount_rs:
                payload["amount"] = int(amount_rs * 100)  # Convert to paise
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Basic {self._get_auth_header()}"
            }
            
            response = requests.post(
                f"{self.base_url}/payments/{payment_id}/refund",
                json=payload,
                headers=headers,
                timeout=8
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error processing refund: {response.status_code}")
                return self._create_simulated_refund(payment_id, amount_rs)
                
        except Exception as e:
            print(f"Error processing refund: {e}")
            return self._create_simulated_refund(payment_id, amount_rs)
    
    def _get_auth_header(self):
        """Generate basic auth header for Razorpay API."""
        import base64
        auth_string = f"{self.key_id}:{self.key_secret}"
        return base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
    
    def _create_simulated_order(self, amount_rs, currency, receipt):
        """Create a simulated order for development/testing."""
        import uuid
        return {
            "id": f"order_sim_{uuid.uuid4().hex[:8]}",
            "entity": "order",
            "amount": int(amount_rs * 100),
            "currency": currency,
            "receipt": receipt,
            "status": "created",
            "notes": {},
            "created_at": int(__import__('time').time())
        }
    
    def _get_simulated_payment_details(self, payment_id):
        """Get simulated payment details for development/testing."""
        return {
            "id": payment_id,
            "entity": "payment",
            "amount": 50000,  # 500.00 in paise
            "currency": "INR",
            "status": "captured",
            "method": "card",
            "description": "Payment for SymbioLink order",
            "created_at": int(__import__('time').time())
        }
    
    def _create_simulated_refund(self, payment_id, amount_rs):
        """Create a simulated refund for development/testing."""
        import uuid
        return {
            "id": f"refund_sim_{uuid.uuid4().hex[:8]}",
            "entity": "refund",
            "amount": int(amount_rs * 100) if amount_rs else 50000,
            "currency": "INR",
            "payment_id": payment_id,
            "status": "processed",
            "created_at": int(__import__('time').time())
        }
    
    def calculate_order_amount(self, material, qty_kg, distance_km=None):
        """
        Calculate order amount based on material price and logistics.

        Args:
            material: Material key
            qty_kg: Quantity in kg
            distance_km: Distance for logistics calculation (optional)

        Returns:
            dict: {
                'material_cost': float,
                'logistics_cost': float,
                'total_amount': float,
                'breakdown': dict
            }
        """
        try:
            from data import PRICE_TABLE_RS_PER_KG, TRANSPORT_RS_PER_KM_SOLO
        except ImportError:
            # Fallback to default values if data module is not available
            PRICE_TABLE_RS_PER_KG = {material: {"byproduct": 15}}
            TRANSPORT_RS_PER_KM_SOLO = 45

        prices = PRICE_TABLE_RS_PER_KG.get(material, {"byproduct": 15})
        material_cost = prices["byproduct"] * qty_kg

        logistics_cost = 0
        if distance_km:
            logistics_cost = distance_km * TRANSPORT_RS_PER_KM_SOLO

        total_amount = material_cost + logistics_cost

        return {
            'material_cost': round(material_cost, 2),
            'logistics_cost': round(logistics_cost, 2),
            'total_amount': round(total_amount, 2),
            'breakdown': {
                'material': material,
                'qty_kg': qty_kg,
                'price_per_kg': prices["byproduct"],
                'distance_km': distance_km,
                'transport_rate_per_km': TRANSPORT_RS_PER_KM_SOLO
            }
        }

# Create global service instance
payment_service = PaymentService()