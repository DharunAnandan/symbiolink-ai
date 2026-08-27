# SymbioLink AI - Advanced Setup Guide

This guide covers the setup and configuration of all advanced features added to SymbioLink AI.

## Prerequisites

- Python 3.8+
- Node.js 14+ (for mobile app)
- PostgreSQL (optional, for production)
- Twilio Account (for WhatsApp integration)
- Razorpay Account (for payment processing)
- Google Maps API Key (for accurate distances)

## Installation Steps

### 1. Backend Setup

```bash
# Navigate to project directory
cd F:\symbiolink

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # On Windows
# source venv/bin/activate  # On Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Copy environment configuration
copy .env.example .env

# Edit .env with your actual credentials
# notepad .env  # On Windows
# nano .env  # On Linux/Mac
```

### 2. Database Setup

```bash
# Run database migration to populate with existing data
python database_migration.py

# This will create:
# - SQLite database (symbiolink_dev.db) for development
# - Or connect to PostgreSQL if configured
# - Populate with units, listings, prices, and trust ratings
# - Create default admin user (admin/admin123)
# - Create unit users (u1, u2, u3.../demo123)
```

### 3. Environment Configuration

Edit the `.env` file with your credentials:

```env
# Flask Configuration
FLASK_ENV=development
SECRET_KEY=your-secret-key-change-in-production

# Database Configuration
DEV_DATABASE_URL=sqlite:///symbiolink_dev.db
# DATABASE_URL=postgresql://username:password@localhost/symbiolink

# Twilio Configuration (WhatsApp)
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=your_twilio_phone_number

# Razorpay Configuration (Payments)
RAZORPAY_KEY_ID=your_razorpay_key_id
RAZORPAY_KEY_SECRET=your_razorpay_key_secret

# Google Maps Configuration
GOOGLE_MAPS_API_KEY=your_google_maps_api_key
```

### 4. Run the Application

```bash
python app.py
```

The application will be available at http://localhost:5000

## Advanced Features Configuration

### WhatsApp Integration

1. **Sign up for Twilio**: https://www.twilio.com/
2. **Get WhatsApp Sandbox credentials**:
   - Go to Twilio Console → Messaging → Try it out → Send a WhatsApp message
   - Note your Account SID, Auth Token, and WhatsApp sandbox number
3. **Configure webhook**:
   - Your webhook URL: `https://your-domain.com/whatsapp/webhook`
   - For local testing, use ngrok: `ngrok http 5000`

### Payment Processing

1. **Sign up for Razorpay**: https://razorpay.com/
2. **Get API keys**:
   - Go to Razorpay Dashboard → Settings → API Keys
   - Generate Key ID and Key Secret
3. **Test mode**:
   - Razorpay provides test mode for development
   - Use test cards: https://razorpay.com/docs/payment-gateway/test-mode/

### Google Maps Integration

1. **Get Google Maps API Key**:
   - Go to Google Cloud Console → APIs & Services → Credentials
   - Create a project and enable Distance Matrix API
   - Generate API key with appropriate restrictions
2. **Configure billing**:
   - Google Maps API requires billing account
   - Free tier includes $200 credit per month

### Mobile App Setup

```bash
# Navigate to mobile directory
cd mobile

# Install dependencies
npm install

# For iOS development (macOS only)
cd ios && pod install && cd ..

# Run development server
npm start

# Run on specific platform
npm run android    # Android
npm run ios        # iOS
npm run web        # Web browser
```

## User Roles and Permissions

### Admin
- Full system access
- User management
- System configuration
- View all data and audit logs

### Unit
- Manage own listings
- Place and manage orders
- View matches and search
- Rate transactions

### Auditor
- Read-only access
- View audit logs
- Access compliance reports
- Export data

## Default Credentials

After running database migration:

- **Admin**: `admin` / `admin123`
- **Units**: `u1`, `u2`, `u3`, etc. / `demo123`

## API Endpoints

### Authentication
- `POST /login` - User login
- `POST /register` - User registration
- `GET /logout` - User logout

### WhatsApp
- `POST /whatsapp/webhook` - Twilio webhook endpoint
- `POST /whatsapp/test-notification/<unit_id>` - Test notification

### Payments
- `POST /payment/create-order` - Create Razorpay order
- `POST /payment/verify` - Verify payment signature
- `POST /payment/refund/<payment_id>` - Process refund

### Admin
- `GET /admin` - Admin dashboard
- `GET /admin/users` - User management
- `GET /admin/audit` - Audit logs

## Testing

### Test WhatsApp Integration
```bash
# Using curl to test webhook
curl -X POST http://localhost:5000/whatsapp/webhook \
  -d "From=whatsapp:+919000000000" \
  -d "Body=I have 100kg metal shavings to give away"
```

### Test Payment Integration
```bash
# Create payment order
curl -X POST http://localhost:5000/payment/create-order \
  -H "Content-Type: application/json" \
  -d '{"material": "metal_shavings", "qty_kg": 100, "distance_km": 2.5}'
```

### Test Google Maps
```python
# Python test script
from maps_service import maps_service

result = maps_service.get_distance((0.0, 0.0), (1.2, 0.4))
print(f"Distance: {result['distance_km']} km")
```

## Troubleshooting

### Database Issues
- **Error**: `no such table: users`
- **Solution**: Run `python database_migration.py`

### WhatsApp Not Working
- **Error**: `Twilio credentials not configured`
- **Solution**: Add Twilio credentials to `.env` file

### Payment Errors
- **Error**: `Razorpay credentials not configured`
- **Solution**: Add Razorpay credentials to `.env` file

### Google Maps Errors
- **Error**: `Google Maps API key not configured`
- **Solution**: Add Google Maps API key to `.env` file

### Mobile App Build Issues
- **Error**: `Failed to install dependencies`
- **Solution**: Delete `node_modules` and run `npm install` again

## Production Deployment

### Backend Deployment
1. Set `FLASK_ENV=production` in `.env`
2. Use PostgreSQL instead of SQLite
3. Configure proper `SECRET_KEY`
4. Set up HTTPS/SSL certificate
5. Use production Twilio/Razorpay credentials
6. Configure proper logging and monitoring

### Mobile App Deployment
1. Update API base URL in `mobile/src/services/api.js`
2. Build for production:
   - Android: `cd android && ./gradlew assembleRelease`
   - iOS: Follow standard React Native iOS build process
3. Submit to app stores

## Security Considerations

1. **Never commit `.env` file** to version control
2. **Use strong secret keys** in production
3. **Enable HTTPS** for all endpoints
4. **Implement rate limiting** for API endpoints
5. **Regular security updates** for dependencies
6. **Backup database** regularly
7. **Monitor logs** for suspicious activity

## Support

For issues or questions:
- Check the main README.md
- Review code comments and docstrings
- Test with demo credentials first
- Enable debug mode for development troubleshooting