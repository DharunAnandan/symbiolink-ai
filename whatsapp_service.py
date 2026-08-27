"""
Real WhatsApp integration service using Twilio.

Handles incoming WhatsApp messages, processes them into listings,
and sends automated responses. Requires Twilio account credentials.
"""

import os
from flask import request, jsonify
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from models import db, WhatsAppMessage, Unit, User
from whatsapp_stub import parse_message
from config import Config
import data

class WhatsAppService:
    # Same placeholder-vs-blank issue as payment_service.py's RAZORPAY_KEY_ID:
    # .env.example ships these as literal placeholder text, which is truthy,
    # so an un-edited .env would build a real (but fake) Twilio Client instead
    # of falling back to simulation mode.
    _PLACEHOLDER_VALUES = {
        'your_twilio_account_sid', 'your_twilio_auth_token', 'your_twilio_phone_number', '',
    }

    def __init__(self):
        self.account_sid = getattr(Config, 'TWILIO_ACCOUNT_SID', None)
        self.auth_token = getattr(Config, 'TWILIO_AUTH_TOKEN', None)
        self.phone_number = getattr(Config, 'TWILIO_PHONE_NUMBER', None)

        if self.account_sid in self._PLACEHOLDER_VALUES:
            self.account_sid = None
        if self.auth_token in self._PLACEHOLDER_VALUES:
            self.auth_token = None
        if self.phone_number in self._PLACEHOLDER_VALUES:
            self.phone_number = None

        if self.account_sid and self.auth_token:
            self.client = Client(self.account_sid, self.auth_token)
        else:
            self.client = None
            print("Warning: Twilio credentials not configured. WhatsApp service will be in simulation mode.")
    
    def receive_message(self):
        """Process incoming WhatsApp message from Twilio webhook."""
        from_number = request.form.get('From', '')
        body = request.form.get('Body', '')
        message_sid = request.form.get('MessageSid', '')
        
        # Log the incoming message
        try:
            log_entry = WhatsAppMessage(
                phone_number=from_number,
                message_body=body,
                direction='inbound',
                processed=False
            )
            db.session.add(log_entry)
            db.session.commit()
        except Exception as e:
            print(f"Error logging WhatsApp message: {e}")
        
        # Find user/unit by phone number
        unit = self._find_unit_by_phone(from_number)

        if not unit:
            response_text = "Welcome to SymbioLink AI! Your phone number is not registered. Please contact support or register via web portal."
            self._send_message(from_number, response_text)
            log_entry.processed = True
            log_entry.error_message = "Unregistered phone number"
            db.session.commit()
            # BUG FIX: MessagingResponse has no .body() method (that's not
            # part of Twilio's TwiML API) -- this raised an unhandled
            # AttributeError, turning a message from any unregistered phone
            # number into a 500 on this public, unauthenticated webhook
            # instead of returning the friendly "not registered" reply.
            # .message() is the correct call, same as the success/failure
            # path a few lines below already uses correctly.
            unregistered_response = MessagingResponse()
            unregistered_response.message(response_text)
            return str(unregistered_response)

        # Parse the message
        result = parse_message(unit['id'], body)
        
        if result['ok']:
            # Create the listing. Goes through data_access.add_listing_to_db rather than
            # creating the Listing row directly here -- data.LISTINGS is a startup snapshot
            # of the database, not a live query, and only add_listing_to_db() knows to
            # mirror a new row into it. Without that, listings created over WhatsApp would
            # commit fine to the database but silently never show up in search/matching/
            # dashboard (all of which read data.LISTINGS) until the app was restarted.
            try:
                import data_access
                data_access.add_listing_to_db(
                    unit_id=unit['id'],
                    listing_type=result['type'],
                    material=result['material'],
                    qty_kg=result['qty_kg'],
                    interval_days=14,
                )

                # The reply text below has always promised "match notifications
                # shortly" -- this is what actually sends them. Same helper
                # /register-unit and the IoT auto-listing path use, so a NEED
                # posted by web form and fulfilled by a WhatsApp-posted WASTE
                # listing (or vice versa) triggers the same bell + email alert
                # regardless of which channel either side used.
                from matching import notify_new_matches_for_unit
                notify_new_matches_for_unit(unit['id'], unit.get('name'))

                response_text = f"✓ Your listing has been created: {result['type'].upper()} {result['qty_kg']}kg of {result['material'].replace('_', ' ')}. You'll receive match notifications shortly!"
                log_entry.listing_created = True
            except Exception as e:
                response_text = f"Error creating listing: {str(e)}. Please try again or contact support."
                log_entry.error_message = str(e)
        else:
            # Ask for missing information
            response_text = f"⚠️ {result['reason']}. Please provide the missing details and try again."
            log_entry.error_message = result['reason']
        
        log_entry.processed = True
        db.session.commit()
        
        # Send response
        self._send_message(from_number, response_text)
        response = MessagingResponse()
        response.message(response_text)
        return str(response)
    
    def _find_unit_by_phone(self, phone_number):
        """Find unit by phone number."""
        # Normalize phone number (remove spaces, dashes, etc.) -- computed
        # outside the try/except below so a malformed/non-string
        # phone_number can't leave `normalized` undefined for the in-memory
        # fallback further down.
        try:
            normalized = ''.join(c for c in (phone_number or "") if c.isdigit())
        except TypeError:
            normalized = ""

        # A missing/blank/non-numeric `From` (a malformed webhook call, a
        # test payload, whatever) used to leave `normalized` empty, and
        # `Unit.phone.like('%%')` matches EVERY unit with a non-null
        # phone -- .first() would silently return an arbitrary unit
        # (whichever sorts first), attributing a stranger's message and
        # any auto-created listing to the wrong company. A handful of
        # digits is the shortest plausible real phone fragment, so
        # require at least that many before querying at all.
        if len(normalized) < 4:
            return None

        try:
            unit = Unit.query.filter(Unit.phone.like(f'%{normalized}%')).first()
            if unit:
                return {
                    "id": unit.id,
                    "name": unit.name,
                    "category": unit.category,
                    "loc": (unit.location_x, unit.location_y),
                    "phone": unit.phone
                }
        except Exception as e:
            print(f"Error finding unit by phone: {e}")

        # BUG FIX: this used to stop here and return None the moment the DB
        # query came up empty, even though data.UNITS (the in-memory layer
        # every other lookup in this app falls back to -- see
        # data_access.py's get_unit_by_id) might still have a matching unit.
        # That happens whenever a unit's phone was never persisted to the
        # Unit DB table (a fresh clone before database_migration.py has run,
        # or this app's own ':memory:' SQLite TestingConfig) -- a real
        # incoming WhatsApp message from a genuinely registered unit would
        # get the "your phone number is not registered" reply instead of
        # being parsed, purely because of where its record happened to live.
        for unit_dict in data.UNITS:
            unit_digits = ''.join(c for c in unit_dict.get("phone", "") if c.isdigit())
            if unit_digits and normalized in unit_digits:
                return unit_dict
        return None
    
    def _send_message(self, to_number, message):
        """Send WhatsApp message."""
        if not self.client:
            print(f"Simulation mode: Would send to {to_number}: {message}")
            return
        
        try:
            message_obj = self.client.messages.create(
                body=message,
                from_=f'whatsapp:{self.phone_number}',
                to=f'whatsapp:{to_number}'
            )
            
            # Log outbound message
            log_entry = WhatsAppMessage(
                phone_number=to_number,
                message_body=message,
                direction='outbound',
                processed=True
            )
            db.session.add(log_entry)
            db.session.commit()
            
            return message_obj.sid
        except Exception as e:
            print(f"Error sending WhatsApp message: {e}")
            return None
    
    def send_match_notification(self, unit_id, match_details):
        """Send match notification to a unit."""
        try:
            # BUG FIX: this used to query `Unit.query.get(unit_id)` directly,
            # bypassing data.unit_by_id()'s DB-then-in-memory fallback that
            # every other lookup in this app relies on (see
            # data_access.get_unit_by_id) -- so a unit that exists in
            # data.UNITS but has no persisted DB row yet (a fresh clone
            # before database_migration.py has run, or this app's own
            # ':memory:' SQLite TestingConfig) silently got zero WhatsApp
            # notifications, with no error, ever. email_service.py already
            # went through data.unit_by_id() correctly; this brings the
            # WhatsApp path in line with it.
            unit = data.unit_by_id(unit_id)
            if not unit or not unit.get("phone"):
                return False

            match_text = f"🎯 New match opportunity!\n\n"
            match_text += f"You have been matched with {match_details['partner_name']} for {match_details['material']}.\n"
            match_text += f"Estimated savings: ₹{match_details['savings']}\n"
            match_text += f"Distance: {match_details['distance']}km\n\n"
            match_text += f"Contact them at: {match_details['partner_phone']}\n\n"
            match_text += f"Reply 'ACCEPT' to proceed or 'DECLINE' to ignore."

            self._send_message(unit["phone"], match_text)
            return True
        except Exception as e:
            print(f"Error sending match notification: {e}")
            return False
    
    def send_order_reminder(self, unit_id, order_details, reason):
        """Nudge one side of an order that's been sitting past this platform's
        stall threshold (see orders.orders_needing_reminder), instead of
        relying on someone happening to notice. `reason` is one of
        "payment_due" / "pickup_due" / "delivery_due" -- the same reasons
        orders_needing_reminder() reports, so app.py can pass them straight
        through without re-deciding the wording here."""
        try:
            # See send_match_notification's comment: data.unit_by_id(), not a
            # raw Unit.query.get(), so this falls back to the in-memory unit
            # list the rest of the app already relies on.
            unit = data.unit_by_id(unit_id)
            if not unit or not unit.get("phone"):
                return False

            material_readable = order_details['material'].replace('_', ' ').title()
            order_ref = f"{order_details['order_number']} ({material_readable}, {order_details['qty_kg']:.0f}kg) with {order_details['partner_name']}"
            reminder_text = {
                "payment_due": f"⏰ Reminder: payment is still pending for order {order_ref}. Please complete payment so the seller can move it forward.",
                "pickup_due": f"⏰ Reminder: order {order_ref} is paid and waiting to be marked picked up.",
                "delivery_due": f"⏰ Reminder: order {order_ref} has been picked up and is waiting to be marked delivered.",
            }
            message = reminder_text.get(reason)
            if not message:
                return False

            self._send_message(unit["phone"], message)
            return True
        except Exception as e:
            print(f"Error sending order reminder: {e}")
            return False

    def send_order_update(self, unit_id, order_details):
        """Send order status update notification."""
        try:
            # See send_match_notification's comment: data.unit_by_id(), not a
            # raw Unit.query.get(), so this falls back to the in-memory unit
            # list the rest of the app already relies on.
            unit = data.unit_by_id(unit_id)
            if not unit or not unit.get("phone"):
                return False

            order_text = f"📦 Order Update: {order_details['order_number']}\n\n"
            order_text += f"Status: {order_details['status']}\n"
            order_text += f"Material: {order_details['material']}\n"
            order_text += f"Quantity: {order_details['qty_kg']}kg\n\n"

            if order_details['status'] == 'completed':
                order_text += f"Please rate your experience with {order_details['partner_name']} (1-5 stars)."

            self._send_message(unit["phone"], order_text)
            return True
        except Exception as e:
            print(f"Error sending order update: {e}")
            return False

# Create global service instance
whatsapp_service = WhatsAppService()